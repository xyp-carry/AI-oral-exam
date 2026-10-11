"""Persist completed repository-analysis Markdown by user, course, and exam."""

from __future__ import annotations

import asyncio
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.dialects.mysql import insert as mysql_insert

from .connection import ensure_database, get_engine, mysql_error_code
from .models import ExamAnalysisDocument, ExamSession
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
    with get_engine().begin() as connection:
        ensure_tables(connection)
        session_row = connection.execute(
            select(ExamSession.exam_id)
            .where(
                ExamSession.user_id == user_id,
                ExamSession.course_id == course_id,
                ExamSession.exam_id == exam_id,
            )
            .limit(1)
            .with_for_update()
        ).fetchone()
        if session_row is None:
            raise ValueError("Exam session does not match user and course")
        statement = mysql_insert(ExamAnalysisDocument).values(
            user_id=user_id,
            course_id=course_id,
            exam_id=exam_id,
            analysis_id=analysis_id,
            title=title,
            markdown_content=markdown_content,
        )
        connection.execute(
            statement.on_duplicate_key_update(
                analysis_id=statement.inserted.analysis_id,
                title=statement.inserted.title,
                markdown_content=statement.inserted.markdown_content,
                updated_at=func.now(6),
            )
        )


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
    with get_engine().begin() as connection:
        ensure_tables(connection)
        row = connection.execute(
            select(
                ExamAnalysisDocument.analysis_id,
                ExamAnalysisDocument.title,
                ExamAnalysisDocument.markdown_content,
                ExamAnalysisDocument.updated_at,
            ).where(
                ExamAnalysisDocument.user_id == user_id,
                ExamAnalysisDocument.course_id == course_id,
                ExamAnalysisDocument.exam_id == exam_id,
            ).limit(1)
        ).fetchone()
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
    try:
        with get_engine().begin() as connection:
            row = connection.execute(
                select(
                    ExamAnalysisDocument.analysis_id,
                    ExamAnalysisDocument.title,
                    func.unix_timestamp(ExamAnalysisDocument.updated_at),
                ).where(
                    ExamAnalysisDocument.user_id == user_id,
                    ExamAnalysisDocument.course_id == course_id,
                    ExamAnalysisDocument.exam_id == exam_id,
                ).limit(1)
            ).fetchone()
    except Exception as exc:
        if mysql_error_code(exc) == 1146:
            return None
        raise
    if row is None:
        return None
    return {
        "analysis_id": row[0],
        "title": row[1],
        "updated_at": float(row[2]) if row[2] is not None else None,
    }
