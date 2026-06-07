import json
from pathlib import Path
from typing import Dict, List

from fastapi import Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from AIOralExamSystem.Exam.Examdata.exam_repository import (
    has_repository_url_by_exam_session,
    update_exam_session_repository_url,
)
from AIOralExamSystem.Exam.QAserver import QAserver
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


class ExamItemUpdateRequest(BaseModel):
    exam_item_name: str | None = None
    dimensions: List[ExamItemDimension] | None = None
    description: str | None = None
    item_type: str | None = None
    need_code_repository: bool | None = None


class ExamItemAvailabilityRequest(BaseModel):
    exam_available_valid_times: int


class GitRepositoryUploadRequest(BaseModel):
    course_id: str
    exam_id: str
    git_url: str
    git_branch: str
    reload: bool = False


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


def _failure(reason: str) -> dict:
    return {
        "success": False,
        "message": "上传失败",
        "reason": reason,
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


def exam_routes(app, args):
    """Register course, exam, and git repository routes."""

    @app.post("/git/repository")
    async def upload_git_repository(
        req: GitRepositoryUploadRequest,
        current_user: dict = Depends(get_current_user),
    ):
        course_id = req.course_id.strip()
        user_id = current_user.get("uuid")
        if not user_id:
            return _failure("user_id 不能为空")
        exam_id = req.exam_id.strip()
        git_url = req.git_url.strip()
        git_branch = req.git_branch.strip()

        if not course_id or not user_id or not exam_id or not git_url or not git_branch:
            return _failure("course_id、user_id、exam_id、git_url、git_branch 不能为空")

        try:
            tool = GitRepositoryTool("git_repository_tool")
            # Wait for repository processing to finish before returning the API response.
            raw_result = await tool._run(
                repo_url=git_url,
                user_uuid=user_id,
                code_path="code",
                doc_path="doc",
                branch=git_branch,
                course_id=course_id,
                exam_id=exam_id,
                git_branch=git_branch,
                reload=req.reload,
            )
            result = json.loads(raw_result)
        except json.JSONDecodeError as e:
            logger.exception("Git 仓库工具返回结果不是合法 JSON")
            return _failure(f"Git 仓库工具返回结果解析失败: {str(e)}")
        except Exception as e:
            logger.exception("Git 仓库上传失败")
            return _failure(str(e))

        if _git_tool_failed(result):
            return _failure(_git_tool_failure_reason(result))

        try:
            if not req.reload:
                updated = await update_exam_session_repository_url(
                    user_id=user_id,
                    course_id=course_id,
                    exam_id=exam_id,
                    repository_url=git_url,
                )
            else:
                updated = True
        except Exception as e:
            logger.exception("更新考试仓库地址失败")
            return _failure(f"Git 仓库已保存，但更新考试仓库地址失败: {str(e)}")

        if not updated:
            return _failure("Git 仓库已保存，但未找到对应的考试记录")

        doc_root = Path(str(result.get("repository_root", ""))) / "doc"
        doc_work_dir = Path(str(result.get("repository_root", ""))) / "FILE"
        doc_files = _collect_files(doc_root)
        if doc_files:
            try:
                settings = get_settings()
                monitor = GlobalMonitor()
                monitor.start()
                insert_tool = InsertTool("insert_tool", settings.mineru_api_key)
                for index, doc_file in enumerate(doc_files):
                    await insert_tool.execute(
                        data=[doc_file],
                        source=user_id,
                        type="file",
                        course_id=course_id,
                        exam_id=exam_id,
                        work_dir=str(doc_work_dir),
                        reload=req.reload and index == 0,
                    )
            except Exception as e:
                logger.exception("Git documents insert failed")
                return _failure(f"Git repository saved, but documents insert failed: {str(e)}")

        return {
            "success": True,
            "message": "上传成功",
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

    @app.get("/exam_history")
    async def exam_history(current_user: dict = Depends(get_current_user)):
        try:
            history = await QAserver.get_exam_history(current_user)
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
            )
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
                description=req.description,
                item_type=req.item_type,
                need_code_repository=req.need_code_repository,
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
