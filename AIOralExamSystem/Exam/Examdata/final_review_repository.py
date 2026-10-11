import asyncio
import hashlib
import json
from contextlib import asynccontextmanager

from sqlalchemy import func, select, text, update

from .connection import get_engine
from .models import ExamSession
from .serializers import to_json


async def _database_call(function, *args):
    task = asyncio.create_task(asyncio.to_thread(function, *args))
    try:
        return await asyncio.shield(task)
    except asyncio.CancelledError:
        # A cancelled await must not close a connection while its worker is using it.
        await task
        raise


def _read_report(connection, exam_id):
    row = connection.execute(
        select(ExamSession.final_review_html, ExamSession.final_review_json)
        .where(ExamSession.exam_id == exam_id)
    ).fetchone()
    if row is None:
        raise ValueError("EXAM_SESSION_NOT_FOUND")
    if not row[0]:
        return None
    review = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
    return {"exam_id": exam_id, "html": row[0], "review": review}


async def get_final_review(exam_id):
    def read():
        with get_engine().connect() as connection:
            return _read_report(connection, exam_id)
    return await asyncio.to_thread(read)


class FinalReviewWriter:
    def __init__(self, connection, exam_id, saved):
        self.connection = connection
        self.exam_id = exam_id
        self.saved = saved

    async def save(self, review, html):
        return await _database_call(self._save, review, html)

    def _save(self, review, html):
        scores = review["scores"]
        try:
            result = self.connection.execute(
                update(ExamSession)
                .where(
                    ExamSession.exam_id == self.exam_id,
                    ExamSession.final_review_html.is_(None),
                )
                .values(
                    final_review_html=html,
                    final_review_json=to_json(review),
                    total_score=scores["max_total"],
                    dimension_scores_json=to_json(scores["max_dimensions"]),
                    exam_score=scores["total"],
                    exam_dimension_scores_json=to_json(scores["dimensions"]),
                    question_count=sum(
                        len(item.get("question_chain") or [])
                        for item in review["question_reviews"]
                    ),
                    exam_completed=int(review["status"] == "finished"),
                    ended_at=func.coalesce(ExamSession.ended_at, func.now()),
                )
            )
            if result.rowcount != 1:
                raise RuntimeError("FINAL_REVIEW_SAVE_CONFLICT")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self.saved = {"exam_id": self.exam_id, "html": html, "review": review}
        return self.saved


def _claim(exam_id, user_id):
    connection = get_engine().connect()
    try:
        # A connection-scoped lock prevents duplicate Agent calls across workers.
        lock_name = "final-review:" + hashlib.sha256(exam_id.encode()).hexdigest()[:48]
        lock_result = connection.execute(
            text("SELECT GET_LOCK(:lock_name, 0)"), {"lock_name": lock_name}
        ).fetchone()
        if lock_result[0] != 1:
            raise RuntimeError("FINAL_REVIEW_BUSY")
        row = connection.execute(
            select(ExamSession.user_id).where(ExamSession.exam_id == exam_id)
        ).fetchone()
        if row is None:
            raise ValueError("EXAM_SESSION_NOT_FOUND")
        if str(row[0]) != str(user_id):
            raise PermissionError("FINAL_REVIEW_ACCESS_DENIED")
        saved = _read_report(connection, exam_id)
        connection.commit()
        return FinalReviewWriter(connection, exam_id, saved)
    except BaseException:
        connection.close()
        raise


@asynccontextmanager
async def claim_final_review(exam_id, user_id):
    if not exam_id or not user_id:
        raise ValueError("EXAM_SESSION_CONTEXT_INCOMPLETE")
    claim_task = asyncio.create_task(asyncio.to_thread(_claim, exam_id, user_id))
    try:
        writer = await asyncio.shield(claim_task)
    except asyncio.CancelledError:
        writer = await claim_task
        await _database_call(writer.connection.close)
        raise
    try:
        yield writer
    finally:
        # Closing releases GET_LOCK even if generation or persistence failed.
        await _database_call(writer.connection.close)
