import asyncio
from typing import List, Optional

from sqlalchemy import ColumnElement, literal, select

from .connection import ensure_database, get_engine
from .models import Course, CourseStudent, CourseTeacher
from .schema import ensure_tables


def build_exam_scope_conditions(
    user_id: Optional[str],
    course_ids: Optional[List[str]],
    allow_course_scope: bool,
    allow_all: bool,
    user_id_column: ColumnElement,
    course_id_column: ColumnElement,
) -> list[ColumnElement]:
    """生成 user/course/all 三级数据范围的过滤条件（空列表表示不加限制）。"""
    if allow_all:
        return []
    if allow_course_scope:
        normalized = [str(course_id) for course_id in (course_ids or []) if course_id]
        if not normalized:
            return [literal(0).self_group() == 1]
        return [course_id_column.in_(normalized)]
    if not user_id:
        raise ValueError("user_id is required for user scoped query")
    return [user_id_column == user_id]


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
    with get_engine().begin() as connection:
        ensure_tables(connection)
        rows = connection.execute(
            select(CourseTeacher.course_id).where(
                CourseTeacher.teacher_id == teacher_id,
                CourseTeacher.status == "active",
            )
        ).fetchall()
        return [row[0] for row in rows]


def _get_student_course_ids_sync(student_id: str) -> List[str]:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        rows = connection.execute(
            select(CourseStudent.course_id).where(
                CourseStudent.student_id == student_id,
                CourseStudent.status == "active",
            )
        ).fetchall()
        return [row[0] for row in rows]


def _is_teacher_of_course_sync(teacher_id: str, course_id: str) -> bool:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(literal(1))
            .where(
                CourseTeacher.teacher_id == teacher_id,
                CourseTeacher.course_id == course_id,
                CourseTeacher.status == "active",
            )
            .limit(1)
        ).fetchone()
        return row is not None


def _is_student_in_course_sync(student_id: str, course_id: str) -> bool:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(literal(1))
            .where(
                CourseStudent.student_id == student_id,
                CourseStudent.course_id == course_id,
                CourseStudent.status == "active",
            )
            .limit(1)
        ).fetchone()
        return row is not None


def _is_course_owner_sync(user_id: str, course_id: str) -> bool:
    ensure_database()
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(literal(1))
            .where(
                Course.owner_teacher_id == user_id,
                Course.course_id == course_id,
                Course.status == "active",
            )
            .limit(1)
        ).fetchone()
        return row is not None
