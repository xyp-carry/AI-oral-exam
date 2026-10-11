"""Snapshot C-mode scoring limits when an exam starts."""

import asyncio

from sqlalchemy import select, update

from .connection import get_engine
from .models import ExamSession
from .schema import ensure_tables
from .serializers import to_json


def _initialize_c_score_limits_sync(exam_id, user_id, configured_turns, root_count):
    if root_count <= 0:
        raise ValueError("C_MODE_ROOT_QUESTIONS_REQUIRED")
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(
                ExamSession.user_id,
                ExamSession.c_max_probe_turns,
                ExamSession.final_review_html,
            )
            .where(ExamSession.exam_id == exam_id)
            .with_for_update()
        ).fetchone()
        if row is None:
            raise ValueError("EXAM_SESSION_NOT_FOUND")
        if str(row[0]) != str(user_id):
            raise PermissionError("C_EXAM_SESSION_SCOPE_INVALID")
        if row[2]:
            raise ValueError("C_EXAM_ALREADY_REVIEWED")
        turns = int(row[1]) if row[1] is not None else configured_turns
        if not 1 <= turns <= 20:
            raise ValueError("C_MAX_PROBE_TURNS_INVALID")
        maximums = {f"根题 {index}": 4 * turns for index in range(1, root_count + 1)}
        connection.execute(
            update(ExamSession)
            .where(ExamSession.exam_id == exam_id)
            .values(
                c_max_probe_turns=turns,
                total_score=sum(maximums.values()),
                dimension_count=root_count,
                dimension_scores_json=to_json(maximums),
            )
        )
    return turns


async def initialize_c_score_limits(exam_id, user_id, configured_turns, root_count):
    return await asyncio.to_thread(
        _initialize_c_score_limits_sync, exam_id, user_id, configured_turns, root_count,
    )
