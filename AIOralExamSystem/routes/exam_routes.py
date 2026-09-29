import asyncio
import shutil
import uuid
from pathlib import Path
from typing import Dict, List, Literal

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse
from loguru import logger
from pydantic import BaseModel, ConfigDict, Field

from AIOralExamSystem.Exam.Examdata.exam_repository import (
    get_exam_session_by_exam_id,
)
from AIOralExamSystem.Exam.Examdata.final_review_repository import get_final_review
from AIOralExamSystem.Exam.Examdata import (
    course_document_source_is_referenced,
    exam_item_id_exists,
    get_exam_item_readiness,
    get_exam_item_by_id,
    get_exam_item_for_edit,
    get_exam_item_course_document_sources,
    is_course_owner,
)
from AIOralExamSystem.Exam.Examdata.judge_config_repository import (
    get_exam_judge_config_by_exam_id,
    get_exam_judge_config_by_exam_item,
    upsert_exam_judge_config,
    upsert_exam_optional_agent_models,
    upsert_exam_report_model_config,
)
from AIOralExamSystem.Exam.QAserver import QAserver
from AIOralExamSystem.Exam.report_storage import (
    resolve_exam_report_path,
    safe_report_component,
)
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
from AIOralExamSystem.Graph.template_content_loader import (
    load_local_report_template,
    normalize_template_prompt,
)
from AIOralExamSystem.Tool.rag.data_tool import InsertTool, SearchTool
from Authentication.auth import get_current_user
from AIOralExamSystem.utils.monitor import GlobalMonitor


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


class ExamItemIdCheckRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam_item_id: str


class ExamItemCreateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    exam_item_id: str
    exam_item_name: str
    dimensions: List[ExamItemDimension] | None = None
    exam_available_valid_times: int
    description: str | None = None
    item_type: str | None = None
    need_code_repository: bool = False
    use_preset_questions: bool = False
    enable_report_analysis: bool = False
    report_total_score: float | None = None
    report_judge_rule: str | None = None


class ExamItemUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

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


class ExamAgentBindingRequest(BaseModel):
    judge_model_ids: List[str] | None = None
    setter_model_id: str | None = None
    main_judger_model_id: str | None = None
    report_judger_model_id: str | None = None
    mineru_model_id: str | None = None
    embedding_model_id: str | None = None
    version: int | None = None


class CourseDocumentSearchRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    query: str
    top_n: int = Field(default=10, ge=1, le=100)


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
        "EXAM_ITEM_ID_EXISTS": (409, "考试项 ID 已存在"),
        "EXAM_ITEM_ID_INVALID": (400, "考试项 ID 必须是 32 位小写十六进制字符串"),
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
        "EXAM_IN_PROGRESS": (409, "A student is taking this exam or has uploaded a repository"),
        "EXAM_VERSION_CONFLICT": (409, "Exam version conflict"),
        "EXAM_UNAVAILABLE": (409, "Exam is unavailable"),
        "SETTER_MODEL_REQUIRED": (400, "Setter model is required"),
        "MAIN_JUDGER_MODEL_REQUIRED": (400, "Main judger model is required"),
        "PRESET_QUESTION_REQUIRED": (400, "At least one preset question is required"),
        "EXAM_ITEM_DIMENSION_SCORE_INVALID": (400, "Dimension scores must be positive"),
        "EXAM_ITEM_TOTAL_SCORE_INVALID": (400, "Exam total score must be positive"),
        "MINERU_MODEL_NOT_CONFIGURED": (400, "当前考试项未配置 MinerU 模型"),
        "MINERU_TOKEN_NOT_CONFIGURED": (400, "MinerU 模型未配置 API Token"),
        "EMBEDDING_MODEL_NOT_CONFIGURED": (400, "当前考试项未配置 embedding 模型"),
    })
    error_map.update({
        "REPORT_TOTAL_SCORE_REQUIRED": (400, "报告分值不能为空且必须大于 0"),
        "REPORT_TOTAL_SCORE_INVALID": (400, "报告分值必须大于 0"),
        "REPORT_JUDGE_RULE_REQUIRED": (400, "报告评价方式不能为空"),
        "REPORT_ANALYSIS_DISABLED": (400, "当前考试项未启用报告分析"),
        "REPORT_SCORE_INVALID": (400, "报告得分无效"),
        "REPORT_MODEL_CONFIG_REQUIRED": (400, "报告评审模型不能为空"),
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
    return bool(req.model_fields_set & {
        "judge_model_ids",
        "setter_model_id",
        "main_judger_model_id",
    })


def has_report_model_config(req) -> bool:
    return getattr(req, "report_judger_model_id", None) is not None


def has_rag_model_config(req) -> bool:
    return bool(req.model_fields_set & {"mineru_model_id", "embedding_model_id"})


async def save_exam_model_config(exam_item_id: str, req, current_user: dict) -> Dict[str, object] | None:
    has_core_config = has_core_exam_model_config(req)
    has_report_config = has_report_model_config(req)
    has_rag_config = has_rag_model_config(req)
    if not has_core_config and not has_report_config and not has_rag_config:
        return None
    user_id = current_user.get("uuid")
    if not user_id:
        raise ValueError("USER_NOT_FOUND")
    if has_core_config and (
        not req.judge_model_ids
        or not req.setter_model_id
        or not req.main_judger_model_id
    ):
        raise ValueError("EXAM_MODEL_CONFIG_REQUIRED")
    if has_report_config and not str(req.report_judger_model_id).strip():
        raise ValueError("REPORT_MODEL_CONFIG_REQUIRED")

    optional_models = {
        role: getattr(req, field)
        for field, role in (
            ("mineru_model_id", "mineru"),
            ("embedding_model_id", "embedding"),
        )
        if field in req.model_fields_set
    }
    if any(
        model_id is not None and not str(model_id).strip()
        for model_id in optional_models.values()
    ):
        raise ValueError("MODEL_ID_REQUIRED")

    if has_core_config:
        flow_type = "single" if len(req.judge_model_ids) == 1 else "panel"
        await upsert_exam_judge_config(
            exam_item_id=exam_item_id,
            created_by=user_id,
            scorer_model_ids=req.judge_model_ids,
            flow_type=flow_type,
            setter_model_id=req.setter_model_id,
            main_judger_model_id=req.main_judger_model_id,
            report_judger_model_id=req.report_judger_model_id if has_report_config else None,
            mineru_model_id=optional_models.get("mineru"),
            embedding_model_id=optional_models.get("embedding"),
            clear_optional_roles=[
                role for role, model_id in optional_models.items() if model_id is None
            ],
        )
    else:
        if has_report_config:
            await upsert_exam_report_model_config(
                exam_item_id=exam_item_id,
                created_by=user_id,
                report_judger_model_id=req.report_judger_model_id,
            )
        if has_rag_config:
            await upsert_exam_optional_agent_models(exam_item_id, user_id, optional_models)

    config = await get_exam_judge_config_by_exam_item(exam_item_id, include_api_key=False)
    return config if config is not None else {}


async def _rag_model_settings_for_exam_item(
    exam_item_id: str, require_mineru: bool = False,
) -> dict:
    config = await get_exam_judge_config_by_exam_item(
        exam_item_id,
        include_api_key=True,
    )
    config = config if isinstance(config, dict) else {}

    mineru_agent = config.get("mineru")
    embedding_agent = config.get("embedding")
    mineru_settings = (
        mineru_agent.get("runtime_model_settings")
        if isinstance(mineru_agent, dict)
        else None
    )
    embedding_settings = (
        embedding_agent.get("runtime_model_settings")
        if isinstance(embedding_agent, dict)
        else None
    )
    if require_mineru and not isinstance(mineru_settings, dict):
        raise ValueError("MINERU_MODEL_NOT_CONFIGURED")
    if require_mineru and not str(mineru_settings.get("model_api_key") or "").strip():
        raise ValueError("MINERU_TOKEN_NOT_CONFIGURED")
    if not isinstance(embedding_settings, dict):
        raise ValueError("EMBEDDING_MODEL_NOT_CONFIGURED")
    for key in ("model_name", "model_url", "model_api_key"):
        if not str(embedding_settings.get(key) or "").strip():
            raise ValueError(f"EMBEDDING_{key.upper()}_NOT_CONFIGURED")
    return {
        "mineru": dict(mineru_settings) if require_mineru else None,
        "embedding": dict(embedding_settings),
        "embedding_model_id": str((embedding_agent.get("model") or {}).get("model_id") or ""),
    }


async def attach_exam_item_view_configs(items: List[Dict[str, object]]) -> List[Dict[str, object]]:
    def agent_model_id(agent: Dict[str, object] | None) -> str | None:
        if not isinstance(agent, dict):
            return None
        model = agent.get("model")
        if not isinstance(model, dict):
            return None
        model_id = model.get("model_id")
        return str(model_id) if model_id else None

    async def attach_configs(item: Dict[str, object]) -> Dict[str, object]:
        exam_item_id = str(item.get("exam_item_id") or "").strip()
        if not exam_item_id:
            return item

        judge_config = await get_exam_judge_config_by_exam_item(
            exam_item_id,
            include_api_key=False,
        )
        enriched = dict(item)
        enriched["judge_config"] = judge_config
        enriched["judge_model_ids"] = []
        enriched["setter_model_id"] = None
        enriched["main_judger_model_id"] = None
        enriched["report_judger_model_id"] = None
        enriched["mineru_model_id"] = None
        enriched["embedding_model_id"] = None

        if isinstance(judge_config, dict):
            scorers = judge_config.get("scorers") or []
            enriched["judge_model_ids"] = [
                model_id
                for model_id in (agent_model_id(scorer) for scorer in scorers)
                if model_id
            ]
            enriched["setter_model_id"] = agent_model_id(judge_config.get("setter"))
            enriched["main_judger_model_id"] = agent_model_id(judge_config.get("main_judger"))
            enriched["report_judger_model_id"] = agent_model_id(judge_config.get("report_judger"))
            enriched["mineru_model_id"] = agent_model_id(judge_config.get("mineru"))
            enriched["embedding_model_id"] = agent_model_id(judge_config.get("embedding"))

        return enriched

    return await asyncio.gather(*(attach_configs(item) for item in items))


def _remove_path(path: Path | None) -> None:
    if not path or not path.exists():
        return
    if path.is_dir():
        shutil.rmtree(path)
    else:
        path.unlink()



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
        raise_course_value_error(exc)

    return {
        "success": True,
        "message": "Report template inserted",
        "data": result,
    }

async def _cleanup_unreferenced_document_sources(course_id: str, sources: List[str]) -> List[dict]:
    errors = []
    for source in dict.fromkeys(sources):
        try:
            if await course_document_source_is_referenced(course_id, source):
                continue
            InsertTool("insert_tool").delete_course_documents_by_source(course_id, source)
        except Exception as error:
            errors.append({"source": source, "error": str(error)})
            logger.error("Failed to clean exam document source: %s", source)
    return errors


def register_exam_routes(app, args):
    """Register exam routes."""
    @app.get("/courses/{course_id}/exam_items/{exam_item_id}/readiness")
    async def exam_item_readiness_route(
        course_id: str,
        exam_item_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        user_id = str(current_user.get("uuid") or "")
        if not user_id or not await is_course_owner(user_id, course_id):
            raise HTTPException(
                status_code=403,
                detail=course_error_detail("FORBIDDEN", "Not allowed to inspect exam readiness"),
            )
        try:
            readiness = await get_exam_item_readiness(course_id, exam_item_id, user_id)
        except ValueError as error:
            raise_course_value_error(error)
        return {"success": True, "data": readiness}

    @app.get("/courses/{course_id}/exam_items/{exam_item_id}")
    async def get_exam_item_detail_route(
        course_id: str,
        exam_item_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        user_id = str(current_user.get("uuid") or "")
        if not user_id or not await is_course_owner(user_id, course_id):
            raise HTTPException(
                status_code=403,
                detail=course_error_detail("FORBIDDEN", "Not allowed to view this exam"),
            )
        item = await get_exam_item_for_edit(course_id, exam_item_id)
        if item is None:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("EXAM_ITEM_NOT_FOUND", "考试项不存在"),
            )
        judge_config, template, readiness = await asyncio.gather(
            get_exam_judge_config_by_exam_item(exam_item_id, include_api_key=False),
            get_exam_report_template(exam_item_id),
            get_exam_item_readiness(course_id, exam_item_id, user_id),
        )
        return {
            "success": True,
            "data": {
                "exam_item": item,
                "judge_config": judge_config,
                "report_template": template,
                "readiness": readiness,
            },
        }

    @app.put("/courses/{course_id}/exam_items/{exam_item_id}/agents")
    async def bind_exam_item_agents_route(
        course_id: str,
        exam_item_id: str,
        req: ExamAgentBindingRequest,
        current_user: dict = Depends(get_current_user),
    ):
        user_id = str(current_user.get("uuid") or "")
        if not user_id or not await is_course_owner(user_id, course_id):
            raise HTTPException(
                status_code=403,
                detail=course_error_detail("FORBIDDEN", "Not allowed to configure exam agents"),
            )
        try:
            readiness = await get_exam_item_readiness(course_id, exam_item_id, user_id)
            if req.version is not None and req.version != readiness["version"]:
                raise ValueError("EXAM_VERSION_CONFLICT")
            config = await save_exam_model_config(exam_item_id, req, current_user)
            if config is None:
                raise ValueError("EXAM_MODEL_CONFIG_REQUIRED")
            readiness = await get_exam_item_readiness(course_id, exam_item_id, user_id)
        except ValueError as error:
            raise_course_value_error(error)
        return {
            "success": True,
            "message": "Exam agent models bound",
            "data": {"judge_config": config, "readiness": readiness},
        }

    @app.get("/report_templates/local-default")
    async def get_local_default_report_template_route(
        template_type: Literal["git", "general"],
        current_user: dict = Depends(get_current_user),
    ):
        try:
            result = load_local_report_template(template_type)
        except FileNotFoundError as exc:
            raise HTTPException(
                status_code=404,
                detail={
                    "code": "LOCAL_REPORT_TEMPLATE_NOT_FOUND",
                    "message": f"未配置 {template_type} 类型的本地预设模板",
                },
            ) from exc
        except Exception as exc:
            logger.error("读取本地默认报告模板失败")
            raise HTTPException(
                status_code=500,
                detail=f"读取本地默认报告模板失败: {str(exc)}",
            ) from exc
        return {
            "success": True,
            "data": result,
        }

    @app.post(
        "/exam_items/{exam_item_id}/report_template",
        status_code=201,
    )
    async def insert_exam_report_template_route(
        exam_item_id: str,
        req: ExamReportTemplateInsertRequest,
    ):
        return await insert_exam_report_template(
            exam_item_id=exam_item_id,
            req=req,
        )

    @app.get("/exam_items/{exam_item_id}/report_template")
    async def get_exam_report_template_route(
        exam_item_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            exam_item = await get_exam_item_by_id(exam_item_id)
            if not exam_item:
                raise ValueError("EXAM_ITEM_NOT_FOUND")
            course_id = str(exam_item.get("course_id") or "").strip()
            if not course_id or not await QAserver.can_view_course(
                current_user, course_id,
            ):
                raise PermissionError("No permission to view this exam report template")
            result = await get_exam_report_template(exam_item_id)
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail=course_error_detail("FORBIDDEN", str(exc)),
            ) from exc
        except ValueError as exc:
            error_code = str(exc)
            status_code = 404 if error_code == "EXAM_ITEM_NOT_FOUND" else 400
            raise HTTPException(
                status_code=status_code,
                detail=course_error_detail(error_code, error_code),
            ) from exc
        except Exception as exc:
            logger.error("查询考试报告模板失败")
            raise HTTPException(status_code=500, detail=f"查询考试报告模板失败: {str(exc)}")
        return {
            "success": True,
            "data": result,
        }

    @app.get("/exam_sessions/{exam_id}/final_review")
    async def read_final_review(exam_id: str, current_user: dict = Depends(get_current_user)):
        try:
            session = await get_exam_session_by_exam_id(exam_id)
            if not session:
                raise HTTPException(404, detail=course_error_detail("EXAM_SESSION_NOT_FOUND", "考试记录不存在"))
            user_id, include_all_users = await QAserver.resolve_course_exam_query_scope(
                current_user, str(session.get("course_id") or ""),
            )
            if not include_all_users and str(user_id or "") != str(session.get("user_id") or ""):
                raise PermissionError("无权查看该考试评审")
            report = await get_final_review(exam_id)
            if report is None:
                raise HTTPException(409, detail=course_error_detail("FINAL_REVIEW_NOT_READY", "最终评审尚未就绪"))
            return {"success": True, "data": {"exam_id": exam_id, "html": report["html"]}}
        except PermissionError as exc:
            raise HTTPException(403, detail=course_error_detail("REPORT_ACCESS_DENIED", str(exc))) from exc
        except HTTPException:
            raise
        except Exception as exc:
            logger.error("读取最终评审失败")
            raise HTTPException(500, detail=course_error_detail("FINAL_REVIEW_READ_FAILED", "读取最终评审失败")) from exc

    @app.get("/exam_sessions/{exam_id}/report/export")
    async def export_exam_report(
        exam_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            exam_session = await get_exam_session_by_exam_id(exam_id)
            if not exam_session:
                raise HTTPException(
                    status_code=404,
                    detail=course_error_detail(
                        "EXAM_SESSION_NOT_FOUND",
                        "考试记录不存在",
                    ),
                )

            course_id = str(exam_session.get("course_id") or "").strip()
            session_user_id = str(exam_session.get("user_id") or "").strip()
            user_id, include_all_users = await QAserver.resolve_course_exam_query_scope(
                current_user,
                course_id,
            )
            if not include_all_users and str(user_id or "") != session_user_id:
                raise PermissionError("无权下载该考试报告")

            report_path = resolve_exam_report_path(course_id, exam_id)
            if not report_path.is_file():
                raise HTTPException(
                    status_code=404,
                    detail=course_error_detail(
                        "REPORT_FILE_NOT_FOUND",
                        "考试报告尚未生成或文件不存在",
                    ),
                )
        except HTTPException:
            raise
        except PermissionError as exc:
            raise HTTPException(
                status_code=403,
                detail=course_error_detail("REPORT_ACCESS_DENIED", str(exc)),
            ) from exc
        except ValueError as exc:
            if str(exc) == "REPORT_PATH_INVALID":
                raise HTTPException(
                    status_code=404,
                    detail=course_error_detail(
                        "REPORT_FILE_NOT_FOUND",
                        "考试报告文件不存在",
                    ),
                ) from exc
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.error("导出考试报告失败")
            raise HTTPException(
                status_code=500,
                detail=course_error_detail(
                    "REPORT_EXPORT_FAILED",
                    "考试报告导出失败",
                ),
            ) from exc

        download_exam_id = safe_report_component(exam_id, "exam")
        return FileResponse(
            path=str(report_path),
            media_type="text/markdown; charset=utf-8",
            filename=f"exam-report-{download_exam_id}.md",
            headers={
                "Cache-Control": "private, no-store",
                "X-Content-Type-Options": "nosniff",
            },
        )

    @app.get("/exam_history/{course_id}")
    async def exam_history(
        course_id: str,
        exam_item_id: str | None = None,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            history = await QAserver.get_exam_history(
                current_user,
                course_id,
                exam_item_id=exam_item_id,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error("查询考试历史失败")
            raise HTTPException(status_code=500, detail=f"查询考试历史失败: {str(e)}")
        return {
            "success": True,
            "data": history,
        }

    @app.get("/exam_items/{exam_item_id}/questions")
    async def exam_item_questions(exam_item_id: str, current_user: dict = Depends(get_current_user)):
        try:
            questions = await QAserver.get_exam_questions_by_exam_item(current_user, exam_item_id)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("查询考试题目失败")
            raise HTTPException(status_code=500, detail=f"查询考试题目失败: {str(e)}")
        return {
            "success": True,
            "data": questions,
        }

    @app.get("/exam_record")
    async def exam_record(exam_id: str, current_user: dict = Depends(get_current_user)):
        try:
            records = await QAserver.get_exam_record(current_user, exam_id)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=str(e))
        except ValueError as e:
            raise HTTPException(status_code=400, detail=str(e))
        except Exception as e:
            logger.error("查询考试记录失败")
            raise HTTPException(status_code=500, detail=f"查询考试记录失败: {str(e)}")
        return {
            "success": True,
            "data": records,
        }

    @app.post("/courses/{course_id}/exam_items/check-id")
    async def check_exam_item_id_route(
        course_id: str,
        req: ExamItemIdCheckRequest,
        current_user: dict = Depends(get_current_user),
    ):
        user_id = str(current_user.get("uuid") or "")
        if not user_id or not await is_course_owner(user_id, course_id):
            raise HTTPException(
                status_code=403,
                detail=course_error_detail("FORBIDDEN", "只有课程主负责老师可以检查考试项 ID"),
            )
        exam_item_id = str(req.exam_item_id)
        try:
            exists = await exam_item_id_exists(exam_item_id)
        except ValueError as error:
            raise_course_value_error(error)
        return {
            "success": True,
            "data": {
                "exam_item_id": exam_item_id,
                "exists": exists,
                "available": not exists,
            },
        }

    @app.post("/courses/{course_id}/exam_items")
    async def create_exam_item(
        course_id: str,
        req: ExamItemCreateRequest,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            result = await QAserver.manage_exam_item(
                current_user=current_user,
                action="create",
                course_id=course_id,
                exam_item_id=str(req.exam_item_id),
                exam_item_name=req.exam_item_name,
                dimension_scores=dimensions_to_scores(req.dimensions) or {},
                exam_available_valid_times=req.exam_available_valid_times,
                description=req.description,
                item_type=req.item_type,
                need_code_repository=req.need_code_repository,
                use_preset_questions=req.use_preset_questions,
                enable_report_analysis=req.enable_report_analysis,
                report_total_score=req.report_total_score,
                report_judge_rule=req.report_judge_rule,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("创建考试项失败")
            raise HTTPException(status_code=500, detail=f"创建考试项失败: {str(e)}")
        return {
            "success": True,
            "message": "考试项创建成功",
            "data": result,
        }

    @app.get("/courses/{course_id}/exam_items")
    async def list_exam_items(course_id: str, current_user: dict = Depends(get_current_user)):
        try:
            items = await QAserver.manage_exam_item(current_user, action="list", course_id=course_id)
            items = await attach_exam_item_view_configs(items)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except Exception as e:
            logger.error("查询考试项失败")
            raise HTTPException(status_code=500, detail=f"查询考试项失败: {str(e)}")
        return {
            "success": True,
            "data": items,
        }

    @app.post("/courses/{course_id}/exam_items/{exam_item_id}/course_documents/search")
    async def search_course_documents(
        course_id: str,
        exam_item_id: str,
        request: CourseDocumentSearchRequest,
        current_user: dict = Depends(get_current_user),
    ):
        query = request.query.strip()
        if not query:
            raise HTTPException(status_code=400, detail="query is required")
        user_id = current_user.get("uuid")
        if not user_id or not await is_course_owner(str(user_id), course_id):
            raise HTTPException(status_code=403, detail="Only the course owner can test document search")
        sources = await get_exam_item_course_document_sources(course_id, exam_item_id)

        search_tool = SearchTool("course_document_search")
        try:
            hits = await search_tool.search_top_documents(
                query=query,
                sources=sources,
                course_id=course_id,
                top_n=request.top_n,
            )
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except Exception as exc:
            logger.exception("Course document search failed")
            raise HTTPException(status_code=502, detail="Document search failed") from exc

        return {
            "success": True,
            "data": {
                "course_id": course_id,
                "exam_item_id": exam_item_id,
                "sources": sources,
                "query": query,
                "top_n": request.top_n,
                "count": len(hits),
                "documents": [
                    {
                        "rank": rank,
                        "document_id": hit["id"],
                        "document_name": hit["source"],
                        "chunk_order": hit["chunk_order"],
                        "content": hit["content"],
                        "rank_score": hit["rank_score"],
                        "semantic_score": hit["semantic_score"],
                    }
                    for rank, hit in enumerate(hits, start=1)
                ],
            },
        }

    @app.post("/courses/{course_id}/exam_items/{exam_item_id}/course_documents")
    async def upload_course_documents(
        course_id: str,
        exam_item_id: str,
        document_name: str = Form(...),
        files: List[UploadFile] = File(...),
        current_user: dict = Depends(get_current_user),
    ):
        document_name = str(document_name or "").strip()
        upload_batch_id = str(uuid.uuid4())
        upload_work_dir = Path("updateFile") / "course_documents" / upload_batch_id
        insert_tool = None
        insert_attempted = False

        try:
            if not document_name:
                raise ValueError("COURSE_DOCUMENT_SOURCE_REQUIRED")
            user_id = current_user.get("uuid")
            if not user_id or not await is_course_owner(str(user_id), course_id):
                raise PermissionError("只有课程主负责老师可以上传课程资料")
            existing_sources = await get_exam_item_course_document_sources(course_id, exam_item_id)
            if document_name in existing_sources:
                raise ValueError("COURSE_DOCUMENT_SOURCE_EXISTS")
            if not files:
                raise ValueError("COURSE_DOCUMENT_FILE_REQUIRED")

            upload_work_dir.mkdir(parents=True, exist_ok=True)
            file_paths = []
            for upload_file in files:
                file_name = Path(upload_file.filename or "").name
                if not file_name:
                    continue
                file_location = upload_work_dir / file_name
                with open(file_location, "wb") as file_object:
                    shutil.copyfileobj(upload_file.file, file_object)
                file_paths.append(str(file_location))
            if not file_paths:
                raise ValueError("COURSE_DOCUMENT_FILE_REQUIRED")

            monitor = GlobalMonitor()
            monitor.start()
            requires_mineru = any(
                Path(path).suffix.lower() in {".pdf", ".doc", ".docx"}
                for path in file_paths
            )
            rag_settings = await _rag_model_settings_for_exam_item(
                exam_item_id, require_mineru=requires_mineru,
            )
            insert_tool = InsertTool(
                "insert_tool",
                mineru_settings=rag_settings["mineru"],
                embedding_settings=rag_settings["embedding"],
                embedding_model_id=rag_settings["embedding_model_id"],
            )
            insert_attempted = True
            insert_result = await insert_tool.execute(
                data=file_paths,
                source=document_name,
                type="file",
                course_id=course_id,
                exam_id=None,
                work_dir=str(upload_work_dir),
                reload=False,
                upload_batch_id=upload_batch_id,
            )
            if isinstance(insert_result, dict) and insert_result.get("ok") is False:
                raise RuntimeError(
                    str(insert_result.get("error_message") or "课程资料解析入库失败")
                )
            if not isinstance(insert_result, str) or not insert_result.startswith("成功插入 "):
                raise RuntimeError(str(insert_result or "课程资料解析入库失败"))
            course_document_sources = await QAserver.manage_exam_item(
                current_user=current_user,
                action="add_course_document_source",
                course_id=course_id,
                exam_item_id=exam_item_id,
                document_name=document_name,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            if insert_attempted and insert_tool is not None:
                try:
                    insert_tool.delete_documents_by_batch(course_id, upload_batch_id)
                except Exception:
                    logger.error("Rollback failed while deleting inserted course document batch")
            raise_course_value_error(e)
        except Exception as e:
            if insert_attempted and insert_tool is not None:
                try:
                    insert_tool.delete_documents_by_batch(course_id, upload_batch_id)
                except Exception:
                    logger.error("Rollback failed while deleting inserted course document batch")
            logger.error("上传课程资料失败")
            raise HTTPException(status_code=500, detail=f"上传课程资料失败: {str(e)}")
        finally:
            _remove_path(upload_work_dir)

        return {
            "success": True,
            "message": "课程资料上传成功",
            "data": {
                "document_name": document_name,
                "course_document_sources": course_document_sources,
            },
        }

    @app.get("/courses/{course_id}/exam_items/{exam_item_id}/course_documents")
    async def list_course_documents(
        course_id: str,
        exam_item_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            course_document_sources = await QAserver.manage_exam_item(
                current_user=current_user,
                action="list_course_document_sources",
                course_id=course_id,
                exam_item_id=exam_item_id,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("查询课程资料列表失败")
            raise HTTPException(status_code=500, detail=f"查询课程资料列表失败: {str(e)}")

        return {
            "success": True,
            "data": {
                "course_id": course_id,
                "exam_item_id": exam_item_id,
                "course_document_sources": course_document_sources,
            },
        }

    @app.delete("/courses/{course_id}/exam_items/{exam_item_id}/course_documents/{document_name}")
    async def delete_course_document(
        course_id: str,
        exam_item_id: str,
        document_name: str,
        current_user: dict = Depends(get_current_user),
    ):
        document_name = str(document_name or "").strip()
        try:
            if not document_name:
                raise ValueError("COURSE_DOCUMENT_SOURCE_REQUIRED")
            user_id = current_user.get("uuid")
            if not user_id or not await is_course_owner(str(user_id), course_id):
                raise PermissionError("Only the course owner can delete course documents")
            existing_sources = await QAserver.manage_exam_item(
                current_user=current_user,
                action="list_course_document_sources",
                course_id=course_id,
                exam_item_id=exam_item_id,
            )
            if document_name not in existing_sources:
                raise ValueError("COURSE_DOCUMENT_SOURCE_NOT_FOUND")

            course_document_sources = await QAserver.manage_exam_item(
                current_user=current_user,
                action="remove_course_document_source",
                course_id=course_id,
                exam_item_id=exam_item_id,
                document_name=document_name,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("删除课程资料失败")
            raise HTTPException(status_code=500, detail=f"删除课程资料失败: {str(e)}")

        cleanup_errors = await _cleanup_unreferenced_document_sources(
            course_id, [document_name],
        )
        return {
            "success": True,
            "message": "课程资料删除成功",
            "data": {
                "document_name": document_name,
                "course_document_sources": course_document_sources,
                "cleanup_errors": cleanup_errors,
            },
        }

    @app.get("/courses/{course_id}/exam_sessions")
    async def list_course_exam_sessions(course_id: str, current_user: dict = Depends(get_current_user)):
        try:
            sessions = await QAserver.list_exam_sessions_by_course(current_user, course_id)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except Exception as e:
            logger.error("查询考试记录失败")
            raise HTTPException(status_code=500, detail=f"查询考试记录失败: {str(e)}")
        return {
            "success": True,
            "data": sessions,
        }


    @app.put("/courses/{course_id}/exam_sessions/{exam_id}/preset_questions_usage")
    async def update_exam_session_preset_questions_usage(
        course_id: str,
        exam_id: str,
        req: ExamSessionPresetUsageRequest,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            updated = await QAserver.update_exam_session_preset_question_usage(
                current_user=current_user,
                course_id=course_id,
                exam_id=exam_id,
                use_preset_questions=req.use_preset_questions,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("更新考试预设题目开关失败")
            raise HTTPException(status_code=500, detail=f"更新考试预设题目开关失败: {str(e)}")
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("EXAM_SESSION_NOT_FOUND", "考试记录不存在或已完成"),
            )
        return {
            "success": True,
            "message": "考试预设题目开关更新成功",
            "data": updated,
        }

    @app.post("/courses/{course_id}/exam_items/{exam_item_id}/preset_questions")
    async def create_preset_question(
        course_id: str,
        exam_item_id: str,
        req: PresetQuestionCreateRequest,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            result = await QAserver.manage_preset_question(
                current_user=current_user,
                action="create",
                course_id=course_id,
                exam_item_id=exam_item_id,
                question_dimension=req.question_dimension,
                question_content=req.question_content,
                standard_answer=req.standard_answer,
                score=req.score,
                sort_order=req.sort_order,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("创建预设题目失败")
            raise HTTPException(status_code=500, detail=f"创建预设题目失败: {str(e)}")
        return {
            "success": True,
            "message": "预设题目创建成功",
            "data": result,
        }

    @app.get("/courses/{course_id}/exam_items/{exam_item_id}/preset_questions")
    async def list_preset_questions(
        course_id: str,
        exam_item_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            questions = await QAserver.manage_preset_question(
                current_user=current_user,
                action="list",
                course_id=course_id,
                exam_item_id=exam_item_id,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("查询预设题目失败")
            raise HTTPException(status_code=500, detail=f"查询预设题目失败: {str(e)}")
        return {
            "success": True,
            "data": questions,
        }

    @app.put("/courses/{course_id}/exam_items/{exam_item_id}/preset_questions/{preset_question_id}")
    async def update_preset_question(
        course_id: str,
        exam_item_id: str,
        preset_question_id: str,
        req: PresetQuestionUpdateRequest,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            updated = await QAserver.manage_preset_question(
                current_user=current_user,
                action="update",
                course_id=course_id,
                exam_item_id=exam_item_id,
                preset_question_id=preset_question_id,
                question_dimension=req.question_dimension,
                question_content=req.question_content,
                standard_answer=req.standard_answer,
                score=req.score,
                sort_order=req.sort_order,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("更新预设题目失败")
            raise HTTPException(status_code=500, detail=f"更新预设题目失败: {str(e)}")
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("PRESET_QUESTION_NOT_FOUND", "预设题目不存在"),
            )
        return {
            "success": True,
            "message": "预设题目更新成功",
            "data": updated,
        }

    @app.delete("/courses/{course_id}/exam_items/{exam_item_id}/preset_questions/{preset_question_id}")
    async def delete_preset_question(
        course_id: str,
        exam_item_id: str,
        preset_question_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            deleted = await QAserver.manage_preset_question(
                current_user=current_user,
                action="delete",
                course_id=course_id,
                exam_item_id=exam_item_id,
                preset_question_id=preset_question_id,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("删除预设题目失败")
            raise HTTPException(status_code=500, detail=f"删除预设题目失败: {str(e)}")
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("PRESET_QUESTION_NOT_FOUND", "预设题目不存在"),
            )
        return {
            "success": True,
            "message": "预设题目删除成功",
        }

    @app.put("/courses/{course_id}/exam_items/{exam_item_id}")
    async def update_exam_item(
        course_id: str,
        exam_item_id: str,
        req: ExamItemUpdateRequest,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            updated = await QAserver.manage_exam_item(
                current_user=current_user,
                action="update",
                course_id=course_id,
                exam_item_id=exam_item_id,
                exam_item_name=req.exam_item_name,
                dimension_scores=dimensions_to_scores(req.dimensions),
                exam_available_valid_times=req.exam_available_valid_times,
                description=req.description,
                item_type=req.item_type,
                need_code_repository=req.need_code_repository,
                use_preset_questions=req.use_preset_questions,
                enable_report_analysis=req.enable_report_analysis,
                report_total_score=req.report_total_score,
                report_judge_rule=req.report_judge_rule,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("更新考试项失败")
            raise HTTPException(status_code=500, detail=f"更新考试项失败: {str(e)}")
        if not updated:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("EXAM_ITEM_NOT_FOUND", "考试项不存在"),
            )
        return {
            "success": True,
            "message": "考试项更新成功",
            "data": updated,
        }

    @app.delete("/courses/{course_id}/exam_items/{exam_item_id}")
    async def delete_exam_item(
        course_id: str,
        exam_item_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            deleted = await QAserver.manage_exam_item(
                current_user,
                action="delete",
                course_id=course_id,
                exam_item_id=exam_item_id,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("删除考试项失败")
            raise HTTPException(status_code=500, detail=f"删除考试项失败: {str(e)}")
        if not deleted:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("EXAM_ITEM_NOT_FOUND", "考试项不存在"),
            )
        return {
            "success": True,
            "message": "考试项删除成功",
        }


    @app.put("/exam_items/{exam_item_id}/availability")
    async def reset_exam_item_availability(
        exam_item_id: str,
        req: ExamItemAvailabilityRequest,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            item = await QAserver.manage_exam_item(
                current_user=current_user,
                action="reset_availability",
                exam_item_id=exam_item_id,
                exam_available_valid_times=req.exam_available_valid_times,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.error("重置考试可开启时长失败")
            raise HTTPException(status_code=500, detail=f"重置考试可开启时长失败: {str(e)}")
        if not item:
            raise HTTPException(
                status_code=404,
                detail=course_error_detail("EXAM_ITEM_NOT_FOUND", "考试项不存在"),
            )
        return {
            "success": True,
            "message": "考试可开启时长重置成功",
            "data": item,
        }


    return app
