from fastapi import Depends, HTTPException
from loguru import logger
from pydantic import BaseModel

from AIOralExamSystem.Course import CourseService
from Authentication.auth import get_current_user


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


def register_course_routes(app, args):
    """Register course routes."""
    @app.post("/courses")
    async def create_course(req: CourseCreateRequest, current_user: dict = Depends(get_current_user)):
        try:
            result = await CourseService.manage_course(
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
            courses = await CourseService.manage_course(current_user, action="list")
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
            course = await CourseService.get_course_by_invite_code(invite_code)
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
            course = await CourseService.manage_course(
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
            result = await CourseService.manage_course_join_request(
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
            requests = await CourseService.manage_course_join_request(
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
            result = await CourseService.manage_course_join_request(
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
            updated = await CourseService.manage_course(
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
            deleted = await CourseService.manage_course(current_user, action="delete", course_id=course_id)
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
