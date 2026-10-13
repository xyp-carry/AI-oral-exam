import asyncio
import secrets
import string
import uuid
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy import exists, func, literal, select, text, update
from sqlalchemy.dialects.mysql import insert as mysql_insert

from mysql_config import get_mysql_config

from .connection import ensure_database, get_engine, mysql_error_code, quote_identifier
from .exam_repository import create_pending_exam_sessions_for_user
from .models import Course, CourseJoinRequest, CourseStudent, CourseTeacher
from .schema import ensure_tables
from .serializers import course_row_to_dict, join_request_row_to_dict

_AUTH_USERS_TABLE = (
    quote_identifier(get_mysql_config(
        database_env="MYSQL_AUTH_DATABASE", default_database="ai_oral_exam_user"
    )["database"])
    + ".`users`"
)

_COURSE_COLUMNS = (
    Course.course_id,
    Course.course_name,
    Course.description,
    Course.owner_teacher_id,
    Course.invite_code,
    Course.invite_code_expires_at,
    Course.invite_code_created_at,
    Course.status,
    Course.created_at,
    Course.updated_at,
)


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
    course_id = str(uuid.uuid4())
    now = datetime.now()
    course_name = _normalize_course_name(course_name)
    valid_times = _normalize_invite_code_valid_times(invite_code_valid_times)
    expires_at = datetime.now() + timedelta(seconds=valid_times)
    try:
        with get_engine().begin() as connection:
            ensure_tables(connection)
            _raise_if_course_name_exists(connection, course_name, owner_teacher_id)
            _clear_expired_course_invite_codes(connection, now)
            invite_code = _generate_unique_course_invite_code(connection, now)
            connection.execute(
                Course.__table__.insert().values(
                    course_id=course_id,
                    course_name=course_name,
                    description=description,
                    owner_teacher_id=owner_teacher_id,
                    invite_code=invite_code,
                    invite_code_expires_at=expires_at,
                    invite_code_created_at=now,
                    status="active",
                    created_at=now,
                    updated_at=now,
                )
            )
            connection.execute(
                CourseTeacher.__table__.insert().values(
                    course_id=course_id,
                    teacher_id=owner_teacher_id,
                    teacher_role="owner",
                    status="active",
                    created_at=now,
                )
            )
            created_row = connection.execute(
                select(*_COURSE_COLUMNS).where(Course.course_id == course_id)
            ).fetchone()
            created_course = course_row_to_dict(created_row)
        return created_course
    except Exception as error:
        _raise_if_course_name_unique_conflict(error)
        raise


def _list_all_courses_sync() -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        rows = connection.execute(
            select(*_COURSE_COLUMNS)
            .where(Course.status == "active")
            .order_by(Course.created_at.desc())
        ).fetchall()
        return [course_row_to_dict(row) for row in rows]


def _list_teacher_courses_sync(teacher_id: str) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        rows = connection.execute(
            select(*_COURSE_COLUMNS)
            .join(
                CourseTeacher,
                Course.course_id == CourseTeacher.course_id,
            )
            .where(
                CourseTeacher.teacher_id == teacher_id,
                CourseTeacher.status == "active",
                Course.status == "active",
            )
            .order_by(Course.created_at.desc())
        ).fetchall()
        return [course_row_to_dict(row) for row in rows]


def _list_student_courses_sync(student_id: str) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        rows = connection.execute(
            select(*_COURSE_COLUMNS)
            .join(
                CourseStudent,
                Course.course_id == CourseStudent.course_id,
            )
            .where(
                CourseStudent.student_id == student_id,
                CourseStudent.status == "active",
                Course.status == "active",
            )
            .order_by(Course.created_at.desc())
        ).fetchall()
        return [course_row_to_dict(row) for row in rows]


def _list_available_courses_sync(student_id: Optional[str]) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        statement = select(*_COURSE_COLUMNS).where(Course.status == "active")
        if student_id:
            statement = statement.where(
                ~exists(
                    select(literal(1)).where(
                        CourseStudent.course_id == Course.course_id,
                        CourseStudent.student_id == student_id,
                        CourseStudent.status == "active",
                    )
                )
            )
        rows = connection.execute(
            statement.order_by(Course.created_at.desc())
        ).fetchall()
        return [course_row_to_dict(row) for row in rows]


def _get_course_by_invite_code_sync(invite_code: str) -> Optional[Dict[str, object]]:
    invite_code = str(invite_code or "").strip()
    if not invite_code:
        return None
    ensure_database()
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(*_COURSE_COLUMNS)
            .where(
                Course.invite_code == invite_code,
                Course.invite_code_expires_at > now,
                Course.status == "active",
            )
            .limit(1)
        ).fetchone()
        return course_row_to_dict(row) if row else None


def _reset_course_invite_code_sync(
    course_id: str,
    invite_code_valid_times: int,
) -> Optional[Dict[str, object]]:
    ensure_database()
    now = datetime.now()
    valid_times = _normalize_invite_code_valid_times(invite_code_valid_times)
    expires_at = datetime.now() + timedelta(seconds=valid_times)
    with get_engine().begin() as connection:
        ensure_tables(connection)
        _clear_expired_course_invite_codes(connection, now)
        invite_code = _generate_unique_course_invite_code(connection, now)
        result = connection.execute(
            update(Course)
            .where(
                Course.course_id == course_id,
                Course.status == "active",
            )
            .values(
                invite_code=invite_code,
                invite_code_expires_at=expires_at,
                invite_code_created_at=now,
                updated_at=now,
            )
        )
        if result.rowcount == 0:
            connection.rollback()
            return None
        updated_row = connection.execute(
            select(*_COURSE_COLUMNS).where(Course.course_id == course_id)
        ).fetchone()
        updated_course = course_row_to_dict(updated_row)
    return updated_course


def _update_course_sync(
    course_id: str,
    owner_teacher_id: str,
    course_name: Optional[str],
    description: Optional[str],
) -> bool:
    ensure_database()
    now = datetime.now()
    try:
        with get_engine().begin() as connection:
            ensure_tables(connection)
            values: Dict[str, object] = {}
            if course_name is not None:
                course_name = _normalize_course_name(course_name)
                _raise_if_course_name_exists(
                    connection, course_name, owner_teacher_id, exclude_course_id=course_id
                )
                values["course_name"] = course_name
            if description is not None:
                values["description"] = description
            if not values:
                return True
            values["updated_at"] = now
            result = connection.execute(
                update(Course)
                .where(
                    Course.course_id == course_id,
                    Course.owner_teacher_id == owner_teacher_id,
                    Course.status == "active",
                )
                .values(**values)
            )
            updated = result.rowcount > 0
        return updated
    except Exception as error:
        _raise_if_course_name_unique_conflict(error)
        raise


def _delete_course_sync(course_id: str, owner_teacher_id: str) -> bool:
    ensure_database()
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        result = connection.execute(
            update(Course)
            .where(
                Course.course_id == course_id,
                Course.owner_teacher_id == owner_teacher_id,
                Course.status == "active",
            )
            .values(status="deleted", updated_at=now)
        )
        deleted = result.rowcount > 0
    return deleted


def _request_join_course_sync(course_id: str, user_id: str) -> str:
    ensure_database()
    request_id = str(uuid.uuid4())
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        course_row = connection.execute(
            select(Course.course_id)
            .where(
                Course.course_id == course_id,
                Course.status == "active",
            )
            .limit(1)
        ).fetchone()
        if course_row is None:
            raise ValueError("COURSE_NOT_FOUND")

        _raise_if_active_course_member_exists(connection, course_id, user_id)

        pending_row = connection.execute(
            select(CourseJoinRequest.request_id)
            .where(
                CourseJoinRequest.course_id == course_id,
                CourseJoinRequest.user_id == user_id,
                CourseJoinRequest.status == "pending",
            )
            .limit(1)
        ).fetchone()
        if pending_row is not None:
            return pending_row[0]

        connection.execute(
            CourseJoinRequest.__table__.insert().values(
                request_id=request_id,
                course_id=course_id,
                user_id=user_id,
                status="pending",
                requested_at=now,
            )
        )
    return request_id


def _raise_if_active_course_member_exists(connection, course_id: str, user_id: str) -> None:
    student_row = connection.execute(
        select(literal(1))
        .where(
            CourseStudent.course_id == course_id,
            CourseStudent.student_id == user_id,
            CourseStudent.status == "active",
        )
        .limit(1)
    ).fetchone()
    teacher_row = connection.execute(
        select(literal(1))
        .where(
            CourseTeacher.course_id == course_id,
            CourseTeacher.teacher_id == user_id,
            CourseTeacher.status == "active",
        )
        .limit(1)
    ).fetchone()
    if student_row is not None or teacher_row is not None:
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


def _clear_expired_course_invite_codes(connection, now: datetime) -> None:
    connection.execute(
        update(Course)
        .where(
            Course.invite_code.isnot(None),
            (Course.invite_code_expires_at.is_(None)) | (Course.invite_code_expires_at <= now),
        )
        .values(
            invite_code=None,
            invite_code_expires_at=None,
            invite_code_created_at=None,
        )
    )


def _generate_unique_course_invite_code(connection, now: datetime) -> str:
    characters = string.ascii_letters + string.digits
    for _ in range(20):
        invite_code = "".join(secrets.choice(characters) for _ in range(5))
        row = connection.execute(
            select(literal(1))
            .where(
                Course.invite_code == invite_code,
                Course.status == "active",
                Course.invite_code_expires_at > now,
            )
            .limit(1)
        ).fetchone()
        if row is None:
            return invite_code
    raise RuntimeError("Unable to generate a unique course invite code")


def _raise_if_course_name_exists(
    connection,
    course_name: str,
    owner_teacher_id: str,
    exclude_course_id: Optional[str] = None,
) -> None:
    conditions = [
        Course.course_name == course_name,
        Course.owner_teacher_id == owner_teacher_id,
        Course.status == "active",
    ]
    if exclude_course_id:
        conditions.append(Course.course_id != exclude_course_id)
    row = connection.execute(
        select(literal(1)).where(*conditions).limit(1)
    ).fetchone()
    if row is not None:
        raise ValueError("COURSE_NAME_EXISTS")


def _raise_if_course_name_unique_conflict(error: Exception) -> None:
    if (
        mysql_error_code(error) == 1062
        and "uniq_courses_owner_active_name" in str(error)
    ):
        raise ValueError("COURSE_NAME_EXISTS") from error


def _list_course_join_requests_sync(
    teacher_id: str,
    course_id: Optional[str],
) -> List[Dict[str, object]]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        conditions = [
            CourseTeacher.teacher_id == teacher_id,
            CourseTeacher.status == "active",
        ]
        if course_id:
            conditions.append(CourseJoinRequest.course_id == course_id)
        rows = connection.execute(
            select(
                CourseJoinRequest.request_id,
                CourseJoinRequest.course_id,
                Course.course_name,
                CourseJoinRequest.user_id,
                CourseJoinRequest.status,
                CourseJoinRequest.requested_at,
                CourseJoinRequest.reviewed_at,
                CourseJoinRequest.reviewed_by,
            )
            .join(Course, Course.course_id == CourseJoinRequest.course_id)
            .join(CourseTeacher, CourseTeacher.course_id == CourseJoinRequest.course_id)
            .where(*conditions)
            .order_by(CourseJoinRequest.requested_at.desc())
        ).fetchall()
        return [join_request_row_to_dict(row) for row in rows]


def _lock_join_request_user(connection, user_id: str, expected_role: str) -> bool:
    """Lock the auth row on the same MySQL transaction as course approval."""
    row = connection.execute(
        text(
            f"SELECT role, is_active, confirm_flag FROM {_AUTH_USERS_TABLE} "
            "WHERE uuid = :user_id FOR UPDATE"
        ),
        {"user_id": user_id},
    ).mappings().first()
    if row is None:
        raise ValueError("USER_NOT_FOUND")
    if not row["is_active"]:
        raise ValueError("USER_DISABLED")
    role = str(row["role"] or "").strip().lower()
    allowed_roles = {"student", "candidate", ""} if expected_role == "student" else {"teacher", "instructor"}
    if role not in allowed_roles:
        raise ValueError("USER_ROLE_UNSUPPORTED")
    return expected_role == "student" and int(row["confirm_flag"] or 0) != 1


def _activate_student(connection, user_id: str) -> None:
    connection.execute(
        text(f"UPDATE {_AUTH_USERS_TABLE} SET confirm_flag = 1 WHERE uuid = :user_id"),
        {"user_id": user_id},
    )


def _approve_course_join_request_by_user_sync(
    course_id: str,
    user_id: str,
    user_role: str,
    reviewer_id: str,
) -> Optional[Dict[str, object]]:
    ensure_database()
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(CourseJoinRequest.request_id)
            .join(Course, Course.course_id == CourseJoinRequest.course_id)
            .where(
                CourseJoinRequest.course_id == course_id,
                CourseJoinRequest.user_id == user_id,
                CourseJoinRequest.status == "pending",
                Course.status == "active",
            )
            .limit(1)
            .with_for_update()
        ).fetchone()
        if row is None:
            return None
        request_id = row[0]

        reviewer = connection.execute(
            select(CourseTeacher.id)
            .where(
                CourseTeacher.course_id == course_id,
                CourseTeacher.teacher_id == reviewer_id,
                CourseTeacher.status == "active",
            )
            .limit(1)
        ).fetchone()
        if reviewer is None:
            raise PermissionError("审批教师不是该课程的有效教师")

        needs_activation = _lock_join_request_user(connection, user_id, user_role)

        _raise_if_active_course_member_exists(connection, course_id, user_id)

        if user_role == "teacher":
            statement = mysql_insert(CourseTeacher).values(
                course_id=course_id,
                teacher_id=user_id,
                teacher_role="teacher",
                status="active",
                created_at=now,
            )
            connection.execute(
                statement.on_duplicate_key_update(
                    teacher_role=func.if_(
                        CourseTeacher.teacher_role == "owner",
                        CourseTeacher.teacher_role,
                        statement.inserted.teacher_role,
                    ),
                    status="active",
                    created_at=statement.inserted.created_at,
                )
            )
        elif user_role == "student":
            statement = mysql_insert(CourseStudent).values(
                course_id=course_id,
                student_id=user_id,
                status="active",
                joined_at=now,
            )
            connection.execute(
                statement.on_duplicate_key_update(
                    status="active",
                    joined_at=statement.inserted.joined_at,
                )
            )
        else:
            raise ValueError("USER_ROLE_UNSUPPORTED")
        create_pending_exam_sessions_for_user(connection, course_id, user_id)

        connection.execute(
            update(CourseJoinRequest)
            .where(CourseJoinRequest.request_id == request_id)
            .values(
                status="approved",
                reviewed_at=now,
                reviewed_by=reviewer_id,
            )
        )
        if needs_activation:
            _activate_student(connection, user_id)
    return {
        "request_id": request_id,
        "course_id": course_id,
        "user_id": user_id,
        "role": user_role,
    }


def _review_join_request_sync(request_id: str, teacher_id: str, target_status: str) -> bool:
    ensure_database()
    now = datetime.now()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(CourseJoinRequest.course_id, CourseJoinRequest.user_id)
            .join(
                CourseTeacher,
                CourseTeacher.course_id == CourseJoinRequest.course_id,
            )
            .where(
                CourseJoinRequest.request_id == request_id,
                CourseJoinRequest.status == "pending",
                CourseTeacher.teacher_id == teacher_id,
                CourseTeacher.status == "active",
            )
            .limit(1)
        ).fetchone()
        if row is None:
            return False
        course_id, user_id = row

        needs_activation = False
        if target_status == "approved":
            needs_activation = _lock_join_request_user(connection, user_id, "student")

        connection.execute(
            update(CourseJoinRequest)
            .where(CourseJoinRequest.request_id == request_id)
            .values(
                status=target_status,
                reviewed_at=now,
                reviewed_by=teacher_id,
            )
        )
        if target_status == "approved":
            statement = mysql_insert(CourseStudent).values(
                course_id=course_id,
                student_id=user_id,
                status="active",
                joined_at=now,
            )
            connection.execute(
                statement.on_duplicate_key_update(
                    status="active",
                    joined_at=statement.inserted.joined_at,
                )
            )
            create_pending_exam_sessions_for_user(connection, course_id, user_id)
            if needs_activation:
                _activate_student(connection, user_id)
    return True
