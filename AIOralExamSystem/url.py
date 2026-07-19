import json
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Dict, List

from fastapi import Depends, File, Form, HTTPException, Request, UploadFile
from loguru import logger
from pydantic import BaseModel

from AIOralExamSystem.Exam.Examdata.exam_repository import (
    get_exam_session_by_exam_id,
    has_repository_url_by_exam_session,
    update_exam_session_repository_url,
)
from AIOralExamSystem.Exam.Examdata import (
    get_exam_item_by_id,
    get_exam_item_course_document_sources,
    is_course_owner,
)
from AIOralExamSystem.Exam.Examdata.judge_config_repository import (
    get_exam_judge_config_by_exam_id,
    upsert_exam_judge_config,
    upsert_exam_report_model_config,
)
from AIOralExamSystem.Exam.QAserver import QAserver
from AIOralExamSystem.Graph.AIOralExamsetter import AIOralExamsetter
from AIOralExamSystem.Tool.git.git_tool import GitRepositoryTool
from AIOralExamSystem.Tool.rag.data_tool import InsertTool
from Authentication.auth import get_current_user
from config import get_settings
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
) -> dict:
    repository_root = Path(repository_root).expanduser().resolve(strict=False)
    if not repository_root.exists() or not repository_root.is_dir():
        return {
            "ok": False,
            "flag": "REPOSITORY_ROOT_NOT_FOUND",
            "error_message": f"仓库目录不存在，无法进行报告评价: {repository_root}",
        }

    settings = get_settings()
    setter = AIOralExamsetter(
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
    )


def exam_routes(app, args):
    """Register course, exam, and git repository routes."""

    @app.post("/git/repository")
    async def upload_git_repository(
        request: Request,
        current_user: dict = Depends(get_current_user),
    ):
        req = await _parse_git_repository_request(request)
        course_id = req["course_id"].strip()
        user_id = current_user.get("uuid")
        if not user_id:
            return _failure("user_id 不能为空")
        exam_id = req["exam_id"].strip()
        git_url = req["git_url"]
        git_branch = req["git_branch"].strip() or "main"
        upload_file = req["file"]
        use_archive = upload_file is not None

        if not course_id or not user_id or not exam_id or not git_branch:
            if upload_file is not None:
                await upload_file.close()
            return _failure("course_id、user_id、exam_id、git_url、git_branch 不能为空")

        if bool(git_url) == use_archive:
            if upload_file is not None:
                await upload_file.close()
            return _failure("git_url and file must be provided one at a time.")

        upload_batch_id = str(uuid.uuid4())
        tool = GitRepositoryTool("git_repository_tool")
        upload_temp_dir = None
        archive_path = None
        archive_name = None
        repository_address = git_url
        if use_archive:
            archive_name = Path(str(upload_file.filename)).name
            if not archive_name.lower().endswith(".zip"):
                await upload_file.close()
                return _failure("Only .zip repository archives are supported.")
            repository_address = archive_name
            upload_temp_dir = tempfile.TemporaryDirectory(prefix="git_repository_upload_")
            archive_path = Path(upload_temp_dir.name) / archive_name
            upload_file.file.seek(0)
            with archive_path.open("wb") as archive_file:
                shutil.copyfileobj(upload_file.file, archive_file)

        final_root = tool._repo_cache_root(None, user_id, repository_address, course_id, exam_id, git_branch)
        staging_root = final_root.parent / f".staging-{final_root.name}-{upload_batch_id}"
        backup_root = final_root.parent / f".backup-{final_root.name}-{upload_batch_id}"
        old_repository_url = ""
        report_result = None
        had_original = False
        final_root_swapped = False
        db_updated = False

        try:
            exam_session = await get_exam_session_by_exam_id(exam_id)
            if (
                not exam_session
                or str(exam_session.get("user_id")) != str(user_id)
                or str(exam_session.get("course_id")) != course_id
            ):
                return _failure("未找到当前用户对应的考试记录")
            old_repository_url = str(exam_session.get("repository_url") or "")

            _remove_path(staging_root)
            raw_result = await tool._run(
                repo_url=repository_address,
                user_uuid=user_id,
                archive_path=str(archive_path) if archive_path else None,
                archive_name=archive_name,
                branch=git_branch,
                course_id=course_id,
                exam_id=exam_id,
                git_branch=git_branch,
                reload=True,
                target_root=str(staging_root),
            )
            result = json.loads(raw_result)
            if _git_tool_failed(result):
                raise RuntimeError(_git_tool_failure_reason(result))

            had_original = _swap_repository_root(staging_root, final_root, backup_root)
            final_root_swapped = True

            if not final_root.exists() or not final_root.is_dir():
                raise RuntimeError(f"仓库目录不存在，无法进行报告评价: {final_root}")

            updated = await update_exam_session_repository_url(
                user_id=user_id,
                course_id=course_id,
                exam_id=exam_id,
                repository_url=repository_address,
            )
            if not updated:
                raise RuntimeError("未找到对应的考试记录，数据库未更新")
            db_updated = True

            report_result = await _run_report_analysis_after_repository_upload(
                user_id=user_id,
                course_id=course_id,
                exam_id=exam_id,
                repository_root=final_root,
            )
            if not report_result.get("ok", False):
                raise RuntimeError(
                    report_result.get("error_message")
                    or report_result.get("finish_reason")
                    or "报告评价初始化失败"
                )

            _remove_path(backup_root)
        except json.JSONDecodeError as e:
            _remove_path(staging_root)
            logger.exception("Git 仓库工具返回结果不是合法 JSON")
            return _failure(f"Git 仓库工具返回结果解析失败: {str(e)}")
        except Exception as e:
            logger.exception("Git 仓库上传失败")
            try:
                _restore_repository_root(final_root, backup_root, had_original, final_root_swapped)
                _remove_path(staging_root)
                _remove_path(backup_root)
            except Exception:
                logger.exception("Rollback failed while restoring repository files")
            if db_updated:
                try:
                    await update_exam_session_repository_url(
                        user_id=user_id,
                        course_id=course_id,
                        exam_id=exam_id,
                        repository_url=old_repository_url,
                    )
                except Exception:
                    logger.exception("Rollback failed while restoring repository_url")
            return _failure(str(e))
        finally:
            if upload_temp_dir is not None:
                upload_temp_dir.cleanup()
            if upload_file is not None:
                await upload_file.close()

        try:
            if False and not req["reload"]:
                updated = await update_exam_session_repository_url(
                    user_id=user_id,
                    course_id=course_id,
                    exam_id=exam_id,
                    repository_url=repository_address,
                )
            else:
                updated = True
        except Exception as e:
            logger.exception("更新考试仓库地址失败")
            return _failure(f"Git 仓库已保存，但更新考试仓库地址失败: {str(e)}")

        if not updated:
            return _failure("Git 仓库已保存，但未找到对应的考试记录")

        return {
            "success": True,
            "message": "上传成功",
            "repository_root": str(final_root),
            "report_result": report_result,
        }


    @app.get("/exam_sessions/repository_status")
    async def exam_session_repository_status(
        course_id: str,
        exam_id: str,
        current_user: dict = Depends(get_current_user),
    ):
        user_uuid = current_user.get("uuid")
        if not user_uuid:
            return {
                "success": False,
                "has_repository_url": False,
                "reason": "用户身份信息无效",
            }
        try:
            has_repository_url = await has_repository_url_by_exam_session(
                user_id=user_uuid,
                course_id=course_id,
                exam_id=exam_id,
            )
        except Exception as e:
            logger.exception("查询考试仓库状态失败")
            raise HTTPException(status_code=500, detail=f"查询考试仓库状态失败: {str(e)}")
        return {
            "success": True,
            "has_repository_url": has_repository_url,
        }

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
            logger.exception("查询考试历史失败")
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
            logger.exception("查询考试题目失败")
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
            logger.exception("查询考试记录失败")
            raise HTTPException(status_code=500, detail=f"查询考试记录失败: {str(e)}")
        return {
            "success": True,
            "data": records,
        }

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
            logger.exception("创建课程失败")
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
            logger.exception("查询课程失败")
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
            logger.exception("根据邀请码查询课程失败")
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
            logger.exception("重置课程邀请码失败")
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
            logger.exception("创建课程加入申请失败")
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
            logger.exception("查询课程加入申请失败")
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
            logger.exception("审批课程加入申请失败")
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
            logger.exception("更新课程失败")
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
            logger.exception("删除课程失败")
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
            judge_config = await save_exam_model_config(
                str(result.get("exam_item_id")),
                req,
                current_user,
            )
            if judge_config is not None:
                result["judge_config"] = judge_config
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.exception("创建考试项失败")
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
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except Exception as e:
            logger.exception("查询考试项失败")
            raise HTTPException(status_code=500, detail=f"查询考试项失败: {str(e)}")
        return {
            "success": True,
            "data": items,
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

            settings = get_settings()
            monitor = GlobalMonitor()
            monitor.start()
            insert_tool = InsertTool("insert_tool", settings.mineru_api_key)
            insert_attempted = True
            await insert_tool.execute(
                data=file_paths,
                source=document_name,
                type="file",
                course_id=course_id,
                exam_id=None,
                work_dir=str(upload_work_dir),
                reload=False,
                upload_batch_id=upload_batch_id,
            )
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
                    logger.exception("Rollback failed while deleting inserted course document batch")
            raise_course_value_error(e)
        except Exception as e:
            if insert_attempted and insert_tool is not None:
                try:
                    insert_tool.delete_documents_by_batch(course_id, upload_batch_id)
                except Exception:
                    logger.exception("Rollback failed while deleting inserted course document batch")
            logger.exception("上传课程资料失败")
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
            logger.exception("查询课程资料列表失败")
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

            settings = get_settings()
            insert_tool = InsertTool("insert_tool", settings.mineru_api_key)
            insert_tool.delete_course_documents_by_source(course_id, document_name)
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
            logger.exception("删除课程资料失败")
            raise HTTPException(status_code=500, detail=f"删除课程资料失败: {str(e)}")

        return {
            "success": True,
            "message": "课程资料删除成功",
            "data": {
                "document_name": document_name,
                "course_document_sources": course_document_sources,
            },
        }

    @app.get("/courses/{course_id}/exam_sessions")
    async def list_course_exam_sessions(course_id: str, current_user: dict = Depends(get_current_user)):
        try:
            sessions = await QAserver.list_exam_sessions_by_course(current_user, course_id)
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except Exception as e:
            logger.exception("查询考试记录失败")
            raise HTTPException(status_code=500, detail=f"查询考试记录失败: {str(e)}")
        return {
            "success": True,
            "data": sessions,
        }

    @app.post("/courses/{course_id}/exam_items/{exam_item_id}/report_score")
    async def score_report(
        course_id: str,
        exam_item_id: str,
        req: ReportScoreRequest,
        current_user: dict = Depends(get_current_user),
    ):
        try:
            exam_item = await get_exam_item_by_id(exam_item_id)
            if not exam_item or str(exam_item.get("course_id")) != str(course_id):
                raise ValueError("EXAM_ITEM_NOT_FOUND")
            if not QAserver._flag_enabled(exam_item.get("enable_report_analysis")):
                raise ValueError("REPORT_ANALYSIS_DISABLED")
            result = await QAserver.score_report_and_prepare_questions(
                current_user=current_user,
                course_id=course_id,
                exam_item_id=exam_item_id,
                target_user_id=req.user_id,
                exam_id=req.exam_id,
                report_total_score=exam_item.get("report_total_score"),
                report_judge_rule=exam_item.get("report_judge_rule"),
                prepare_questions=req.prepare_questions,
            )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.exception("报告评分失败")
            raise HTTPException(status_code=500, detail=f"报告评分失败: {str(e)}")
        return {
            "success": True,
            "data": result,
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
            logger.exception("更新考试预设题目开关失败")
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
            logger.exception("创建预设题目失败")
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
            logger.exception("查询预设题目失败")
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
            logger.exception("更新预设题目失败")
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
            logger.exception("删除预设题目失败")
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
            if updated:
                await save_exam_model_config(
                    exam_item_id,
                    req,
                    current_user,
                )
        except PermissionError as e:
            raise HTTPException(status_code=403, detail=course_error_detail("FORBIDDEN", str(e)))
        except ValueError as e:
            raise_course_value_error(e)
        except Exception as e:
            logger.exception("更新考试项失败")
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
        except Exception as e:
            logger.exception("删除考试项失败")
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
            logger.exception("重置考试可开启时长失败")
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

