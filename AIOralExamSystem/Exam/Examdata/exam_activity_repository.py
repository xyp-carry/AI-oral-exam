import asyncio
import uuid
from datetime import datetime, timedelta
from typing import Callable

from .connection import connect, ensure_database
from .exam_lifecycle_repository import _load_state, _readiness
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
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                "SELECT exam_item_id FROM exam_sessions WHERE exam_id = %s",
                (exam_id,),
            )
            row = cursor.fetchone()
            if not row or not row[0]:
                raise ValueError("EXAM_SESSION_NOT_FOUND")
            exam_item_id = str(row[0])
            cursor.execute(
                """
                SELECT course_id, status, exam_available_from, exam_available_until
                FROM course_exam_items WHERE exam_item_id = %s FOR UPDATE
                """,
                (exam_item_id,),
            )
            item = cursor.fetchone()
            now = datetime.now()
            if (
                not item or item[1] != "active"
                or not item[2] or not item[3]
                or not item[2] <= now <= item[3]
            ):
                raise ValueError("EXAM_UNAVAILABLE")
            state = _load_state(cursor, str(item[0]), exam_item_id)
            if state is None or not _readiness(cursor, state, state["created_by"])["ready"]:
                raise ValueError("EXAM_UNAVAILABLE")
            cursor.execute(
                """
                SELECT exam_item_id, user_id, exam_completed,
                       exam_active_token, exam_active_until,
                       exam_pipeline_id, exam_pipeline_owner
                FROM exam_sessions WHERE exam_id = %s FOR UPDATE
                """,
                (exam_id,),
            )
            session = cursor.fetchone()
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
            cursor.execute(
                """
                UPDATE exam_sessions
                SET exam_active_token = %s, exam_active_until = %s,
                    exam_pipeline_id = %s, exam_pipeline_owner = %s
                WHERE exam_id = %s
                """,
                (
                    token,
                    now + timedelta(seconds=EXAM_LEASE_SECONDS),
                    pipeline_id,
                    pipeline_owner,
                    exam_id,
                ),
            )
        connection.commit()
        return token
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _renew_exam_session_sync(
    exam_id: str, token: str, pipeline_id: str, pipeline_owner: str
) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE exam_sessions AS s
                JOIN course_exam_items AS i ON i.exam_item_id = s.exam_item_id
                SET s.exam_active_until = %s
                WHERE s.exam_id = %s AND s.exam_active_token = %s
                  AND s.exam_pipeline_id = %s AND s.exam_pipeline_owner = %s
                  AND s.exam_completed = 0 AND i.status = 'active'
                """,
                (
                    datetime.now() + timedelta(seconds=EXAM_LEASE_SECONDS),
                    exam_id,
                    token,
                    pipeline_id,
                    pipeline_owner,
                ),
            )
            renewed = cursor.rowcount == 1
        connection.commit()
        return renewed
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _end_exam_session_sync(
    exam_id: str, token: str, pipeline_id: str, pipeline_owner: str
) -> None:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE exam_sessions
                SET exam_active_token = NULL, exam_active_until = NULL,
                    exam_pipeline_id = NULL, exam_pipeline_owner = NULL
                WHERE exam_id = %s AND exam_active_token = %s
                  AND exam_pipeline_id = %s AND exam_pipeline_owner = %s
                """,
                (exam_id, token, pipeline_id, pipeline_owner),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
