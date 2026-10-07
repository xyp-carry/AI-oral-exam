"""Persist completed repository-analysis Markdown by user, course, and exam."""

from __future__ import annotations

import asyncio
from datetime import datetime

from .connection import connect, ensure_database
from .schema import ensure_tables


async def save_analysis_document(
    user_id: str,
    course_id: str,
    exam_id: str,
    analysis_id: str,
    title: str,
    markdown_content: str,
) -> None:
    await asyncio.to_thread(
        _save_analysis_document_sync,
        user_id,
        course_id,
        exam_id,
        analysis_id,
        title,
        markdown_content,
    )


def _save_analysis_document_sync(
    user_id: str,
    course_id: str,
    exam_id: str,
    analysis_id: str,
    title: str,
    markdown_content: str,
) -> None:
    if not markdown_content.strip():
        raise ValueError("Analysis document is empty")
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT 1 FROM exam_sessions
                WHERE user_id = %s AND course_id = %s AND exam_id = %s
                LIMIT 1 FOR UPDATE
                """,
                (user_id, course_id, exam_id),
            )
            if cursor.fetchone() is None:
                raise ValueError("Exam session does not match user and course")
            cursor.execute(
                """
                INSERT INTO exam_analysis_documents (
                    user_id, course_id, exam_id, analysis_id,
                    title, markdown_content
                ) VALUES (%s, %s, %s, %s, %s, %s)
                ON DUPLICATE KEY UPDATE
                    analysis_id = VALUES(analysis_id),
                    title = VALUES(title),
                    markdown_content = VALUES(markdown_content),
                    updated_at = NOW(6)
                """,
                (user_id, course_id, exam_id, analysis_id, title, markdown_content),
            )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


async def get_analysis_document(
    user_id: str, course_id: str, exam_id: str
) -> dict | None:
    return await asyncio.to_thread(
        _get_analysis_document_sync, user_id, course_id, exam_id
    )


def _get_analysis_document_sync(
    user_id: str, course_id: str, exam_id: str
) -> dict | None:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT analysis_id, title, markdown_content, updated_at
                FROM exam_analysis_documents
                WHERE user_id = %s AND course_id = %s AND exam_id = %s
                LIMIT 1
                """,
                (user_id, course_id, exam_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        updated_at = row[3]
        return {
            "user_id": user_id,
            "course_id": course_id,
            "exam_id": exam_id,
            "analysis_id": row[0],
            "title": row[1],
            "markdown_content": row[2],
            "updated_at": updated_at.isoformat() if isinstance(updated_at, datetime) else updated_at,
        }
    finally:
        connection.close()


async def get_analysis_document_metadata(
    user_id: str, course_id: str, exam_id: str
) -> dict | None:
    """Read only the title and identifiers used by one-second progress polling."""
    return await asyncio.to_thread(
        _get_analysis_document_metadata_sync, user_id, course_id, exam_id
    )


def _get_analysis_document_metadata_sync(
    user_id: str, course_id: str, exam_id: str
) -> dict | None:
    connection = connect(use_database=True)
    try:
        with connection.cursor() as cursor:
            cursor.execute(
                """
                SELECT analysis_id, title, UNIX_TIMESTAMP(updated_at)
                FROM exam_analysis_documents
                WHERE user_id = %s AND course_id = %s AND exam_id = %s
                LIMIT 1
                """,
                (user_id, course_id, exam_id),
            )
            row = cursor.fetchone()
        if row is None:
            return None
        return {
            "analysis_id": row[0],
            "title": row[1],
            "updated_at": float(row[2]) if row[2] is not None else None,
        }
    except Exception as exc:
        if getattr(exc, "args", (None,))[0] == 1146:
            return None
        raise
    finally:
        connection.close()
