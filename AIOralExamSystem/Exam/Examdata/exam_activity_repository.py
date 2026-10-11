import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Callable

from sqlalchemy import select, update

from .connection import ensure_database, get_engine
from .exam_lifecycle_repository import _load_state, _readiness
from .models import CourseExamItem, ExamSession
from .schema import ensure_tables

EXAM_LEASE_SECONDS = 300


async def begin_exam_session(
    exam_id: str,
    user_id: str,
    pipeline_id: str,
    pipeline_owner: str,
    is_local_pipeline_active: Callable[[str], bool],
) -> str:
    return await asyncio.to_thread(
        _begin_exam_session_sync,
        exam_id,
        user_id,
        pipeline_id,
        pipeline_owner,
        is_local_pipeline_active,
    )


async def renew_exam_session(
    exam_id: str, token: str, pipeline_id: str, pipeline_owner: str
) -> bool:
    return await asyncio.to_thread(
        _renew_exam_session_sync, exam_id, token, pipeline_id, pipeline_owner
    )


async def end_exam_session(
    exam_id: str, token: str, pipeline_id: str, pipeline_owner: str
) -> None:
    await asyncio.to_thread(
        _end_exam_session_sync, exam_id, token, pipeline_id, pipeline_owner
    )


def _binding_blocks_start(
    active_token: str | None,
    active_until: datetime | None,
    existing_pipeline_id: str | None,
    existing_owner: str | None,
    current_owner: str,
    now: datetime,
    is_local_pipeline_active: Callable[[str], bool],
) -> bool:
    if not active_token:
        return False
    if existing_pipeline_id and existing_owner == current_owner:
        return is_local_pipeline_active(existing_pipeline_id)
    # Another process cannot be inspected through this process's registry.
    # Its lease remains authoritative until the heartbeat stops renewing it.
    return active_until is not None and active_until > now


def _begin_exam_session_sync(
    exam_id: str,
    user_id: str,
    pipeline_id: str,
    pipeline_owner: str,
    is_local_pipeline_active: Callable[[str], bool],
) -> str:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(ExamSession.exam_item_id).where(ExamSession.exam_id == exam_id)
        ).fetchone()
        if not row or not row[0]:
            raise ValueError("EXAM_SESSION_NOT_FOUND")
        exam_item_id = str(row[0])
        item = connection.execute(
            select(
                CourseExamItem.course_id,
                CourseExamItem.status,
                CourseExamItem.exam_available_from,
                CourseExamItem.exam_available_until,
            )
            .where(CourseExamItem.exam_item_id == exam_item_id)
            .with_for_update()
        ).fetchone()
        now = datetime.now()
        if (
            not item or item[1] != "active"
            or not item[2] or not item[3]
            or not item[2] <= now <= item[3]
        ):
            raise ValueError("EXAM_UNAVAILABLE")
        state = _load_state(connection, str(item[0]), exam_item_id)
        if state is None or not _readiness(connection, state, state["created_by"])["ready"]:
            raise ValueError("EXAM_UNAVAILABLE")
        session = connection.execute(
            select(
                ExamSession.exam_item_id,
                ExamSession.user_id,
                ExamSession.exam_completed,
                ExamSession.exam_active_token,
                ExamSession.exam_active_until,
                ExamSession.exam_pipeline_id,
                ExamSession.exam_pipeline_owner,
            )
            .where(ExamSession.exam_id == exam_id)
            .with_for_update()
        ).fetchone()
        if not session or str(session[0]) != exam_item_id or str(session[1]) != str(user_id):
            raise ValueError("EXAM_SESSION_NOT_FOUND")
        if session[2]:
            raise ValueError("EXAM_SESSION_COMPLETED")
        if _binding_blocks_start(
            session[3], session[4], session[5], session[6],
            pipeline_owner, now, is_local_pipeline_active,
        ):
            raise ValueError("EXAM_IN_PROGRESS")
        token = str(uuid.uuid4())
        connection.execute(
            update(ExamSession)
            .where(ExamSession.exam_id == exam_id)
            .values(
                exam_active_token=token,
                exam_active_until=now + timedelta(seconds=EXAM_LEASE_SECONDS),
                exam_pipeline_id=pipeline_id,
                exam_pipeline_owner=pipeline_owner,
            )
        )
    return token


def _renew_exam_session_sync(
    exam_id: str, token: str, pipeline_id: str, pipeline_owner: str
) -> bool:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        # 等价于 UPDATE exam_sessions JOIN course_exam_items：仅当所属考试项
        # 仍为 active 时续租（IN 子查询不涉及被更新的表，MySQL 允许）。
        result = connection.execute(
            update(ExamSession)
            .where(
                ExamSession.exam_id == exam_id,
                ExamSession.exam_active_token == token,
                ExamSession.exam_pipeline_id == pipeline_id,
                ExamSession.exam_pipeline_owner == pipeline_owner,
                ExamSession.exam_completed == 0,
                ExamSession.exam_item_id.in_(
                    select(CourseExamItem.exam_item_id).where(
                        CourseExamItem.status == "active"
                    )
                ),
            )
            .values(
                exam_active_until=datetime.now() + timedelta(seconds=EXAM_LEASE_SECONDS)
            )
        )
        renewed = result.rowcount == 1
    return renewed


def _end_exam_session_sync(
    exam_id: str, token: str, pipeline_id: str, pipeline_owner: str
) -> None:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        connection.execute(
            update(ExamSession)
            .where(
                ExamSession.exam_id == exam_id,
                ExamSession.exam_active_token == token,
                ExamSession.exam_pipeline_id == pipeline_id,
                ExamSession.exam_pipeline_owner == pipeline_owner,
            )
            .values(
                exam_active_token=None,
                exam_active_until=None,
                exam_pipeline_id=None,
                exam_pipeline_owner=None,
            )
        )
