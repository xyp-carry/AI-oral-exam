import asyncio
import shutil
from pathlib import Path
from typing import Dict, List

from fastapi import Depends, HTTPException, Request
from loguru import logger
from pydantic import BaseModel

from AIOralExamSystem.Exam.Examdata import (
    get_exam_item_by_id
)
from AIOralExamSystem.Exam.Examdata.judge_config_repository import (
    get_exam_judge_config_by_exam_id,
    get_exam_judge_config_by_exam_item,
    upsert_exam_judge_config,
    upsert_exam_report_model_config,
)
from AIOralExamSystem.Exam.QAserver import QAserver

from AIOralExamSystem.Exam.Examdata.report_template_repository import (
    get_exam_report_template,
    replace_exam_report_template,
)
from AIOralExamSystem.Exam.Examdata.preset_question_repository import (
    AI_CREATED_BY,
    create_preset_question,
    delete_preset_questions_by_exam_item_and_user,
)
from AIOralExamSystem.Agent.QuestionSetter import QuestionSetterAgent
from AIOralExamSystem.Graph.AIOralExamsetter import AIOralExamsetter
from AIOralExamSystem.Graph.template_content_loader import normalize_template_prompt
from Authentication.auth import get_current_user
from config import get_settings

class CourseCreateRequest(BaseModel):
    course_name: str
    description: str | None = None
    invite_code_valid_times: int = 2592000


class CourseUpdateRequest(BaseModel):
    course_name: str | None = None
    description: str | None = None


class CourseJoinApprovalRequest(BaseModel):
    course_id: str
    user_id: str


class CourseInviteCodeRequest(BaseModel):
    invite_code_valid_times: int


class ExamItemDimension(BaseModel):
    name: str
    score: float


class ExamItemCreateRequest(BaseModel):
    exam_item_name: str
    dimensions: List[ExamItemDimension]
    exam_available_valid_times: int
    description: str | None = None
    item_type: str | None = None
    need_code_repository: bool = False
    use_preset_questions: bool = False
    enable_report_analysis: bool = False
    report_total_score: float | None = None
    report_judge_rule: str | None = None
    judge_model_ids: List[str] | None = None
    setter_model_id: str | None = None
    main_judger_model_id: str | None = None
    report_judger_model_id: str | None = None


class ExamItemUpdateRequest(BaseModel):
    exam_item_name: str | None = None
    dimensions: List[ExamItemDimension] | None = None
    exam_available_valid_times: int | None = None
    description: str | None = None
    item_type: str | None = None
    need_code_repository: bool | None = None
    use_preset_questions: bool | None = None
    enable_report_analysis: bool | None = None
    report_total_score: float | None = None
    report_judge_rule: str | None = None
    judge_model_ids: List[str] | None = None
    setter_model_id: str | None = None
    main_judger_model_id: str | None = None
    report_judger_model_id: str | None = None


class ExamItemAvailabilityRequest(BaseModel):
    exam_available_valid_times: int


class GitRepositoryUploadRequest(BaseModel):
    course_id: str
    exam_id: str
    git_url: str | None = None
    git_branch: str | None = "main"
    reload: bool = False


class ExamReportTemplateModuleRequest(BaseModel):
    module_key: str
    template_body: str
    template_prompt: str
    provides_questions: bool
    sort_order: int = 0


class ExamReportTemplateInsertRequest(BaseModel):
    template_name: str
    modules: List[ExamReportTemplateModuleRequest]


class PresetQuestionCreateRequest(BaseModel):
    question_dimension: str
    question_content: str
    standard_answer: str | None = None
    score: float = 1.0
    sort_order: int | None = None


class PresetQuestionUpdateRequest(BaseModel):
    question_dimension: str | None = None
    question_content: str | None = None
    standard_answer: str | None = None
    score: float | None = None
    sort_order: int | None = None


class ExamSessionPresetUsageRequest(BaseModel):
    use_preset_questions: bool


class ReportScoreRequest(BaseModel):
    user_id: str | None = None
    exam_id: str | None = None
    prepare_questions: bool = True


def course_error_detail(code: str, message: str) -> dict:
    return {
        "code": code,
        "message": message,
    }


def raise_course_value_error(error: ValueError) -> None:
    message = str(error)
    error_map = {
        "COURSE_NAME_EXISTS": (400, "课程名称已存在"),
        "COURSE_NAME_REQUIRED": (400, "课程名称不能为空"),
        "COURSE_NOT_FOUND": (404, "课程不存在"),
        "COURSE_ALREADY_JOINED": (400, "用户已加入该课程"),
        "USER_NOT_FOUND": (404, "用户不存在"),
        "USER_ROLE_UNSUPPORTED": (400, "用户身份不支持加入课程"),
        "EXAM_ITEM_NAME_EXISTS": (400, "考试项名称已存在"),
        "EXAM_ITEM_NAME_REQUIRED": (400, "考试项名称不能为空"),
        "EXAM_ITEM_DIMENSIONS_REQUIRED": (400, "考试项维度不能为空"),
        "EXAM_ITEM_DIMENSION_NAME_REQUIRED": (400, "考试项维度名称不能为空"),
        "INVITE_CODE_VALID_TIMES_INVALID": (400, "邀请码有效时长必须在 1 到 2592000 秒之间"),
        "EXAM_AVAILABLE_VALID_TIMES_INVALID": (400, "考试可开启时长必须在 1 到 2592000 秒之间"),
    }
    error_map.update({
        "COURSE_DOCUMENT_SOURCE_REQUIRED": (400, "课程资料名称不能为空"),
        "COURSE_DOCUMENT_SOURCE_EXISTS": (400, "课程资料名称已存在"),
        "COURSE_DOCUMENT_FILE_REQUIRED": (400, "课程资料文件不能为空"),
        "COURSE_DOCUMENT_SOURCE_NOT_FOUND": (404, "课程资料不存在"),
    })
    error_map.update({
        "EXAM_ITEM_NOT_FOUND": (404, "考试项不存在"),
        "EXAM_SESSION_NOT_FOUND": (404, "考试记录不存在或已完成"),
        "PRESET_QUESTION_NOT_FOUND": (404, "预设题目不存在"),
        "PRESET_QUESTION_DIMENSION_REQUIRED": (400, "预设题目维度不能为空"),
        "PRESET_QUESTION_DIMENSION_INVALID": (400, "预设题目维度必须属于该考试项"),
        "PRESET_QUESTION_CONTENT_REQUIRED": (400, "预设题目内容不能为空"),
        "PRESET_QUESTION_SCORE_INVALID": (400, "预设题目分值不合法"),
        "PRESET_QUESTION_SORT_ORDER_INVALID": (400, "预设题目排序值不合法"),
        "PRESET_QUESTION_BLOCKS_INVALID": (400, "预设题目结构化内容必须是列表"),
    })
    error_map.update({
        "EXAM_MODEL_CONFIG_REQUIRED": (400, "考试模型配置不完整"),
        "JUDGE_MODEL_REQUIRED": (400, "评价模型不能为空"),
        "MODEL_ID_REQUIRED": (400, "模型 ID 不能为空"),
        "MODEL_NOT_FOUND": (404, "模型不存在或无权使用"),
    })
    error_map.update({
        "REPORT_TOTAL_SCORE_REQUIRED": (400, "报告分值不能为空且必须大于 0"),
        "REPORT_TOTAL_SCORE_INVALID": (400, "报告分值必须大于 0"),
        "REPORT_JUDGE_RULE_REQUIRED": (400, "报告评价方式不能为空"),
        "REPORT_ANALYSIS_DISABLED": (400, "当前考试项未启用报告分析"),
        "REPORT_SCORE_INVALID": (400, "报告得分无效"),
        "REPORT_MODEL_CONFIG_REQUIRED": (400, "?????????"),
    })
    if message in error_map:
        status_code, detail = error_map[message]
        raise HTTPException(
            status_code=status_code,
            detail=course_error_detail(message, detail),
        )
    raise HTTPException(status_code=400, detail=message)


def dimensions_to_scores(dimensions: List[ExamItemDimension] | None) -> Dict[str, float] | None:
    if dimensions is None:
        return None
    return {item.name: item.score for item in dimensions}


def has_core_exam_model_config(req) -> bool:
    return any([
        req.judge_model_ids is not None,
        req.setter_model_id is not None,
        req.main_judger_model_id is not None,
    ])


def has_report_model_config(req) -> bool:
    return getattr(req, "report_judger_model_id", None) is not None


async def save_exam_model_config(exam_item_id: str, req, current_user: dict) -> Dict[str, object] | None:
    has_core_config = has_core_exam_model_config(req)
    has_report_config = has_report_model_config(req)
    if not has_core_config and not has_report_config:
        return None
    user_id = current_user.get("uuid")
    if not user_id:
        raise ValueError("USER_NOT_FOUND")
    if has_core_config:
        if not req.judge_model_ids or not req.setter_model_id or not req.main_judger_model_id:
            raise ValueError("EXAM_MODEL_CONFIG_REQUIRED")
        flow_type = "single" if len(req.judge_model_ids) == 1 else "panel"
        return await upsert_exam_judge_config(
            exam_item_id=exam_item_id,
            created_by=user_id,
            scorer_model_ids=req.judge_model_ids,
            flow_type=flow_type,
            setter_model_id=req.setter_model_id,
            main_judger_model_id=req.main_judger_model_id,
            report_judger_model_id=req.report_judger_model_id,
        )
    return await upsert_exam_report_model_config(
        exam_item_id=exam_item_id,
        created_by=user_id,
        report_judger_model_id=req.report_judger_model_id,
    )


async def attach_exam_item_view_configs(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    async def attach_configs(item: Dict[str, object]) -> Dict[str, object]:
        exam_item_id = str(item.get("exam_item_id") or "").strip()
        if not exam_item_id:
            return item

        judge_config, report_template_config = await asyncio.gather(
            get_exam_judge_config_by_exam_item(exam_item_id, include_api_key=False),
            get_exam_report_template(exam_item_id),
        )
        enriched = dict(item)
        if judge_config is not None:
            enriched["judge_config"] = judge_config
        enriched["report_template_config"] = report_template_config
        return enriched

    return await asyncio.gather(*(attach_configs(item) for item in items))


def _failure(reason: str) -> dict:
    return {
        "success": False,
        "message": "上传失败",
        "reason": reason,
    }


def _parse_bool(value, default: bool = False) -> bool:
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes", "on"}


async def _parse_git_repository_request(request: Request) -> dict:
    content_type = request.headers.get("content-type", "").lower()
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload_file = form.get("file")
        if upload_file is not None and not getattr(upload_file, "filename", ""):
            upload_file = None
        return {
            "course_id": str(form.get("course_id") or ""),
            "exam_id": str(form.get("exam_id") or ""),
            "git_url": str(form.get("git_url") or "").strip() or None,
            "git_branch": str(form.get("git_branch") or "main").strip() or "main",
            "reload": _parse_bool(form.get("reload"), False),
            "file": upload_file,
        }

    req = GitRepositoryUploadRequest(**(await request.json()))
    return {
        "course_id": req.course_id,
        "exam_id": req.exam_id,
        "git_url": req.git_url.strip() if req.git_url else None,
        "git_branch": req.git_branch.strip() if req.git_branch else "main",
        "reload": req.reload,
        "file": None,
    }


def _git_tool_failed(result: dict) -> bool:
    return bool(result.get("mode") == "error" or result.get("error") or result.get("errors"))


def _git_tool_failure_reason(result: dict) -> str:
    reason = result.get("error") or result.get("errors")
    if isinstance(reason, list):
        return "; ".join(str(item) for item in reason)
    if reason:
        return str(reason)
    return "Git 仓库处理失败"


def _collect_files(root: Path) -> List[str]:
    if not root.exists() or not root.is_dir():
        return []
    return [
        str(path)
        for path in root.rglob("*")
        if path.is_file() and ".git" not in path.parts
    ]


def _remove_path(path: Path | None) -> None:
    if not path or not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()


def _move_path(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(source), str(target))


def _swap_repository_root(staging_root: Path, final_root: Path, backup_root: Path) -> bool:
    had_original = final_root.exists()
    if backup_root.exists():
        _remove_path(backup_root)
    if had_original:
        _move_path(final_root, backup_root)
    _move_path(staging_root, final_root)
    return had_original


def _restore_repository_root(final_root: Path, backup_root: Path, had_original: bool, final_root_swapped: bool) -> None:
    if final_root_swapped and final_root.exists():
        _remove_path(final_root)
    if had_original and backup_root.exists():
        _move_path(backup_root, final_root)


def _default_model_settings() -> dict:
    settings = get_settings()
    return {
        "model_name": settings.model_name,
        "model_url": settings.model_url,
        "model_api_key": settings.model_api_key,
    }


async def _report_model_settings_for_exam(exam_id: str) -> dict:
    config = await get_exam_judge_config_by_exam_id(exam_id, include_api_key=True)
    report_judger = config.get("report_judger") if isinstance(config, dict) else None
    if isinstance(report_judger, dict):
        runtime_settings = report_judger.get("runtime_model_settings")
        if (
            isinstance(runtime_settings, dict)
            and runtime_settings.get("model_name")
            and runtime_settings.get("model_url")
            and runtime_settings.get("model_api_key")
        ):
            return dict(runtime_settings)
    return _default_model_settings()


async def _run_report_analysis_after_repository_upload(
    user_id: str,
    course_id: str,
    exam_id: str,
    repository_root: Path,
    template_module: dict | None = None,
    setter_cls=AIOralExamsetter,
) -> dict:
    repository_root = Path(repository_root).expanduser().resolve(strict=False)
    if not repository_root.exists() or not repository_root.is_dir():
        return {
            "ok": False,
            "flag": "REPOSITORY_ROOT_NOT_FOUND",
            "error_message": f"仓库目录不存在，无法进行报告评价: {repository_root}",
        }

    settings = get_settings()
    setter = setter_cls(
        model_settings=await _report_model_settings_for_exam(exam_id),
        thinking=False,
        response_format=True,
        temperature=0,
        mineru_api_key=settings.mineru_api_key,
    )
    return await setter.execute(
        user_requirement="请基于当前代码仓库完成报告评价，并初始化口试问题。",
        folder_path=str(repository_root),
        user_name=user_id,
        course_id=course_id,
        exam_id=exam_id,
        template_modules=(
            list(template_module.get("modules") or [])
            if isinstance(template_module, dict)
            and template_module.get("configured")
            else None
        ),
    )


async def _generate_questions_from_core_module_records(
    exam_id: str,
    repository_root: Path,
    core_module_records: dict,
) -> dict:
    if not isinstance(core_module_records, dict) or not core_module_records:
        return {
            "ok": True,
            "flag": "NO_CORE_MODULE_RECORDS",
            "question_count": 0,
            "questions": {},
        }

    agent = QuestionSetterAgent(
        model_settings=await _report_model_settings_for_exam(exam_id),
        document_scope=str(repository_root),
        thinking=False,
        response_format=True,
        temperature=0.2,
    )
    async def generate_one_question(key: str, record: dict) -> tuple[str, dict]:
        module_key = str(key)
        try:
            module = record.get("module") if isinstance(record, dict) else {}
            if not isinstance(module, dict):
                return module_key, {
                    "ok": False,
                    "flag": "CORE_MODULE_RECORD_INVALID",
                    "module_name": module_key,
                    "error_message": "core module record must contain a module object",
                }
            module_name = str(module.get("module_name") or key or "").strip()
            document_refs = module.get("document_refs") or []
            result = await agent.execute(
                module_name=module_name,
                module_content=module,
                document_refs=document_refs if isinstance(document_refs, list) else [],
            )
            return module_key, result
        except Exception as exc:
            return module_key, {
                "ok": False,
                "flag": "QUESTION_SET_FAILED",
                "module_name": module_key,
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
            }

    question_pairs = await asyncio.gather(
        *(generate_one_question(key, record) for key, record in core_module_records.items())
    )
    questions = dict(question_pairs)

    return {
        "ok": True,
        "flag": "CORE_MODULE_QUESTIONS_GENERATED",
        "question_count": len(questions),
        "questions": questions,
    }



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
    questions = [
        item
        for item in (_normalize_a_report_question_item(question) for question in raw_questions)
        if item.get("Question")
    ]
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
    if not questions:
        return {
            "ok": True,
            "flag": "NO_A_MODE_QUESTIONS",
            "deleted_count": 0,
            "saved_count": 0,
            "saved_questions": [],
        }

    exam_item = await get_exam_item_by_id(exam_item_id)
    dimension_names = exam_item.get("dimension_names") if isinstance(exam_item, dict) else []
    if not isinstance(dimension_names, list):
        dimension_names = []
    dimension_names = [str(item or "").strip() for item in dimension_names if str(item or "").strip()]

    deleted_count = await delete_preset_questions_by_exam_item_and_user(
        course_id,
        exam_item_id,
        user_id,
    )
    saved_questions = []
    savable_questions = questions[:len(dimension_names)] if dimension_names else questions
    for index, question in enumerate(savable_questions, start=1):
        dimension = str(question.get("dimension") or "").strip()
        if dimension_names and dimension not in dimension_names and index <= len(dimension_names):
            dimension = dimension_names[index - 1]
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
    for module_key, question_set in raw_questions.items():
        if not isinstance(question_set, dict) or not question_set.get("ok", True):
            continue
        module_name = str(question_set.get("module_name") or module_key or "").strip()
        if not module_name:
            continue
        items = question_set.get("questions") or []
        if not isinstance(items, list):
            continue
        questions = [
            item
            for item in (_normalize_c_core_module_question_item(item) for item in items[:3])
            if item.get("question")
        ]
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

    for module_key, question_set in questions_by_module.items():
        if not isinstance(question_set, dict) or not question_set.get("ok", True):
            continue
        module_name = str(question_set.get("module_name") or module_key or "").strip()
        if not module_name:
            continue

        items = question_set.get("questions") or []
        if not isinstance(items, list):
            continue
        for item in items[:3]:
            normalized = _normalize_c_core_module_question_item(item)
            if not normalized.get("question"):
                continue
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


async def insert_exam_report_template(
    exam_item_id: str,
    req: ExamReportTemplateInsertRequest,
):

    try:
        result = await replace_exam_report_template(
            exam_item_id=exam_item_id,
            template_name=req.template_name,
            modules=[
                {
                    "module_key": module.module_key,
                    "template_body": module.template_body,
                    "template_prompt": normalize_template_prompt(module.template_prompt),
                    "provides_questions": module.provides_questions,
                    "sort_order": module.sort_order,
                }
                for module in req.modules
            ],
        )
    except ValueError as exc:
        error_code = str(exc)
        status_code = 404 if error_code == "EXAM_ITEM_NOT_FOUND" else 400
        raise HTTPException(
            status_code=status_code,
            detail=course_error_detail(error_code, error_code),
        ) from exc

    return {
        "success": True,
        "message": "Report template inserted",
        "data": result,
    }

def register_course_routes(app, args):
    """Register course routes."""
    @app.post("/courses")
    async def create_course(req: CourseCreateRequest, current_user: dict = Depends(get_current_user)):
        try:
            result = await QAserver.manage_course(
                current_user=current_user,
                action="create",
                course_name=req.course_name,
                description=req.description,
                invite_code_valid_times=req.invite_code_valid_times,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("创建课程失败")
            raise HTTPException(status_code=500, detail=f"创建课程失败: {str(e)}")
        return {
            "success": True,
            "message": "课程创建成功",
            "data": result,
        }

    @app.get("/courses")
    async def list_courses(current_user: dict = Depends(get_current_user)):
        try:
            courses = await QAserver.manage_course(current_user, action="list")
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except Exception as e:
            logger.error("查询课程失败")
            raise HTTPException(status_code=500, detail=f"查询课程失败: {str(e)}")
        return {
            "success": True,
            "data": courses,
        }

    @app.get("/courses/by_invite_code/{invite_code}")
    async def get_course_by_invite_code(
        invite_code: str,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            course = await QAserver.get_course_by_invite_code(invite_code)
        except Exception as e:
            logger.error("根据邀请码查询课程失败")
            raise HTTPException(status_code=500, detail=f"根据邀请码查询课程失败: {str(e)}")
        if not course:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("COURSE_INVITE_CODE_NOT_FOUND", "邀请码无效或课程不存在"),
            )
        return {
            "success": True,
            "data": course,
        }

    @app.put("/courses/{course_id}/invite_code")
    async def reset_course_invite_code(
        course_id: str,
        req: CourseInviteCodeRequest,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            course = await QAserver.manage_course(
                current_user=current_user,
                action="reset_invite_code",
                course_id=course_id,
                invite_code_valid_times=req.invite_code_valid_times,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("重置课程邀请码失败")
            raise HTTPException(status_code=500, detail=f"重置课程邀请码失败: {str(e)}")
        if not course:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("COURSE_NOT_FOUND", "课程不存在"),
            )
        return {
            "success": True,
            "message": "课程邀请码重置成功",
            "data": course,
        }

    @app.post("/courses/{course_id}/join_requests")
    async def create_course_join_request(
        course_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            result = await QAserver.manage_course_join_request(
                current_user=current_user,
                action="create",
                course_id=course_id,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("创建课程加入申请失败")
            raise HTTPException(status_code=500, detail=f"创建课程加入申请失败: {str(e)}")
        return {
            "success": True,
            "message": "课程加入申请创建成功",
            "data": result,
        }

    @app.get("/courses/{course_id}/join_requests")
    async def list_course_join_requests(
        course_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            requests = await QAserver.manage_course_join_request(
                current_user=current_user,
                action="list",
                course_id=course_id,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("查询课程加入申请失败")
            raise HTTPException(status_code=500, detail=f"查询课程加入申请失败: {str(e)}")
        return {
            "success": True,
            "data": requests,
        }

    @app.post("/courses/join_requests/approve")
    async def approve_course_join_request(
        req: CourseJoinApprovalRequest,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            result = await QAserver.manage_course_join_request(
                current_user=current_user,
                action="approve",
                course_id=req.course_id,
                target_user_id=req.user_id,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("审批课程加入申请失败")
            raise HTTPException(status_code=500, detail=f"审批课程加入申请失败: {str(e)}")
        if not result:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("JOIN_REQUEST_NOT_FOUND", "加入申请不存在"),
            )
        return {
            "success": True,
            "message": "课程加入申请已通过",
            "data": result,
        }

    @app.put("/courses/{course_id}")
    async def update_course(
        course_id: str,
        req: CourseUpdateRequest,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            updated = await QAserver.manage_course(
                current_user=current_user,
                action="update",
                course_id=course_id,
                course_name=req.course_name,
                description=req.description,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("更新课程失败")
            raise HTTPException(status_code=500, detail=f"更新课程失败: {str(e)}")
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("COURSE_NOT_FOUND", "课程不存在"),
            )
        return {
            "success": True,
            "message": "课程更新成功",
            "data": updated,
        }

    @app.delete("/courses/{course_id}")
    async def delete_course(course_id: str, current_user: dict = Depends(get_current_user)):
        try:
            deleted = await QAserver.manage_course(current_user, action="delete", course_id=course_id)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except Exception as e:
            logger.error("删除课程失败")
            raise HTTPException(status_code=500, detail=f"删除课程失败: {str(e)}")
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("COURSE_NOT_FOUND", "课程不存在"),
            )
        return {
            "success": True,
            "message": "课程删除成功",
        }


    return app
