import asyncio
from typing import List, Optional

from .connection import connect, ensure_database
from .schema import ensure_tables


def build_exam_scope_filter(
    user_id: Optional[str],
    course_ids: Optional[List[str]],
    allow_course_scope: bool,
    allow_all: bool,
    table_alias: str = "",
) -> tuple[str, tuple]:
    prefix = f"{table_alias}." if table_alias else ""
    if allow_all:
        return "", ()
    if allow_course_scope:
        course_ids = [str(course_id) for course_id in (course_ids or []) if course_id]
        if not course_ids:
            return f"WHERE 1 = 0", ()
        placeholders = ", ".join(["%s"] * len(course_ids))
        return f"WHERE {prefix}course_id IN ({placeholders})", tuple(course_ids)
    if not user_id:
        raise ValueError("user_id is required for user scoped query")
    return f"WHERE {prefix}user_id = %s", (user_id,)


async def get_teacher_course_ids(teacher_id: str) -> List[str]:
    return await asyncio.to_thread(_get_teacher_course_ids_sync, teacher_id)


async def get_student_course_ids(student_id: str) -> List[str]:
    return await asyncio.to_thread(_get_student_course_ids_sync, student_id)


async def is_teacher_of_course(teacher_id: str, course_id: str) -> bool:
    return await asyncio.to_thread(_is_teacher_of_course_sync, teacher_id, course_id)


async def is_student_in_course(student_id: str, course_id: str) -> bool:
    return await asyncio.to_thread(_is_student_in_course_sync, student_id, course_id)


async def is_course_owner(user_id: str, course_id: str) -> bool:
    return await asyncio.to_thread(_is_course_owner_sync, user_id, course_id)


def _get_teacher_course_ids_sync(teacher_id: str) -> List[str]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT course_id
                FROM course_teachers
                WHERE teacher_id = %s
                  AND status = 'active'
                """,
                (teacher_id,),
            )
            return [row[0] for row in cursor.fetchall()]
    finally:
        connection.close()


def _get_student_course_ids_sync(student_id: str) -> List[str]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT course_id
                FROM course_students
                WHERE student_id = %s
                  AND status = 'active'
                """,
                (student_id,),
            )
            return [row[0] for row in cursor.fetchall()]
    finally:
        connection.close()


def _is_teacher_of_course_sync(teacher_id: str, course_id: str) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM course_teachers
                WHERE teacher_id = %s
                  AND course_id = %s
                  AND status = 'active'
                LIMIT 1
                """,
                (teacher_id, course_id),
            )
            return cursor.fetchone() is not None
    finally:
        connection.close()


def _is_student_in_course_sync(student_id: str, course_id: str) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM course_students
                WHERE student_id = %s
                  AND course_id = %s
                  AND status = 'active'
                LIMIT 1
                """,
                (student_id, course_id),
            )
            return cursor.fetchone() is not None
    finally:
        connection.close()


def _is_course_owner_sync(user_id: str, course_id: str) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1
                FROM courses
                WHERE owner_teacher_id = %s
                  AND course_id = %s
                  AND status = 'active'
                LIMIT 1
                """,
                (user_id, course_id),
            )
            return cursor.fetchone() is not None
    finally:
        connection.close()
