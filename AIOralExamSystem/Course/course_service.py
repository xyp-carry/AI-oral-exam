"""Course management and course-based access decisions."""

from __future__ import annotations

import asyncio
from typing import Dict, List, Optional

from Authentication.database import db as auth_db
from AIOralExamSystem.Exam.Examdata import (
    approve_course_join_request_by_user,
    create_course as create_course_record,
    delete_course as delete_course_record,
    get_course_by_invite_code as get_course_by_invite_code_record,
    get_teacher_course_ids,
    is_course_owner,
    is_student_in_course,
    is_teacher_of_course,
    list_all_courses,
    list_course_join_requests,
    list_student_courses,
    list_teacher_courses,
    request_join_course,
    reset_course_invite_code as reset_course_invite_code_record,
    update_course as update_course_record,
)


class CourseService:
    """Manage courses, membership requests, and access scopes."""

    STUDENT_ROLES = {"student", "candidate", ""}
    TEACHER_ROLES = {"teacher", "instructor"}
    ADMIN_ROLES = {"admin", "administrator", "super_admin"}

    @classmethod
    async def resolve_exam_access_scope(cls, current_user: dict) -> Dict[str, object]:
        role = cls.get_user_role(current_user)
        user_id = cls.get_user_id(current_user)

        if role in cls.ADMIN_ROLES:
            return {
                "user_id": user_id,
                "course_ids": [],
                "allow_course_scope": False,
                "allow_all": True,
            }

        if role in cls.TEACHER_ROLES:
            if not user_id:
                raise PermissionError("当前教师用户缺少 user_id")
            course_ids = await get_teacher_course_ids(user_id)
            return {
                "user_id": user_id,
                "course_ids": course_ids,
                "allow_course_scope": True,
                "allow_all": False,
            }

        if not user_id:
            raise PermissionError("当前用户缺少 user_id")
        return {
            "user_id": user_id,
            "course_ids": [],
            "allow_course_scope": False,
            "allow_all": False,
        }

    @classmethod
    async def resolve_course_exam_query_scope(cls, current_user: dict, course_id: str) -> tuple[Optional[str], bool]:
        if not course_id:
            raise ValueError("course_id is required")
        role = cls.get_user_role(current_user)
        user_id = cls.get_user_id(current_user)

        if role in cls.ADMIN_ROLES:
            return user_id, True

        if not user_id:
            raise PermissionError("当前用户缺少 user_id")

        if role in cls.TEACHER_ROLES:
            if not await is_teacher_of_course(user_id, course_id):
                raise PermissionError("无权查看该课程的考试信息")
            return user_id, True

        if not await is_student_in_course(user_id, course_id):
            raise PermissionError("无权查看该课程的考试信息")
        return user_id, False

    @classmethod
    async def manage_course(
        cls,
        current_user: dict,
        action: str,
        course_id: Optional[str] = None,
        course_name: Optional[str] = None,
        description: Optional[str] = None,
        invite_code_valid_times: Optional[int] = None,
    ):
        role = cls.get_user_role(current_user)
        user_id = cls.get_user_id(current_user)
        action = str(action).strip().lower()

        if action == "list":
            if role in cls.ADMIN_ROLES:
                return await list_all_courses()
            if not user_id:
                raise PermissionError("当前用户缺少 user_id")
            if role in cls.TEACHER_ROLES:
                return await list_teacher_courses(user_id)
            return await list_student_courses(user_id)

        if action == "create":
            if role not in cls.TEACHER_ROLES and role not in cls.ADMIN_ROLES:
                raise PermissionError("只有教师可以创建课程")
            if not user_id:
                raise PermissionError("当前用户缺少 user_id")
            return await create_course_record(
                course_name=course_name,
                owner_teacher_id=user_id,
                description=description,
                invite_code_valid_times=(
                    2592000 if invite_code_valid_times is None else invite_code_valid_times
                ),
            )

        if action not in {"update", "delete", "reset_invite_code"}:
            raise ValueError(f"Unsupported course action: {action}")
        if role not in cls.TEACHER_ROLES:
            raise PermissionError("只有课程主负责老师可以修改或删除课程")
        if not user_id:
            raise PermissionError("当前教师用户缺少 user_id")
        if not course_id:
            raise ValueError("course_id is required")

        if action == "reset_invite_code":
            if not await is_course_owner(user_id, course_id):
                raise PermissionError("只有课程主负责老师可以重置课程邀请码")
            return await reset_course_invite_code_record(
                course_id=course_id,
                invite_code_valid_times=invite_code_valid_times,
            )

        if action == "update":
            return await update_course_record(
                course_id=course_id,
                owner_teacher_id=user_id,
                course_name=course_name,
                description=description,
            )
        return await delete_course_record(course_id=course_id, owner_teacher_id=user_id)

    @classmethod
    async def manage_course_join_request(
        cls,
        current_user: dict,
        action: str,
        course_id: str,
        target_user_id: Optional[str] = None,
    ):
        role = cls.get_user_role(current_user)
        user_id = cls.get_user_id(current_user)
        action = str(action).strip().lower()
        if not user_id:
            raise PermissionError("当前用户缺少 user_id")
        if not course_id:
            raise ValueError("course_id is required")

        if action == "create":
            request_id = await request_join_course(course_id=course_id, user_id=user_id)
            return {"request_id": request_id}

        if action == "list":
            if role not in cls.TEACHER_ROLES:
                raise PermissionError("只有课程教师可以查看课程申请")
            if not await is_teacher_of_course(user_id, course_id):
                raise PermissionError("无权查看该课程的申请")
            return await list_course_join_requests(teacher_id=user_id, course_id=course_id)

        if action == "approve":
            if not target_user_id:
                raise ValueError("user_id is required")
            if not await is_teacher_of_course(user_id, course_id):
                raise PermissionError("审批教师不是该课程的有效教师")
            target_user = await asyncio.to_thread(auth_db.get_user_by_uuid, target_user_id)
            if target_user is None:
                raise ValueError("USER_NOT_FOUND")
            target_role = cls.get_user_role(target_user)
            if target_role in cls.TEACHER_ROLES:
                member_role = "teacher"
            elif target_role in cls.STUDENT_ROLES:
                member_role = "student"
            else:
                raise ValueError("USER_ROLE_UNSUPPORTED")
            return await approve_course_join_request_by_user(
                course_id=course_id,
                user_id=target_user_id,
                user_role=member_role,
                reviewer_id=user_id,
            )

        raise ValueError(f"Unsupported course join request action: {action}")

    @classmethod
    async def get_course_by_invite_code(cls, invite_code: str) -> Optional[Dict[str, object]]:
        return await get_course_by_invite_code_record(invite_code)

    @classmethod
    async def can_view_course(cls, current_user: dict, course_id: str) -> bool:
        """检查用户是否有查看查看课程的权限"""
        role = cls.get_user_role(current_user)
        user_id = cls.get_user_id(current_user)
        if role in cls.ADMIN_ROLES:
            return True
        if not user_id:
            return False
        if role in cls.TEACHER_ROLES:
            return await is_teacher_of_course(user_id, course_id)
        return await is_student_in_course(user_id, course_id)

    @staticmethod
    def get_user_id(current_user: dict) -> Optional[str]:
        user_id = (
            current_user.get("uuid")
            or current_user.get("id")
            or current_user.get("user_id")
        )
        return str(user_id) if user_id is not None else None

    @staticmethod
    def get_user_role(current_user: dict) -> str:
        return str(
            current_user.get("role")
            or current_user.get("user_type")
            or current_user.get("type")
            or ""
        ).lower()

    @classmethod
    def get_user_course_ids(cls, current_user: dict) -> List[str]:
        course_ids: List[str] = []
        for key in (
            "teacher_course_ids",
            "teacher_courses",
            "course_ids",
            "courses",
            "course_id",
            "courseId",
            "course",
            "class_id",
            "classId",
        ):
            course_ids.extend(cls.normalize_course_ids(current_user.get(key)))
        return list(dict.fromkeys(course_ids))

    @classmethod
    def normalize_course_ids(cls, raw_value) -> List[str]:
        if raw_value is None:
            return []
        if isinstance(raw_value, dict):
            course_ids: List[str] = []
            for key in ("course_id", "courseId", "id", "uuid", "course", "class_id", "classId"):
                value = raw_value.get(key)
                if value is not None:
                    course_ids.extend(cls.normalize_course_ids(value))
            if course_ids:
                return course_ids
            return cls.get_user_course_ids(raw_value)
        if isinstance(raw_value, (list, tuple, set)):
            course_ids: List[str] = []
            for item in raw_value:
                if isinstance(item, dict):
                    course_ids.extend(cls.get_user_course_ids(item))
                else:
                    course_ids.extend(cls.normalize_course_ids(item))
            return course_ids
        if isinstance(raw_value, str):
            return [
                item.strip()
                for item in raw_value.split(",")
                if item.strip()
            ]
        return [str(raw_value)]

