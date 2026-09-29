import asyncio
import hashlib
import json
from contextlib import asynccontextmanager

from .connection import connect
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
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT final_review_html, final_review_json FROM exam_sessions WHERE exam_id = %s",
            (exam_id,),
        )
        row = cursor.fetchone()
    if row is None:
        raise ValueError("EXAM_SESSION_NOT_FOUND")
    if not row[0]:
        return None
    review = json.loads(row[1]) if isinstance(row[1], str) else (row[1] or {})
    return {"exam_id": exam_id, "html": row[0], "review": review}


async def get_final_review(exam_id):
    def read():
        connection = connect(use_database=True)
        try:
            return _read_report(connection, exam_id)
        finally:
            connection.close()
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
            with self.connection.cursor() as cursor:
                cursor.execute(
                    """
                    UPDATE exam_sessions
                    SET final_review_html = %s, final_review_json = %s,
                        exam_score = %s, exam_dimension_scores_json = %s,
                        question_count = %s, exam_completed = %s,
                        ended_at = COALESCE(ended_at, NOW())
                    WHERE exam_id = %s AND final_review_html IS NULL
                    """,
                    (html, to_json(review), scores["total"], to_json(scores["dimensions"]),
                     sum(item["question_count"] for item in review["question_reviews"]),
                     int(review["status"] == "finished"), self.exam_id),
                )
                if cursor.rowcount != 1:
                    raise RuntimeError("FINAL_REVIEW_SAVE_CONFLICT")
            self.connection.commit()
        except Exception:
            self.connection.rollback()
            raise
        self.saved = {"exam_id": self.exam_id, "html": html, "review": review}
        return self.saved


def _claim(exam_id, user_id):
    connection = connect(use_database=True)
    try:
        # A connection-scoped lock prevents duplicate Agent calls across workers.
        lock_name = "final-review:" + hashlib.sha256(exam_id.encode()).hexdigest()[:48]
        with connection.cursor() as cursor:
            cursor.execute("SELECT GET_LOCK(%s, 0)", (lock_name,))
            if cursor.fetchone()[0] != 1:
                raise RuntimeError("FINAL_REVIEW_BUSY")
            cursor.execute("SELECT user_id FROM exam_sessions WHERE exam_id = %s", (exam_id,))
            row = cursor.fetchone()
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
