import asyncio
import secrets
import string
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from .connection import connect, ensure_database
from .exam_repository import create_pending_exam_sessions_for_user
from .schema import ensure_tables
from .serializers import course_row_to_dict, join_request_row_to_dict


async def create_course(
    course_name: str,
    owner_teacher_id: str,
    description: Optional[str] = None,
    invite_code_valid_times: int = 2592000,
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _create_course_sync,
        course_name,
        owner_teacher_id,
        description,
        invite_code_valid_times,
    )


async def list_all_courses() -> List[Dict[str, object]]:
    return await asyncio.to_thread(_list_all_courses_sync)


async def list_teacher_courses(teacher_id: str) -> List[Dict[str, object]]:
    return await asyncio.to_thread(_list_teacher_courses_sync, teacher_id)


async def list_student_courses(student_id: str) -> List[Dict[str, object]]:
    return await asyncio.to_thread(_list_student_courses_sync, student_id)


async def list_available_courses(student_id: Optional[str] = None) -> List[Dict[str, object]]:
    return await asyncio.to_thread(_list_available_courses_sync, student_id)


async def get_course_by_invite_code(invite_code: str) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(_get_course_by_invite_code_sync, invite_code)


async def reset_course_invite_code(
    course_id: str,
    invite_code_valid_times: int,
) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(
        _reset_course_invite_code_sync,
        course_id,
        invite_code_valid_times,
    )


async def request_join_course(course_id: str, user_id: str) -> str:
    return await asyncio.to_thread(_request_join_course_sync, course_id, user_id)


async def list_course_join_requests(
    teacher_id: str,
    course_id: Optional[str] = None,
) -> List[Dict[str, object]]:
    return await asyncio.to_thread(
        _list_course_join_requests_sync,
        teacher_id,
        course_id,
    )


async def approve_course_join_request_by_user(
    course_id: str,
    user_id: str,
    user_role: str,
    reviewer_id: str,
) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(
        _approve_course_join_request_by_user_sync,
        course_id,
        user_id,
        user_role,
        reviewer_id,
    )


async def approve_join_request(request_id: str, teacher_id: str) -> bool:
    return await asyncio.to_thread(_review_join_request_sync, request_id, teacher_id, "approved")


async def reject_join_request(request_id: str, teacher_id: str) -> bool:
    return await asyncio.to_thread(_review_join_request_sync, request_id, teacher_id, "rejected")


async def update_course(
    course_id: str,
    owner_teacher_id: str,
    course_name: Optional[str] = None,
    description: Optional[str] = None,
) -> bool:
    return await asyncio.to_thread(
        _update_course_sync,
        course_id,
        owner_teacher_id,
        course_name,
        description,
    )


async def delete_course(course_id: str, owner_teacher_id: str) -> bool:
    return await asyncio.to_thread(_delete_course_sync, course_id, owner_teacher_id)


def _create_course_sync(
    course_name: str,
    owner_teacher_id: str,
    description: Optional[str],
    invite_code_valid_times: int,
) -> Dict[str, object]:
    ensure_database()
    connection = connect(use_database=True)
    course_id = str(uuid.uuid4())
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    course_name = _normalize_course_name(course_name)
    valid_times = _normalize_invite_code_valid_times(invite_code_valid_times)
    expires_at = (datetime.now() + timedelta(seconds=valid_times)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _raise_if_course_name_exists(cursor, course_name)
            _clear_expired_course_invite_codes(cursor, now)
            invite_code = _generate_unique_course_invite_code(cursor, now)
            cursor.execute(
                """
                INSERT INTO courses (
                    course_id,
                    course_name,
                    description,
                    owner_teacher_id,
                    invite_code,
                    invite_code_expires_at,
                    invite_code_created_at,
                    status,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'active', %s, %s)
                """,
                (
                    course_id,
                    course_name,
                    description,
                    owner_teacher_id,
                    invite_code,
                    expires_at,
                    now,
                    now,
                    now,
                ),
            )
            cursor.execute(
                """
                INSERT INTO course_teachers (
                    course_id,
                    teacher_id,
                    teacher_role,
                    status,
                    created_at
                ) VALUES (%s, %s, 'owner', 'active', %s)
                """,
                (course_id, owner_teacher_id, now),
            )
            cursor.execute(
                """
                SELECT
                    course_id,
                    course_name,
                    description,
                    owner_teacher_id,
                    invite_code,
                    invite_code_expires_at,
                    invite_code_created_at,
                    status,
                    created_at,
                    updated_at
                FROM courses
                WHERE course_id = %s
                """,
                (course_id,),
            )
            created_course = course_row_to_dict(cursor.fetchone())
        connection.commit()
        return created_course
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _list_all_courses_sync() -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    course_id,
                    course_name,
                    description,
                    owner_teacher_id,
                    invite_code,
                    invite_code_expires_at,
                    invite_code_created_at,
                    status,
                    created_at,
                    updated_at
                FROM courses
                WHERE status = 'active'
                ORDER BY created_at DESC
                """
            )
            return [course_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _list_teacher_courses_sync(teacher_id: str) -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.course_id,
                    c.course_name,
                    c.description,
                    c.owner_teacher_id,
                    c.invite_code,
                    c.invite_code_expires_at,
                    c.invite_code_created_at,
                    c.status,
                    c.created_at,
                    c.updated_at
                FROM courses c
                JOIN course_teachers t ON c.course_id = t.course_id
                WHERE t.teacher_id = %s
                  AND t.status = 'active'
                  AND c.status = 'active'
                ORDER BY c.created_at DESC
                """,
                (teacher_id,),
            )
            return [course_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _list_student_courses_sync(student_id: str) -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.course_id,
                    c.course_name,
                    c.description,
                    c.owner_teacher_id,
                    c.invite_code,
                    c.invite_code_expires_at,
                    c.invite_code_created_at,
                    c.status,
                    c.created_at,
                    c.updated_at
                FROM courses c
                JOIN course_students s ON c.course_id = s.course_id
                WHERE s.student_id = %s
                  AND s.status = 'active'
                  AND c.status = 'active'
                ORDER BY c.created_at DESC
                """,
                (student_id,),
            )
            return [course_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _list_available_courses_sync(student_id: Optional[str]) -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        params: tuple = ()
        exclude_joined = ""
        if student_id:
            exclude_joined = """
                AND NOT EXISTS (
                    SELECT 1
                    FROM course_students s
                    WHERE s.course_id = c.course_id
                      AND s.student_id = %s
                      AND s.status = 'active'
                )
            """
            params = (student_id,)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    c.course_id,
                    c.course_name,
                    c.description,
                    c.owner_teacher_id,
                    c.invite_code,
                    c.invite_code_expires_at,
                    c.invite_code_created_at,
                    c.status,
                    c.created_at,
                    c.updated_at
                FROM courses c
                WHERE c.status = 'active'
                {exclude_joined}
                ORDER BY c.created_at DESC
                """,
                params,
            )
            return [course_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _get_course_by_invite_code_sync(invite_code: str) -> Optional[Dict[str, object]]:
    invite_code = str(invite_code or "").strip()
    if not invite_code:
        return None
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT
                    c.course_id,
                    c.course_name,
                    c.description,
                    c.owner_teacher_id,
                    c.invite_code,
                    c.invite_code_expires_at,
                    c.invite_code_created_at,
                    c.status,
                    c.created_at,
                    c.updated_at
                FROM courses c
                WHERE c.invite_code = %s
                  AND c.invite_code_expires_at > %s
                  AND c.status = 'active'
                LIMIT 1
                """,
                (invite_code, now),
            )
            row = cursor.fetchone()
            return course_row_to_dict(row) if row else None
    finally:
        connection.close()


def _reset_course_invite_code_sync(
    course_id: str,
    invite_code_valid_times: int,
) -> Optional[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    valid_times = _normalize_invite_code_valid_times(invite_code_valid_times)
    expires_at = (datetime.now() + timedelta(seconds=valid_times)).strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _clear_expired_course_invite_codes(cursor, now)
            invite_code = _generate_unique_course_invite_code(cursor, now)
            cursor.execute(
                """
                UPDATE courses
                SET invite_code = %s,
                    invite_code_expires_at = %s,
                    invite_code_created_at = %s,
                    updated_at = %s
                WHERE course_id = %s
                  AND status = 'active'
                """,
                (invite_code, expires_at, now, now, course_id),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                return None
            cursor.execute(
                """
                SELECT
                    course_id,
                    course_name,
                    description,
                    owner_teacher_id,
                    invite_code,
                    invite_code_expires_at,
                    invite_code_created_at,
                    status,
                    created_at,
                    updated_at
                FROM courses
                WHERE course_id = %s
                """,
                (course_id,),
            )
            updated_course = course_row_to_dict(cursor.fetchone())
        connection.commit()
        return updated_course
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _update_course_sync(
    course_id: str,
    owner_teacher_id: str,
    course_name: Optional[str],
    description: Optional[str],
) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        set_clauses = []
        values = []
        with connection.cursor() as cursor:
            if course_name is not None:
                course_name = _normalize_course_name(course_name)
                _raise_if_course_name_exists(cursor, course_name, exclude_course_id=course_id)
                set_clauses.append("course_name = %s")
                values.append(course_name)
            if description is not None:
                set_clauses.append("description = %s")
                values.append(description)
            if not set_clauses:
                return True
            set_clauses.append("updated_at = %s")
            values.append(now)
            values.extend([course_id, owner_teacher_id])
            cursor.execute(
                f"""
                UPDATE courses
                SET {', '.join(set_clauses)}
                WHERE course_id = %s
                  AND owner_teacher_id = %s
                  AND status = 'active'
                """,
                values,
            )
            updated = cursor.rowcount > 0
        connection.commit()
        return updated
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _delete_course_sync(course_id: str, owner_teacher_id: str) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                UPDATE courses
                SET status = 'deleted',
                    updated_at = %s
                WHERE course_id = %s
                  AND owner_teacher_id = %s
                  AND status = 'active'
                """,
                (now, course_id, owner_teacher_id),
            )
            deleted = cursor.rowcount > 0
        connection.commit()
        return deleted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _request_join_course_sync(course_id: str, user_id: str) -> str:
    ensure_database()
    connection = connect(use_database=True)
    request_id = str(uuid.uuid4())
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM courses
                WHERE course_id = %s
                  AND status = 'active'
                LIMIT 1
                """,
                (course_id,),
            )
            if cursor.fetchone() is None:
                raise ValueError("COURSE_NOT_FOUND")

            _raise_if_active_course_member_exists(cursor, course_id, user_id)

            cursor.execute(
                """
                SELECT request_id
                FROM course_join_requests
                WHERE course_id = %s
                  AND user_id = %s
                  AND status = 'pending'
                LIMIT 1
                """,
                (course_id, user_id),
            )
            row = cursor.fetchone()
            if row is not None:
                return row[0]

            cursor.execute(
                """
                INSERT INTO course_join_requests (
                    request_id,
                    course_id,
                    user_id,
                    status,
                    requested_at
                ) VALUES (%s, %s, %s, 'pending', %s)
                """,
                (request_id, course_id, user_id, now),
            )
        connection.commit()
        return request_id
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _raise_if_active_course_member_exists(cursor, course_id: str, user_id: str) -> None:
    cursor.execute(
        """
        SELECT 1
        FROM course_students
        WHERE course_id = %s
          AND student_id = %s
          AND status = 'active'
        UNION ALL
        SELECT 1
        FROM course_teachers
        WHERE course_id = %s
          AND teacher_id = %s
          AND status = 'active'
        LIMIT 1
        """,
        (course_id, user_id, course_id, user_id),
    )
    if cursor.fetchone() is not None:
        raise ValueError("COURSE_ALREADY_JOINED")


def _normalize_course_name(course_name: str) -> str:
    course_name = (course_name or "").strip()
    if not course_name:
        raise ValueError("COURSE_NAME_REQUIRED")
    return course_name


def _normalize_invite_code_valid_times(invite_code_valid_times: int) -> int:
    try:
        valid_times = int(invite_code_valid_times)
    except (TypeError, ValueError) as exc:
        raise ValueError("INVITE_CODE_VALID_TIMES_INVALID") from exc
    if valid_times <= 0 or valid_times > 2592000:
        raise ValueError("INVITE_CODE_VALID_TIMES_INVALID")
    return valid_times


def _clear_expired_course_invite_codes(cursor, now: str) -> None:
    cursor.execute(
        """
        UPDATE courses
        SET invite_code = NULL,
            invite_code_expires_at = NULL,
            invite_code_created_at = NULL
        WHERE invite_code IS NOT NULL
          AND (invite_code_expires_at IS NULL OR invite_code_expires_at <= %s)
        """,
        (now,),
    )


def _generate_unique_course_invite_code(cursor, now: str) -> str:
    characters = string.ascii_letters + string.digits
    for _ in range(20):
        invite_code = "".join(secrets.choice(characters) for _ in range(5))
        cursor.execute(
            """
            SELECT 1
            FROM courses
            WHERE invite_code = %s
              AND status = 'active'
              AND invite_code_expires_at > %s
            LIMIT 1
            """,
            (invite_code, now),
        )
        if cursor.fetchone() is None:
            return invite_code
    raise RuntimeError("Unable to generate a unique course invite code")


def _raise_if_course_name_exists(cursor, course_name: str, exclude_course_id: Optional[str] = None) -> None:
    if exclude_course_id:
        cursor.execute(
            """
            SELECT 1
            FROM courses
            WHERE course_name = %s
              AND course_id <> %s
            LIMIT 1
            """,
            (course_name, exclude_course_id),
        )
    else:
        cursor.execute(
            """
            SELECT 1
            FROM courses
            WHERE course_name = %s
            LIMIT 1
            """,
            (course_name,),
        )
    if cursor.fetchone() is not None:
        raise ValueError("COURSE_NAME_EXISTS")


def _list_course_join_requests_sync(
    teacher_id: str,
    course_id: Optional[str],
) -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        course_filter = ""
        params: tuple = (teacher_id,)
        if course_id:
            course_filter = "AND r.course_id = %s"
            params = (teacher_id, course_id)
        with connection.cursor() as cursor:
            cursor.execute(
                f"""
                SELECT
                    r.request_id,
                    r.course_id,
                    c.course_name,
                    r.user_id,
                    r.status,
                    r.requested_at,
                    r.reviewed_at,
                    r.reviewed_by
                FROM course_join_requests r
                JOIN courses c ON r.course_id = c.course_id
                JOIN course_teachers t ON r.course_id = t.course_id
                WHERE t.teacher_id = %s
                  AND t.status = 'active'
                  {course_filter}
                ORDER BY r.requested_at DESC
                """,
                params,
            )
            return [join_request_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _approve_course_join_request_by_user_sync(
    course_id: str,
    user_id: str,
    user_role: str,
    reviewer_id: str,
) -> Optional[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.request_id
                FROM course_join_requests r
                JOIN courses c ON r.course_id = c.course_id
                WHERE r.course_id = %s
                  AND r.user_id = %s
                  AND r.status = 'pending'
                  AND c.status = 'active'
                LIMIT 1
                FOR UPDATE
                """,
                (course_id, user_id),
            )
            row = cursor.fetchone()
            if row is None:
                return None
            request_id = row[0]

            _raise_if_active_course_member_exists(cursor, course_id, user_id)

            if user_role == "teacher":
                cursor.execute(
                    """
                    INSERT INTO course_teachers (
                        course_id,
                        teacher_id,
                        teacher_role,
                        status,
                        created_at
                    ) VALUES (%s, %s, 'teacher', 'active', %s)
                    ON DUPLICATE KEY UPDATE
                        teacher_role = IF(teacher_role = 'owner', teacher_role, VALUES(teacher_role)),
                        status = 'active',
                        created_at = VALUES(created_at)
                    """,
                    (course_id, user_id, now),
                )
            elif user_role == "student":
                cursor.execute(
                    """
                    INSERT INTO course_students (
                        course_id,
                        student_id,
                        status,
                        joined_at
                    ) VALUES (%s, %s, 'active', %s)
                    ON DUPLICATE KEY UPDATE
                        status = 'active',
                        joined_at = VALUES(joined_at)
                    """,
                    (course_id, user_id, now),
                )
            else:
                raise ValueError("USER_ROLE_UNSUPPORTED")
            create_pending_exam_sessions_for_user(connection, course_id, user_id)

            cursor.execute(
                """
                UPDATE course_join_requests
                SET status = 'approved',
                    reviewed_at = %s,
                    reviewed_by = %s
                WHERE request_id = %s
                """,
                (now, reviewer_id, request_id),
            )
        connection.commit()
        return {
            "request_id": request_id,
            "course_id": course_id,
            "user_id": user_id,
            "role": user_role,
        }
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _review_join_request_sync(request_id: str, teacher_id: str, target_status: str) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT r.course_id, r.user_id
                FROM course_join_requests r
                JOIN course_teachers t ON r.course_id = t.course_id
                WHERE r.request_id = %s
                  AND r.status = 'pending'
                  AND t.teacher_id = %s
                  AND t.status = 'active'
                LIMIT 1
                """,
                (request_id, teacher_id),
            )
            row = cursor.fetchone()
            if row is None:
                return False
            course_id, user_id = row

            cursor.execute(
                """
                UPDATE course_join_requests
                SET status = %s,
                    reviewed_at = %s,
                    reviewed_by = %s
                WHERE request_id = %s
                """,
                (target_status, now, teacher_id, request_id),
            )
            if target_status == "approved":
                cursor.execute(
                    """
                    INSERT INTO course_students (
                        course_id,
                        student_id,
                        status,
                        joined_at
                    ) VALUES (%s, %s, 'active', %s)
                    ON DUPLICATE KEY UPDATE
                        status = 'active',
                        joined_at = VALUES(joined_at)
                    """,
                    (course_id, user_id, now),
                )
                create_pending_exam_sessions_for_user(connection, course_id, user_id)
        connection.commit()
        return True
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()
