import asyncio
import json
import re
import uuid
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from .connection import connect, ensure_database
from .schema import ensure_tables
from .serializers import preset_question_row_to_dict, to_json


AI_CREATED_BY = "AI"


async def create_preset_question(
    course_id: str,
    exam_item_id: str,
    created_by: str,
    question_dimension: str,
    question_content: str,
    standard_answer: Optional[str] = None,
    question_blocks: Optional[List[Dict[str, object]]] = None,
    code_fragments: Optional[List[Dict[str, object]]] = None,
    score: float = 1.0,
    sort_order: Optional[int] = None,
    user_id: Optional[str] = None,
) -> Dict[str, object]:
    return await asyncio.to_thread(
        _create_preset_question_sync,
        course_id,
        exam_item_id,
        created_by,
        question_dimension,
        question_content,
        standard_answer,
        question_blocks,
        code_fragments,
        score,
        sort_order,
        user_id,
    )


async def list_preset_questions_by_exam_item(
    course_id: str,
    exam_item_id: str,
    user_id: Optional[str] = None,
) -> List[Dict[str, object]]:
    return await asyncio.to_thread(
        _list_preset_questions_by_exam_item_sync,
        course_id,
        exam_item_id,
        user_id,
    )


async def list_ai_preset_questions_by_exam_item_and_user(
    course_id: str,
    exam_item_id: str,
    user_id: str,
) -> List[Dict[str, object]]:
    return await asyncio.to_thread(
        _list_ai_preset_questions_by_exam_item_and_user_sync,
        course_id,
        exam_item_id,
        user_id,
    )


async def deactivate_ai_preset_questions_by_exam_item_and_user(
    course_id: str,
    exam_item_id: str,
    user_id: str,
) -> int:
    return await asyncio.to_thread(
        _deactivate_ai_preset_questions_by_exam_item_and_user_sync,
        course_id,
        exam_item_id,
        user_id,
    )


async def update_preset_question(
    course_id: str,
    exam_item_id: str,
    preset_question_id: str,
    question_dimension: Optional[str] = None,
    question_content: Optional[str] = None,
    standard_answer: Optional[str] = None,
    question_blocks: Optional[List[Dict[str, object]]] = None,
    code_fragments: Optional[List[Dict[str, object]]] = None,
    score: Optional[float] = None,
    sort_order: Optional[int] = None,
) -> Optional[Dict[str, object]]:
    return await asyncio.to_thread(
        _update_preset_question_sync,
        course_id,
        exam_item_id,
        preset_question_id,
        question_dimension,
        question_content,
        standard_answer,
        question_blocks,
        code_fragments,
        score,
        sort_order,
    )


async def delete_preset_question(
    course_id: str,
    exam_item_id: str,
    preset_question_id: str,
) -> bool:
    return await asyncio.to_thread(
        _delete_preset_question_sync,
        course_id,
        exam_item_id,
        preset_question_id,
    )


def _create_preset_question_sync(
    course_id: str,
    exam_item_id: str,
    created_by: str,
    question_dimension: str,
    question_content: str,
    standard_answer: Optional[str],
    question_blocks: Optional[List[Dict[str, object]]],
    code_fragments: Optional[List[Dict[str, object]]],
    score: float,
    sort_order: Optional[int],
    user_id: Optional[str],
) -> Dict[str, object]:
    ensure_database()
    connection = connect(use_database=True)
    preset_question_id = str(uuid.uuid4())
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            question_dimension = _normalize_question_dimension(question_dimension)
            _raise_if_dimension_invalid(cursor, course_id, exam_item_id, question_dimension)
            question_content = _normalize_question_content(question_content)
            parsed_question_blocks, parsed_code_fragments = _parse_question_markdown(question_content)
            score = _normalize_score(score)
            if sort_order is None:
                sort_order = _next_sort_order(cursor, exam_item_id)
            else:
                sort_order = _normalize_sort_order(sort_order)
            cursor.execute(
                """
                INSERT INTO exam_preset_questions (
                    preset_question_id,
                    exam_item_id,
                    user_id,
                    question_dimension,
                    question_content,
                    standard_answer,
                    question_blocks_json,
                    code_fragments_json,
                    score,
                    sort_order,
                    status,
                    created_by,
                    created_at,
                    updated_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'active', %s, %s, %s)
                """,
                (
                    preset_question_id,
                    exam_item_id,
                    _normalize_optional_user_id(user_id),
                    question_dimension,
                    question_content,
                    standard_answer,
                    to_json(parsed_question_blocks),
                    to_json(parsed_code_fragments),
                    score,
                    sort_order,
                    created_by,
                    now,
                    now,
                ),
            )
            created = _select_preset_question(cursor, course_id, exam_item_id, preset_question_id)
        connection.commit()
        return created
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _list_preset_questions_by_exam_item_sync(
    course_id: str,
    exam_item_id: str,
    user_id: Optional[str],
) -> List[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _raise_if_exam_item_missing(cursor, course_id, exam_item_id)
            cursor.execute(
                """
                SELECT
                    q.preset_question_id,
                    q.exam_item_id,
                    q.user_id,
                    q.question_dimension,
                    q.question_content,
                    q.standard_answer,
                    q.question_blocks_json,
                    q.code_fragments_json,
                    q.score,
                    q.sort_order,
                    q.status,
                    q.created_by,
                    q.created_at,
                    q.updated_at
                FROM exam_preset_questions q
                JOIN course_exam_items i
                  ON q.exam_item_id = i.exam_item_id
                WHERE i.course_id = %s
                  AND q.exam_item_id = %s
                  AND q.created_by <> %s
                  AND (q.user_id IS NULL OR q.user_id = %s)
                  AND q.status = 'active'
                  AND i.status = 'active'
                ORDER BY q.sort_order ASC, q.created_at ASC
                """,
                (course_id, exam_item_id, AI_CREATED_BY, _normalize_optional_user_id(user_id)),
            )
            return [preset_question_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _list_ai_preset_questions_by_exam_item_and_user_sync(
    course_id: str,
    exam_item_id: str,
    user_id: str,
) -> List[Dict[str, object]]:
    user_id = _normalize_user_id(user_id)
    ensure_database()
    connection = connect(use_database=True)
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _raise_if_exam_item_missing(cursor, course_id, exam_item_id)
            cursor.execute(
                """
                SELECT
                    q.preset_question_id,
                    q.exam_item_id,
                    q.user_id,
                    q.question_dimension,
                    q.question_content,
                    q.standard_answer,
                    q.question_blocks_json,
                    q.code_fragments_json,
                    q.score,
                    q.sort_order,
                    q.status,
                    q.created_by,
                    q.created_at,
                    q.updated_at
                FROM exam_preset_questions q
                JOIN course_exam_items i
                  ON q.exam_item_id = i.exam_item_id
                WHERE i.course_id = %s
                  AND q.exam_item_id = %s
                  AND q.user_id = %s
                  AND q.created_by = %s
                  AND q.status = 'active'
                  AND i.status = 'active'
                ORDER BY q.sort_order ASC, q.created_at ASC
                """,
                (course_id, exam_item_id, user_id, AI_CREATED_BY),
            )
            return [preset_question_row_to_dict(row) for row in cursor.fetchall()]
    finally:
        connection.close()


def _deactivate_ai_preset_questions_by_exam_item_and_user_sync(
    course_id: str,
    exam_item_id: str,
    user_id: str,
) -> int:
    user_id = _normalize_user_id(user_id)
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _raise_if_exam_item_missing(cursor, course_id, exam_item_id)
            cursor.execute(
                """
                UPDATE exam_preset_questions q
                JOIN course_exam_items i
                  ON q.exam_item_id = i.exam_item_id
                SET q.status = 'deleted',
                    q.updated_at = %s
                WHERE i.course_id = %s
                  AND q.exam_item_id = %s
                  AND q.user_id = %s
                  AND q.created_by = %s
                  AND q.status = 'active'
                  AND i.status = 'active'
                """,
                (now, course_id, exam_item_id, user_id, AI_CREATED_BY),
            )
            count = cursor.rowcount
        connection.commit()
        return count
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _update_preset_question_sync(
    course_id: str,
    exam_item_id: str,
    preset_question_id: str,
    question_dimension: Optional[str],
    question_content: Optional[str],
    standard_answer: Optional[str],
    question_blocks: Optional[List[Dict[str, object]]],
    code_fragments: Optional[List[Dict[str, object]]],
    score: Optional[float],
    sort_order: Optional[int],
) -> Optional[Dict[str, object]]:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _raise_if_exam_item_missing(cursor, course_id, exam_item_id)
            if not _preset_question_exists(cursor, course_id, exam_item_id, preset_question_id):
                return None

            set_clauses = []
            values = []
            if question_dimension is not None:
                question_dimension = _normalize_question_dimension(question_dimension)
                _raise_if_dimension_invalid(cursor, course_id, exam_item_id, question_dimension)
                set_clauses.append("question_dimension = %s")
                values.append(question_dimension)
            if question_content is not None:
                question_content = _normalize_question_content(question_content)
                parsed_question_blocks, parsed_code_fragments = _parse_question_markdown(question_content)
                set_clauses.append("question_content = %s")
                values.append(question_content)
                set_clauses.append("question_blocks_json = %s")
                values.append(to_json(parsed_question_blocks))
                set_clauses.append("code_fragments_json = %s")
                values.append(to_json(parsed_code_fragments))
            if standard_answer is not None:
                set_clauses.append("standard_answer = %s")
                values.append(standard_answer)
            if score is not None:
                set_clauses.append("score = %s")
                values.append(_normalize_score(score))
            if sort_order is not None:
                set_clauses.append("sort_order = %s")
                values.append(_normalize_sort_order(sort_order))

            if set_clauses:
                set_clauses.append("updated_at = %s")
                values.append(now)
                values.extend([exam_item_id, preset_question_id])
                cursor.execute(
                    f"""
                    UPDATE exam_preset_questions
                    SET {', '.join(set_clauses)}
                    WHERE exam_item_id = %s
                      AND preset_question_id = %s
                      AND status = 'active'
                    """,
                    values,
                )
            updated = _select_preset_question(cursor, course_id, exam_item_id, preset_question_id)
        connection.commit()
        return updated
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _delete_preset_question_sync(
    course_id: str,
    exam_item_id: str,
    preset_question_id: str,
) -> bool:
    ensure_database()
    connection = connect(use_database=True)
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    try:
        ensure_tables(connection)
        with connection.cursor() as cursor:
            _raise_if_exam_item_missing(cursor, course_id, exam_item_id)
            cursor.execute(
                """
                UPDATE exam_preset_questions
                SET status = 'deleted',
                    updated_at = %s
                WHERE exam_item_id = %s
                  AND preset_question_id = %s
                  AND status = 'active'
                """,
                (now, exam_item_id, preset_question_id),
            )
            deleted = cursor.rowcount > 0
        connection.commit()
        return deleted
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def _select_preset_question(cursor, course_id: str, exam_item_id: str, preset_question_id: str) -> Dict[str, object]:
    cursor.execute(
        """
        SELECT
            q.preset_question_id,
            q.exam_item_id,
            q.user_id,
            q.question_dimension,
            q.question_content,
            q.standard_answer,
            q.question_blocks_json,
            q.code_fragments_json,
            q.score,
            q.sort_order,
            q.status,
            q.created_by,
            q.created_at,
            q.updated_at
        FROM exam_preset_questions q
        JOIN course_exam_items i
          ON q.exam_item_id = i.exam_item_id
        WHERE i.course_id = %s
          AND q.exam_item_id = %s
          AND q.preset_question_id = %s
          AND q.status = 'active'
          AND i.status = 'active'
        LIMIT 1
        """,
        (course_id, exam_item_id, preset_question_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("PRESET_QUESTION_NOT_FOUND")
    return preset_question_row_to_dict(row)


def _preset_question_exists(cursor, course_id: str, exam_item_id: str, preset_question_id: str) -> bool:
    cursor.execute(
        """
        SELECT 1
        FROM exam_preset_questions q
        JOIN course_exam_items i
          ON q.exam_item_id = i.exam_item_id
        WHERE i.course_id = %s
          AND q.exam_item_id = %s
          AND q.preset_question_id = %s
          AND q.status = 'active'
          AND i.status = 'active'
        LIMIT 1
        """,
        (course_id, exam_item_id, preset_question_id),
    )
    return cursor.fetchone() is not None


def _raise_if_dimension_invalid(cursor, course_id: str, exam_item_id: str, question_dimension: str) -> None:
    dimension_names = _get_exam_item_dimension_names(cursor, course_id, exam_item_id)
    if question_dimension not in dimension_names:
        raise ValueError("PRESET_QUESTION_DIMENSION_INVALID")


def _raise_if_exam_item_missing(cursor, course_id: str, exam_item_id: str) -> None:
    _get_exam_item_dimension_names(cursor, course_id, exam_item_id)


def _get_exam_item_dimension_names(cursor, course_id: str, exam_item_id: str) -> List[str]:
    cursor.execute(
        """
        SELECT dimension_names_json, dimension_scores_json
        FROM course_exam_items
        WHERE course_id = %s
          AND exam_item_id = %s
          AND status = 'active'
        LIMIT 1
        """,
        (course_id, exam_item_id),
    )
    row = cursor.fetchone()
    if row is None:
        raise ValueError("EXAM_ITEM_NOT_FOUND")
    dimension_names = _json_loads(row[0], [])
    if not dimension_names:
        dimension_scores = _json_loads(row[1], {})
        if isinstance(dimension_scores, dict):
            dimension_names = list(dimension_scores.keys())
    return [str(name).strip() for name in dimension_names if str(name).strip()]


def _next_sort_order(cursor, exam_item_id: str) -> int:
    cursor.execute(
        """
        SELECT COALESCE(MAX(sort_order), 0) + 1
        FROM exam_preset_questions
        WHERE exam_item_id = %s
          AND status = 'active'
        """,
        (exam_item_id,),
    )
    return int(cursor.fetchone()[0] or 1)


def _normalize_question_dimension(question_dimension: str) -> str:
    question_dimension = str(question_dimension or "").strip()
    if not question_dimension:
        raise ValueError("PRESET_QUESTION_DIMENSION_REQUIRED")
    return question_dimension


def _normalize_user_id(user_id: str) -> str:
    normalized = str(user_id or "").strip()
    if not normalized:
        raise ValueError("PRESET_QUESTION_USER_ID_REQUIRED")
    return normalized


def _normalize_optional_user_id(user_id: Optional[str]) -> Optional[str]:
    normalized = str(user_id or "").strip()
    return normalized or None


def _normalize_question_content(question_content: str) -> str:
    question_content = str(question_content or "").strip()
    if not question_content:
        raise ValueError("PRESET_QUESTION_CONTENT_REQUIRED")
    return question_content


def _parse_question_markdown(question_content: str) -> Tuple[List[Dict[str, object]], List[Dict[str, object]]]:
    question_blocks: List[Dict[str, object]] = []
    code_fragments: List[Dict[str, object]] = []
    text_lines: List[str] = []
    code_lines: List[str] = []
    in_code_block = False
    fence_char = ""
    fence_length = 0
    language = ""
    title = ""

    def flush_text() -> None:
        content = "".join(text_lines).strip()
        text_lines.clear()
        if content:
            question_blocks.append({"type": "text", "content": content})

    def flush_code() -> None:
        nonlocal language, title
        if not code_lines:
            language = ""
            title = ""
            return
        fragment_id = f"snippet_{len(code_fragments) + 1}"
        question_blocks.append({"type": "code", "fragment_id": fragment_id})
        code_fragments.append({
            "id": fragment_id,
            "relative_path": None,
            "start_line": 0,
            "end_line": 0,
            "language": language,
            "title": title,
            "lines": code_lines.copy(),
        })
        code_lines.clear()
        language = ""
        title = ""

    def parse_code_info(raw_info: str) -> Tuple[str, str]:
        info = raw_info.strip()
        if not info:
            return "", ""
        parts = info.split(None, 1)
        if len(parts) == 1:
            return parts[0], ""
        return parts[0], parts[1].strip()

    opening_fence_pattern = re.compile(r"^\s{0,3}(`{3,}|~{3,})(.*)$")

    for raw_line in question_content.splitlines(keepends=True):
        line_without_newline = raw_line.rstrip("\r\n")
        fence_match = opening_fence_pattern.match(line_without_newline)
        if in_code_block:
            if fence_match:
                marker = fence_match.group(1)
                if marker.startswith(fence_char) and len(marker) >= fence_length:
                    flush_code()
                    in_code_block = False
                    fence_char = ""
                    fence_length = 0
                    continue
            code_lines.append(line_without_newline)
            continue

        if fence_match:
            flush_text()
            marker = fence_match.group(1)
            fence_char = marker[0]
            fence_length = len(marker)
            language, title = parse_code_info(fence_match.group(2))
            in_code_block = True
            continue

        text_lines.append(raw_line)

    if in_code_block:
        flush_code()
    flush_text()

    if not question_blocks:
        question_blocks.append({"type": "text", "content": question_content})
    return question_blocks, code_fragments


def _normalize_score(score: float) -> float:
    try:
        return float(score)
    except (TypeError, ValueError) as exc:
        raise ValueError("PRESET_QUESTION_SCORE_INVALID") from exc


def _normalize_sort_order(sort_order: int) -> int:
    try:
        return int(sort_order)
    except (TypeError, ValueError) as exc:
        raise ValueError("PRESET_QUESTION_SORT_ORDER_INVALID") from exc


def _json_loads(value, default):
    if value is None:
        return default
    if isinstance(value, (list, dict)):
        return value
    try:
        return json.loads(value)
    except (TypeError, ValueError):
        return default
