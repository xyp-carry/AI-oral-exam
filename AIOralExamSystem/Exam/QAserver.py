"""Exam item, preset question, and exam record service."""

from typing import Dict, List, Optional

from AIOralExamSystem.Course import CourseService
from AIOralExamSystem.Exam.Examdata import (
    add_exam_item_course_document_source,
    create_exam_item as create_exam_item_record,
    create_preset_question as create_preset_question_record,
    delete_exam_item as delete_exam_item_record,
    delete_preset_question as delete_preset_question_record,
    get_exam_history_by_course,
    get_exam_item_by_id,
    get_exam_item_course_document_sources,
    get_exam_questions_by_exam_item,
    get_exam_record_by_exam_id,
    get_exam_session_by_exam_id,
    is_course_owner,
    list_exam_items_by_course,
    list_exam_sessions_by_course_and_user,
    list_preset_questions_by_exam_item,
    remove_exam_item_course_document_source,
    reset_exam_item_availability as reset_exam_item_availability_record,
    update_exam_item as update_exam_item_record,
    update_exam_session_use_preset_questions as update_exam_session_use_preset_questions_record,
    update_preset_question as update_preset_question_record,
)


class QAserver:
    """Handle exam configuration and record queries used by routes."""

    @staticmethod
    def _flag_enabled(value) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "y", "on"}
        return bool(value)

    @classmethod
    async def get_exam_history(
        cls,
        current_user: dict,
        course_id: str,
        exam_item_id: Optional[str] = None,
    ) -> List[Dict[str, object]]:
        user_id, include_all_users = await CourseService.resolve_course_exam_query_scope(current_user, course_id)
        return await get_exam_history_by_course(
            course_id=course_id,
            user_id=user_id,
            include_all_users=include_all_users,
            exam_item_id=exam_item_id,
        )

    @classmethod
    async def get_exam_questions_by_exam_item(
        cls,
        current_user: dict,
        exam_item_id: str,
    ) -> List[Dict[str, object]]:
        if not exam_item_id:
            raise ValueError("exam_item_id is required")
        exam_item = await get_exam_item_by_id(exam_item_id)
        if not exam_item:
            raise ValueError("EXAM_ITEM_NOT_FOUND")
        course_id = str(exam_item["course_id"])
        user_id, include_all_users = await CourseService.resolve_course_exam_query_scope(current_user, course_id)
        return await get_exam_questions_by_exam_item(
            course_id=course_id,
            exam_item_id=exam_item_id,
            user_id=user_id,
            include_all_users=include_all_users,
        )

    @classmethod
    async def get_exam_record(cls, current_user: dict, exam_id: str) -> List[Dict[str, object]]:
        access_scope = await CourseService.resolve_exam_access_scope(current_user)
        return await get_exam_record_by_exam_id(
            exam_id=exam_id,
            **access_scope,
        )

    @classmethod
    async def list_exam_sessions_by_course(cls, current_user: dict, course_id: str) -> List[Dict[str, object]]:
        user_id = CourseService.get_user_id(current_user)
        if not user_id:
            raise PermissionError("当前用户缺少 user_id")
        if not await CourseService.can_view_course(current_user, course_id):
            raise PermissionError("无权查看该课程的考试记录")
        return await list_exam_sessions_by_course_and_user(course_id=course_id, user_id=user_id)

    @classmethod
    async def manage_exam_item(
        cls,
        current_user: dict,
        action: str,
        course_id: Optional[str] = None,
        exam_item_id: Optional[str] = None,
        exam_item_name: Optional[str] = None,
        dimension_scores: Optional[Dict[str, float]] = None,
        exam_available_valid_times: Optional[int] = None,
        description: Optional[str] = None,
        item_type: Optional[str] = None,
        need_code_repository: Optional[bool] = None,
        use_preset_questions: Optional[bool] = None,
        enable_report_analysis: Optional[bool] = None,
        report_total_score: Optional[float] = None,
        report_judge_rule: Optional[str] = None,
        c_max_probe_turns: Optional[int] = None,
        analysis_question_count: Optional[int] = None,
        document_name: Optional[str] = None,
    ):
        action = str(action).strip().lower()
        user_id = CourseService.get_user_id(current_user)

        if action == "reset_availability":
            if not exam_item_id:
                raise ValueError("exam_item_id is required")
            exam_item = await get_exam_item_by_id(exam_item_id)
            if not exam_item:
                return None
            course_id = str(exam_item["course_id"])
            if not user_id or not await is_course_owner(user_id, course_id):
                raise PermissionError("只有课程主负责老师可以重置考试可开启有效期")
            return await reset_exam_item_availability_record(
                exam_item_id=exam_item_id,
                exam_available_valid_times=exam_available_valid_times,
            )

        if not course_id:
            raise ValueError("course_id is required")

        if action == "list":
            if not await CourseService.can_view_course(current_user, course_id):
                raise PermissionError("无权查看该课程的考试项")
            return await list_exam_items_by_course(course_id)

        if action == "create":
            role = CourseService.get_user_role(current_user)
            if role not in CourseService.TEACHER_ROLES and role not in CourseService.ADMIN_ROLES:
                raise PermissionError("只有课程主负责老师可以创建考试项")
            if not user_id:
                raise PermissionError("当前用户缺少 user_id")
            if not await is_course_owner(user_id, course_id):
                raise PermissionError("只有课程主负责老师可以创建考试项")
            created_item = await create_exam_item_record(
                course_id=course_id,
                exam_item_id=exam_item_id,
                exam_item_name=exam_item_name,
                created_by=user_id,
                dimension_scores=dimension_scores,
                exam_available_valid_times=exam_available_valid_times,
                description=description,
                item_type=item_type,
                need_code_repository=cls._flag_enabled(need_code_repository),
                use_preset_questions=cls._flag_enabled(use_preset_questions),
                enable_report_analysis=cls._flag_enabled(enable_report_analysis),
                report_total_score=report_total_score,
                report_judge_rule=report_judge_rule,
                c_max_probe_turns=2 if c_max_probe_turns is None else c_max_probe_turns,
                analysis_question_count=3 if analysis_question_count is None else analysis_question_count,
            )
            return created_item

        if action == "add_course_document_source":
            if not exam_item_id:
                raise ValueError("exam_item_id is required")
            if not user_id or not await is_course_owner(user_id, course_id):
                raise PermissionError("只有课程主负责老师可以添加课程资料")
            return await add_exam_item_course_document_source(
                course_id=course_id,
                exam_item_id=exam_item_id,
                document_name=document_name,
            )

        if action == "list_course_document_sources":
            if not exam_item_id:
                raise ValueError("exam_item_id is required")
            if not await CourseService.can_view_course(current_user, course_id):
                raise PermissionError("No permission to view course documents")
            return await get_exam_item_course_document_sources(course_id, exam_item_id)

        if action == "remove_course_document_source":
            if not exam_item_id:
                raise ValueError("exam_item_id is required")
            if not user_id or not await is_course_owner(user_id, course_id):
                raise PermissionError("Only the course owner can delete course documents")
            return await remove_exam_item_course_document_source(
                course_id=course_id,
                exam_item_id=exam_item_id,
                document_name=document_name,
            )

        if action not in {"update", "delete"}:
            raise ValueError(f"Unsupported exam item action: {action}")
        if not user_id or not await is_course_owner(user_id, course_id):
            raise PermissionError("只有课程主负责老师可以修改或删除考试项")
        if not exam_item_id:
            raise ValueError("exam_item_id is required")

        if action == "update":
            return await update_exam_item_record(
                course_id=course_id,
                exam_item_id=exam_item_id,
                exam_item_name=exam_item_name,
                dimension_scores=dimension_scores,
                exam_available_valid_times=exam_available_valid_times,
                description=description,
                item_type=item_type,
                need_code_repository=(
                    None
                    if need_code_repository is None
                    else cls._flag_enabled(need_code_repository)
                ),
                use_preset_questions=(
                    None
                    if use_preset_questions is None
                    else cls._flag_enabled(use_preset_questions)
                ),
                enable_report_analysis=(
                    None
                    if enable_report_analysis is None
                    else cls._flag_enabled(enable_report_analysis)
                ),
                report_total_score=report_total_score,
                report_judge_rule=report_judge_rule,
                c_max_probe_turns=c_max_probe_turns,
                analysis_question_count=analysis_question_count,
            )
        return await delete_exam_item_record(course_id=course_id, exam_item_id=exam_item_id)

    @classmethod
    async def manage_preset_question(
        cls,
        current_user: dict,
        action: str,
        course_id: str,
        exam_item_id: str,
        preset_question_id: Optional[str] = None,
        question_dimension: Optional[str] = None,
        question_content: Optional[str] = None,
        standard_answer: Optional[str] = None,
        question_blocks: Optional[List[Dict[str, object]]] = None,
        code_fragments: Optional[List[Dict[str, object]]] = None,
        score: Optional[float] = None,
        sort_order: Optional[int] = None,
    ):
        action = str(action).strip().lower()
        user_id = CourseService.get_user_id(current_user)

        if action == "list":
            if not await CourseService.can_view_course(current_user, course_id):
                raise PermissionError("无权查看该考试项的预设题库")
            return await list_preset_questions_by_exam_item(course_id, exam_item_id, user_id=user_id)

        if action not in {"create", "update", "delete"}:
            raise ValueError(f"Unsupported preset question action: {action}")
        if not user_id or not await is_course_owner(user_id, course_id):
            raise PermissionError("只有课程主负责老师可以维护预设题库")

        if action == "create":
            return await create_preset_question_record(
                course_id=course_id,
                exam_item_id=exam_item_id,
                created_by=user_id,
                question_dimension=question_dimension,
                question_content=question_content,
                standard_answer=standard_answer,
                question_blocks=question_blocks,
                code_fragments=code_fragments,
                score=1.0 if score is None else score,
                sort_order=sort_order,
            )

        if not preset_question_id:
            raise ValueError("preset_question_id is required")
        if action == "update":
            return await update_preset_question_record(
                course_id=course_id,
                exam_item_id=exam_item_id,
                preset_question_id=preset_question_id,
                question_dimension=question_dimension,
                question_content=question_content,
                standard_answer=standard_answer,
                question_blocks=question_blocks,
                code_fragments=code_fragments,
                score=score,
                sort_order=sort_order,
            )
        return await delete_preset_question_record(
            course_id=course_id,
            exam_item_id=exam_item_id,
            preset_question_id=preset_question_id,
        )

    @classmethod
    async def update_exam_session_preset_question_usage(
        cls,
        current_user: dict,
        course_id: str,
        exam_id: str,
        use_preset_questions: bool,
    ) -> bool:
        user_id = CourseService.get_user_id(current_user)
        if not user_id or not await is_course_owner(user_id, course_id):
            raise PermissionError("只有课程主负责老师可以设置考试是否使用预设题目")
        session = await get_exam_session_by_exam_id(exam_id)
        if session and session.get("course_id") == course_id and session.get("exam_type") == "B" and not use_preset_questions:
            raise ValueError("PRESET_QUESTION_REQUIRED")
        return await update_exam_session_use_preset_questions_record(
            course_id=course_id,
            exam_id=exam_id,
            use_preset_questions=use_preset_questions,
        )
