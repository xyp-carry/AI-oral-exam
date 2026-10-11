"""Normalize and persist oral questions produced by repository analysis."""

from AIOralExamSystem.Exam.Examdata.exam_item_repository import get_exam_item_by_id
from AIOralExamSystem.Exam.Examdata.preset_question_repository import (
    AI_CREATED_BY,
    create_preset_question,
    delete_preset_questions_by_exam_item_and_user,
)


def _normalize_a_report_question_item(question: object) -> dict:
    if not isinstance(question, dict):
        return {}
    question_content = str(question.get("Question") or question.get("question") or "").strip()
    if not question_content:
        return {}
    try:
        score = float(question.get("score", 1.0) or 1.0)
    except (TypeError, ValueError):
        score = 1.0
    return {
        "dimension": str(
            question.get("dimension")
            or question.get("question_dimension")
            or question.get("aspect")
            or ""
        ).strip(),
        "Question": question_content,
        "standard_answer": str(
            question.get("standard_answer")
            or question.get("Answer")
            or question.get("answer")
            or question.get("reference_answer")
            or ""
        ).strip(),
        "question_blocks": question.get("question_blocks") if isinstance(question.get("question_blocks"), list) else [],
        "code_fragments": question.get("code_fragments") if isinstance(question.get("code_fragments"), list) else [],
        "score": score,
    }


def _extract_a_questions_from_report_result(report_result: dict) -> dict:
    if not isinstance(report_result, dict):
        return {"ok": True, "flag": "NO_RECORDED_A_MODE_QUESTIONS", "question_count": 0, "questions": []}
    raw_questions = (
        report_result.get("questions")
        or report_result.get("a_mode_questions")
        or report_result.get("preset_questions")
        or []
    )
    if isinstance(raw_questions, dict):
        raw_questions = raw_questions.get("questions") or raw_questions.get("items") or []
    if not isinstance(raw_questions, list):
        raw_questions = []
    questions = []
    seen = set()
    for question in raw_questions:
        item = _normalize_a_report_question_item(question)
        key = str(item.get("Question") or "").casefold()
        if key and key not in seen:
            seen.add(key)
            questions.append(item)
    return {
        "ok": True,
        "flag": "A_MODE_QUESTIONS_REUSED" if questions else "NO_RECORDED_A_MODE_QUESTIONS",
        "question_count": len(questions),
        "questions": questions,
    }


async def _save_a_questions_to_preset_questions(
    course_id: str,
    exam_item_id: str,
    user_id: str,
    question_result: dict,
) -> dict:
    exam_item_id = str(exam_item_id or "").strip()
    user_id = str(user_id or "").strip()
    if not exam_item_id:
        return {"ok": False, "flag": "EXAM_ITEM_ID_MISSING", "saved_count": 0, "saved_questions": []}
    if not user_id:
        return {"ok": False, "flag": "USER_ID_MISSING", "saved_count": 0, "saved_questions": []}

    raw_questions = question_result.get("questions") if isinstance(question_result, dict) else []
    if not isinstance(raw_questions, list):
        raw_questions = []
    questions = [
        item
        for item in (_normalize_a_report_question_item(question) for question in raw_questions)
        if item.get("Question")
    ]
    deleted_count = await delete_preset_questions_by_exam_item_and_user(
        course_id,
        exam_item_id,
        user_id,
    )
    if not questions:
        return {
            "ok": True,
            "flag": "NO_A_MODE_QUESTIONS",
            "deleted_count": deleted_count,
            "saved_count": 0,
            "saved_questions": [],
        }

    exam_item = await get_exam_item_by_id(exam_item_id)
    dimension_names = exam_item.get("dimension_names") if isinstance(exam_item, dict) else []
    if not isinstance(dimension_names, list):
        dimension_names = []
    dimension_names = [str(item or "").strip() for item in dimension_names if str(item or "").strip()]

    saved_questions = []
    for index, question in enumerate(questions, start=1):
        dimension = str(question.get("dimension") or "").strip()
        if dimension_names and dimension not in dimension_names:
            dimension = dimension_names[(index - 1) % len(dimension_names)]
        if not dimension:
            dimension = "general"
        created = await create_preset_question(
            course_id=course_id,
            exam_item_id=exam_item_id,
            created_by=AI_CREATED_BY,
            user_id=user_id,
            question_dimension=dimension,
            question_content=str(question.get("Question") or "").strip(),
            standard_answer=str(question.get("standard_answer") or "").strip(),
            question_blocks=question.get("question_blocks") if isinstance(question.get("question_blocks"), list) else [],
            code_fragments=question.get("code_fragments") if isinstance(question.get("code_fragments"), list) else [],
            score=float(question.get("score", 1.0) or 1.0),
            sort_order=index,
            validate_dimension=bool(dimension_names),
        )
        saved_questions.append(created)

    return {
        "ok": True,
        "flag": "A_MODE_PRESET_QUESTIONS_SAVED",
        "deleted_count": deleted_count,
        "saved_count": len(saved_questions),
        "saved_questions": saved_questions,
    }

def _normalize_c_core_module_question_source(source: object) -> dict | None:
    if not isinstance(source, dict):
        return None
    file_path = str(source.get("file_path") or source.get("path") or "").strip()
    start_line = source.get("start_line") or source.get("line_start") or source.get("line")
    end_line = source.get("end_line") or source.get("line_end") or source.get("line")
    if not isinstance(start_line, int):
        start_line = None
    if not isinstance(end_line, int):
        end_line = None
    if start_line is not None and end_line is None:
        end_line = start_line
    if end_line is not None and start_line is None:
        start_line = end_line
    if start_line is not None and end_line is not None and end_line < start_line:
        start_line, end_line = end_line, start_line
    if not file_path and start_line is None and end_line is None:
        return None
    return {"file_path": file_path, "start_line": start_line, "end_line": end_line}


def _normalize_c_core_module_question_sources(sources: object) -> list[dict]:
    if isinstance(sources, dict):
        sources = [sources]
    if not isinstance(sources, list):
        return []
    normalized = []
    seen = set()
    for item in sources:
        source = _normalize_c_core_module_question_source(item)
        if not source:
            continue
        key = (source["file_path"], source["start_line"], source["end_line"])
        if key in seen:
            continue
        seen.add(key)
        normalized.append(source)
    return normalized


def _normalize_c_core_module_question_item(question: object) -> dict:
    if isinstance(question, str):
        return {"aspect": "general", "question": question.strip(), "Answer": "", "source": []}
    if not isinstance(question, dict):
        return {"aspect": "", "question": "", "Answer": "", "source": []}
    return {
        "aspect": str(question.get("aspect") or question.get("dimension") or "general").strip() or "general",
        "question": str(question.get("question") or question.get("Question") or "").strip(),
        "Answer": str(
            question.get("Answer")
            or question.get("answer")
            or question.get("standard_answer")
            or question.get("reference_answer")
            or ""
        ).strip(),
        "source": _normalize_c_core_module_question_sources(question.get("source")),
    }


def _extract_c_core_module_questions_from_report_result(report_result: dict) -> dict:
    raw_questions = report_result.get("core_module_questions") if isinstance(report_result, dict) else {}
    if not isinstance(raw_questions, dict) or not raw_questions:
        return {
            "ok": True,
            "flag": "NO_RECORDED_CORE_MODULE_QUESTIONS",
            "question_count": 0,
            "questions": {},
        }

    questions_by_module = {}
    total_count = 0
    seen_questions = set()
    for module_key, question_set in raw_questions.items():
        if not isinstance(question_set, dict) or not question_set.get("ok", True):
            continue
        module_name = str(question_set.get("module_name") or module_key or "").strip()
        if not module_name:
            continue
        items = question_set.get("questions") or []
        if not isinstance(items, list):
            continue
        questions = []
        for item in items:
            normalized_item = _normalize_c_core_module_question_item(item)
            key = str(normalized_item.get("question") or "").casefold()
            if key and key not in seen_questions:
                seen_questions.add(key)
                questions.append(normalized_item)
        if not questions:
            continue
        questions_by_module[module_key] = {
            "ok": True,
            "flag": str(question_set.get("flag") or "CORE_MODULE_QUESTIONS_REUSED"),
            "module_name": module_name,
            "questions": questions,
        }
        total_count += len(questions)

    return {
        "ok": True,
        "flag": "CORE_MODULE_QUESTIONS_REUSED",
        "question_count": total_count,
        "questions": questions_by_module,
    }


def _build_c_core_module_question_blocks(question_content: str, aspect: str, sources: list[dict]) -> list[dict]:
    block = {"type": "text", "content": question_content}
    if aspect:
        block["aspect"] = aspect
    if sources:
        block["source"] = sources
    return [block]


async def _save_c_core_module_questions_to_preset_questions(
    course_id: str,
    exam_item_id: str,
    user_id: str,
    question_result: dict,
) -> dict:
    exam_item_id = str(exam_item_id or "").strip()
    user_id = str(user_id or "").strip()
    if not exam_item_id:
        return {"ok": False, "flag": "EXAM_ITEM_ID_MISSING", "saved_count": 0, "saved_questions": []}
    if not user_id:
        return {"ok": False, "flag": "USER_ID_MISSING", "saved_count": 0, "saved_questions": []}

    deleted_count = await delete_preset_questions_by_exam_item_and_user(
        course_id,
        exam_item_id,
        user_id,
    )
    questions_by_module = question_result.get("questions") if isinstance(question_result, dict) else {}
    if not isinstance(questions_by_module, dict) or not questions_by_module:
        return {
            "ok": True,
            "flag": "NO_CORE_MODULE_QUESTIONS",
            "deleted_count": deleted_count,
            "saved_count": 0,
            "saved_questions": [],
        }

    saved_questions = []
    sort_order = 1

    async def save_question(module_name: str, question: dict, aspect: str) -> None:
        nonlocal sort_order
        question_content = str(question.get("question") or question.get("Question") or "").strip()
        if not question_content:
            return
        sources = _normalize_c_core_module_question_sources(question.get("source"))
        created = await create_preset_question(
            course_id=course_id,
            exam_item_id=exam_item_id,
            created_by=AI_CREATED_BY,
            user_id=user_id,
            question_dimension=module_name,
            question_content=question_content,
            standard_answer=str(
                question.get("Answer")
                or question.get("answer")
                or question.get("standard_answer")
                or question.get("reference_answer")
                or ""
            ).strip(),
            question_blocks=_build_c_core_module_question_blocks(question_content, aspect, sources),
            code_fragments=[],
            score=10,
            sort_order=sort_order,
            validate_dimension=False,
        )
        saved_questions.append(created)
        sort_order += 1

    prepared: list[tuple[str, list[dict]]] = []
    for module_key, question_set in questions_by_module.items():
        if not isinstance(question_set, dict) or not question_set.get("ok", True):
            continue
        module_name = str(question_set.get("module_name") or module_key or "").strip()
        if not module_name:
            continue

        items = question_set.get("questions") or []
        if not isinstance(items, list):
            continue
        normalized_items = [
            normalized for item in items
            if (normalized := _normalize_c_core_module_question_item(item)).get("question")
        ]
        if normalized_items:
            prepared.append((module_name, normalized_items))

    for position in range(max((len(items) for _, items in prepared), default=0)):
        for module_name, items in prepared:
            if position >= len(items):
                continue
            normalized = items[position]
            await save_question(
                module_name,
                normalized,
                str(normalized.get("aspect") or "general").strip() or "general",
            )

    return {
        "ok": True,
        "flag": "CORE_MODULE_PRESET_QUESTIONS_SAVED",
        "deleted_count": deleted_count,
        "saved_count": len(saved_questions),
        "saved_questions": saved_questions,
    }
