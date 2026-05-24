import asyncio
import json
import re
from typing import Dict, List, Optional

from loguru import logger

from AIOralExamSystem.Exam.Examdata import (
    create_course as create_course_record,
    create_exam_item as create_exam_item_record,
    delete_course as delete_course_record,
    delete_exam_item as delete_exam_item_record,
    get_exam_history_by_user,
    get_exam_record_by_exam_id,
    get_teacher_course_ids,
    is_course_owner,
    is_student_in_course,
    is_teacher_of_course,
    list_all_courses,
    list_exam_items_by_course,
    list_student_courses,
    list_teacher_courses,
    save_exam_data,
    update_course as update_course_record,
    update_exam_item as update_exam_item_record,
)
from AIOralExamSystem.Exam.Judger import MainJudgerAgent, StageJudgerAgent
from AIOralExamSystem.Exam.OutputSetting import build_final_review_output
from AIOralExamSystem.Exam.examObject import CandidateExamState, Question, QuestionGenerationPlan
from AIOralExamSystem.Exam.examSetter import ExamSetterAgent
from config import get_settings


class QAserver:
    """独立的问题管理服务，只处理普通消息和考试对象。"""

    MISSING_EXAM_STATE_ERROR = "AI考试状态不存在，无法继续口试"

    STUDENT_ROLES = {"student", "candidate", ""}
    TEACHER_ROLES = {"teacher", "instructor"}
    ADMIN_ROLES = {"admin", "administrator", "super_admin"}

    def __init__(
        self,
        current_user: dict,
        exam_state: Optional[CandidateExamState],
        history: List[Dict[str, str]],
    ):
        self.current_user = current_user
        self.exam_state = exam_state
        self.history = history
        self.exam_finished = False
        self.exam_setter = None
        self.stage_judger = None
        self.main_judger = None
        self.task_queue = None
        self._task = None
        self._prepare_generation_pending = False

        if self.exam_state is None:
            return

        self.task_queue = asyncio.Queue()

        settings = get_settings()
        model_settings = settings.model_dump(mode="json")
        self.exam_setter = ExamSetterAgent(
            model_settings,
            current_user["uuid"],
            thinking=False,
            response_format=True,
            temperature=0,
        )
        self.stage_judger = StageJudgerAgent(
            model_settings,
            current_user["uuid"],
            thinking=True,
            response_format=True,
            temperature=0,
        )
        self.main_judger = MainJudgerAgent(
            model_settings,
            current_user["uuid"],
            thinking=True,
            response_format=True,
            temperature=0,
        )

    @classmethod
    async def get_exam_history(cls, current_user: dict) -> List[Dict[str, object]]:
        access_scope = await cls.resolve_exam_access_scope(current_user)
        return await get_exam_history_by_user(**access_scope)

    @classmethod
    async def get_exam_record(cls, current_user: dict, exam_id: str) -> List[Dict[str, object]]:
        access_scope = await cls.resolve_exam_access_scope(current_user)
        return await get_exam_record_by_exam_id(
            exam_id=exam_id,
            **access_scope,
        )

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
    async def create_course(
        cls,
        current_user: dict,
        course_name: str,
        description: Optional[str] = None,
    ) -> Dict[str, object]:
        role = cls.get_user_role(current_user)
        user_id = cls.get_user_id(current_user)
        if role not in cls.TEACHER_ROLES and role not in cls.ADMIN_ROLES:
            raise PermissionError("只有教师可以创建课程")
        if not user_id:
            raise PermissionError("当前用户缺少 user_id")
        course_id = await create_course_record(
            course_name=course_name,
            owner_teacher_id=user_id,
            description=description,
        )
        return {"course_id": course_id}

    @classmethod
    async def list_courses(cls, current_user: dict) -> List[Dict[str, object]]:
        role = cls.get_user_role(current_user)
        user_id = cls.get_user_id(current_user)
        if role in cls.ADMIN_ROLES:
            return await list_all_courses()
        if not user_id:
            raise PermissionError("当前用户缺少 user_id")
        if role in cls.TEACHER_ROLES:
            return await list_teacher_courses(user_id)
        return await list_student_courses(user_id)

    @classmethod
    async def update_course(
        cls,
        current_user: dict,
        course_id: str,
        course_name: Optional[str] = None,
        description: Optional[str] = None,
    ) -> bool:
        role = cls.get_user_role(current_user)
        user_id = cls.get_user_id(current_user)
        if role not in cls.TEACHER_ROLES:
            raise PermissionError("只有课程主负责老师可以修改课程")
        if not user_id:
            raise PermissionError("当前教师用户缺少 user_id")
        return await update_course_record(
            course_id=course_id,
            owner_teacher_id=user_id,
            course_name=course_name,
            description=description,
        )

    @classmethod
    async def delete_course(cls, current_user: dict, course_id: str) -> bool:
        role = cls.get_user_role(current_user)
        user_id = cls.get_user_id(current_user)
        if role not in cls.TEACHER_ROLES:
            raise PermissionError("只有课程主负责老师可以删除课程")
        if not user_id:
            raise PermissionError("当前教师用户缺少 user_id")
        return await delete_course_record(
            course_id=course_id,
            owner_teacher_id=user_id,
        )

    @classmethod
    async def create_exam_item(
        cls,
        current_user: dict,
        course_id: str,
        exam_item_name: str,
        dimension_scores: Dict[str, float],
        description: Optional[str] = None,
        item_type: Optional[str] = None,
    ) -> Dict[str, object]:
        user_id = cls.get_user_id(current_user)
        role = cls.get_user_role(current_user)
        if role not in cls.TEACHER_ROLES and role not in cls.ADMIN_ROLES:
            raise PermissionError("只有课程主负责老师可以创建考试项")
        if not user_id:
            raise PermissionError("当前用户缺少 user_id")
        if not await is_course_owner(user_id, course_id):
            raise PermissionError("只有课程主负责老师可以创建考试项")
        exam_item_id = await create_exam_item_record(
            course_id=course_id,
            exam_item_name=exam_item_name,
            created_by=user_id,
            dimension_scores=dimension_scores,
            description=description,
            item_type=item_type,
        )
        return {"exam_item_id": exam_item_id}

    @classmethod
    async def list_exam_items(cls, current_user: dict, course_id: str) -> List[Dict[str, object]]:
        if not await cls.can_view_course(current_user, course_id):
            raise PermissionError("无权查看该课程的考试项")
        return await list_exam_items_by_course(course_id)

    @classmethod
    async def update_exam_item(
        cls,
        current_user: dict,
        course_id: str,
        exam_item_id: str,
        exam_item_name: Optional[str] = None,
        dimension_scores: Optional[Dict[str, float]] = None,
        description: Optional[str] = None,
        item_type: Optional[str] = None,
    ) -> bool:
        user_id = cls.get_user_id(current_user)
        if not user_id or not await is_course_owner(user_id, course_id):
            raise PermissionError("只有课程主负责老师可以修改考试项")
        return await update_exam_item_record(
            course_id=course_id,
            exam_item_id=exam_item_id,
            exam_item_name=exam_item_name,
            dimension_scores=dimension_scores,
            description=description,
            item_type=item_type,
        )

    @classmethod
    async def delete_exam_item(cls, current_user: dict, course_id: str, exam_item_id: str) -> bool:
        user_id = cls.get_user_id(current_user)
        if not user_id or not await is_course_owner(user_id, course_id):
            raise PermissionError("只有课程主负责老师可以删除考试项")
        return await delete_exam_item_record(course_id=course_id, exam_item_id=exam_item_id)

    @classmethod
    async def can_view_course(cls, current_user: dict, course_id: str) -> bool:
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

    async def put_messages(self, messages: List[Dict[str, str]]) -> List[Dict[str, str]]:
        if self.exam_state is None:
            return [self._text_event(self.MISSING_EXAM_STATE_ERROR)]
        if self.exam_finished:
            return []
        self._ensure_task_loop()

        self.history.extend(messages)
        answered_question = self.exam_state.get_current_question()
        latest_user_answer = self.get_latest_user_content(messages)
        wait_can_request_prepare_generation = True

        if answered_question and latest_user_answer:
            wait_can_request_prepare_generation = False
            await self.task_queue.put({
                "type": "judge_and_generate",
                "question": answered_question,
                "student_answer": latest_user_answer,
            })
        elif not answered_question:
            await self.request_prepare_generation_if_needed()

        return await self.wait_and_dispatch_question(
            can_request_prepare_generation=wait_can_request_prepare_generation,
        )

    def _ensure_task_loop(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self.requst_loop())

    async def requst_loop(self):
        while True:
            task = await self.task_queue.get()
            try:
                if self.exam_state is None or self.exam_finished:
                    continue
                task_type = task.get("type")
                if task_type == "judge_and_generate":
                    await self.handle_judge_and_generate_task(task)
                elif task_type == "prepare_generation":
                    try:
                        await self.ensure_ready_question_exists()
                    finally:
                        self._prepare_generation_pending = False
            finally:
                print(f"当前分数: {self.exam_state.get_score()}")
                self.task_queue.task_done()

    async def handle_judge_and_generate_task(self, task: Dict[str, object]) -> None:
        answered_question = task.get("question")
        latest_user_answer = str(task.get("student_answer", "")).strip()
        if not isinstance(answered_question, Question) or not latest_user_answer:
            return

        judge_res = await self.judge_current_answer(
            latest_user_answer,
            question=answered_question,
        )
        plan = self.exam_state.plan_next_question_after_judge(
            answered_question,
            judge_res,
        )
        if plan.should_finish:
            return
        await self.generate_questions_for_plan(plan, judge_res)

    async def request_prepare_generation_if_needed(self) -> None:
        if self.exam_state is None or self.exam_finished:
            return
        if self.exam_state.has_ready_question() or self._prepare_generation_pending:
            return
        self._prepare_generation_pending = True
        await self.task_queue.put({"type": "prepare_generation"})

    async def wait_and_dispatch_question(
        self,
        can_request_prepare_generation: bool = True,
    ) -> List[Dict[str, str]]:
        while True:
            if self.exam_state is None:
                return [self._text_event(self.MISSING_EXAM_STATE_ERROR)]
            if self.exam_finished:
                return []
            if self.exam_state.all_dimensions_finished():
                return await self.finish_interview()

            next_question = self.take_ready_question()
            if next_question is not None:
                return self.ask_question(next_question)

            if can_request_prepare_generation:
                await self.request_prepare_generation_if_needed()
            else:
                await self.task_queue.join()
                can_request_prepare_generation = True
            await asyncio.sleep(0.1)

    def get_latest_user_content(self, messages: Optional[List[Dict[str, str]]] = None) -> str:
        source_messages = messages if messages is not None else self.history
        for message in reversed(source_messages):
            if message.get("role") == "user":
                return str(message.get("content", "")).strip()
        return ""

    async def judge_current_answer(
        self,
        student_answer: str,
        question: Optional[Question] = None,
    ):
        judged_question = question
        if judged_question is None:
            return {}

        recent_history = list(self.history[-8:])
        payload = {
            "question": {
                "question_id": judged_question.question_id,
                "content": judged_question.content,
                "dimension": judged_question.dimension,
                "score": judged_question.score,
                "difficulty_level": self.get_current_difficulty_level(judged_question.dimension),
                "standard_answer": judged_question.standard_answer,
            },
            "student_answer": student_answer,
            "history": recent_history,
        }

        response = await self.stage_judger.run(history=[{
            "role": "user",
            "content": json.dumps(payload, ensure_ascii=False),
        }])
        judge_res = self.parse_outer_json_block(self.get_agent_response_content(response))

        self.exam_state.record_answer(
            question=judged_question,
            student_answer=student_answer,
            correctness_level=judge_res.get("correctness_level"),
            evaluation=judge_res.get("reason"),
        )
        return judge_res

    def get_current_difficulty_level(self, dimension: Optional[str] = None) -> int:
        if self.exam_state:
            dimension = dimension or self.exam_state.get_active_question_dimension()
            return self.exam_state.get_difficulty_level(dimension)
        return 3

    def take_ready_question(self) -> Optional[Question]:
        if not self.exam_state:
            return None
        return self.exam_state.pop_ready_question()

    async def ensure_ready_question_exists(self) -> None:
        if not self.exam_state:
            return
        if self.exam_state.has_ready_question():
            return
        plan = self.exam_state.plan_prepared_question_generation()
        await self.generate_questions_for_plan(plan)

    async def generate_questions_for_plan(
        self,
        plan: QuestionGenerationPlan,
        judge_res: Optional[dict] = None,
    ) -> None:
        if not self.exam_state or plan.should_finish:
            return
        if plan.difficulty_level is None or not plan.target_dimension:
            return
        dimension = plan.target_dimension
        questions = await self.generate_questions(
            question_count=1,
            difficulty_level=plan.difficulty_level,
            target_dimension=dimension,
            source_question=plan.source_question,
            judge_res=judge_res,
        )
        await self.insert_generated_questions(questions)

    async def insert_generated_questions(
        self,
        questions: List[Question],
        priority: int = 100,
    ) -> None:
        if self.exam_state:
            for question in questions:
                self.exam_state.add_priority_question(question, priority=priority)

    async def generate_questions(
        self,
        question_count: int = 1,
        difficulty_level: Optional[int] = None,
        target_dimension: Optional[str] = None,
        source_question: Optional[Question] = None,
        judge_res: Optional[dict] = None,
    ) -> List[Question]:
        if self.exam_state:
            dimension = target_dimension or self.exam_state.get_active_question_dimension(
                question=source_question,
            )
        else:
            dimension = target_dimension or (source_question.dimension if source_question else "default")
        active_difficulty_level = (
            self.get_current_difficulty_level(dimension)
            if difficulty_level is None
            else difficulty_level
        )
        prompt_payload = {
            "task": "Generate the next oral-exam question.",
            "target_dimension": dimension,
            "current_score": self.exam_state.get_score(dimension) if self.exam_state else None,
            "difficulty_level": active_difficulty_level,
            "dimension_rule": (
                "The generated question must stay in target_dimension. "
                "Do not switch to another assessment dimension."
            ),
            "previous_question": {
                "question_id": source_question.question_id,
                "content": source_question.content,
                "dimension": source_question.dimension,
            } if source_question else None,
            "latest_judge": judge_res,
            "history": self.history[-8:],
        }
        response = await self.exam_setter.run(
            history=[{
                "role": "user",
                "content": json.dumps(prompt_payload, ensure_ascii=False),
            }],
            difficulty_level=active_difficulty_level,
            question_count=question_count,
            question_dimensions=[dimension],
        )
        question_doc = self.parse_outer_json_block(self.get_agent_response_content(response))
        questions = []
        existing_question_count = self.get_existing_question_count()
        for index, item in enumerate(question_doc.get("questions", [])[:question_count], start=1):
            content = str(item.get("Question", item.get("question", ""))).strip()
            if not content:
                continue

            questions.append(Question(
                question_id=str(existing_question_count + index),
                content=content,
                dimension=dimension,
                score=float(item.get("score", 1.0)),
                standard_answer=item.get("standard_answer"),
                based_on_record_index=len(self.exam_state.exam_records) - 1 if self.exam_state else -1,
                source_detail=str(item.get("reason", question_doc.get("project_summary", ""))),
            ))
        return questions

    def get_existing_question_count(self) -> int:
        if not self.exam_state:
            return 0
        return (
            len(self.exam_state.exam_records)
            + len(self.exam_state.prepared_question_queue)
            + len(self.exam_state.priority_question_queue)
        )

    async def finish_interview(self) -> List[Dict[str, str]]:
        if self.exam_finished:
            return []
        self.exam_finished = True
        final_review = await self.build_final_review()
        try:
            await save_exam_data(
                current_user=self.current_user,
                exam_state=self.exam_state,
                final_review=final_review,
            )
        except Exception:
            logger.exception("保存考试数据到 MySQL 失败")
        return [
            self._text_event("AI评审开始评估"),
            self._text_event(build_final_review_output(final_review, output_type="html")),
            self._text_event("AI评审结束评估"),
            self._text_event("AI考试全部结束"),
        ]

    async def build_final_review(self) -> Dict[str, object]:
        review_payload = self.build_final_review_payload()
        response = await self.main_judger.run(history=[{
            "role": "user",
            "content": json.dumps(review_payload, ensure_ascii=False),
        }])
        review_json = self.parse_outer_json_block(self.get_agent_response_content(response))
        review_json["scores"] = self.build_score_summary()
        return review_json

    def build_final_review_payload(self) -> Dict[str, object]:
        records = []
        for index, record in enumerate(self.exam_state.exam_records, start=1):
            question = record.question
            records.append({
                "index": index,
                "question": {
                    "question_id": question.question_id,
                    "content": question.content,
                    "dimension": question.dimension,
                    "score": question.score,
                    "standard_answer": question.standard_answer,
                    "based_on_record_index": question.based_on_record_index,
                    "source_detail": question.source_detail,
                },
                "student_answer": record.student_answer,
                "correctness_level": record.correctness_level,
                "evaluation": record.evaluation,
            })
        return {
            "records": records,
            "dimension_scores": dict(self.exam_state.dimension_scores),
            "total_score": sum(self.exam_state.dimension_scores.values()),
        }

    def build_score_summary(self) -> Dict[str, object]:
        dimension_scores = dict(self.exam_state.dimension_scores)
        return {
            "dimensions": dimension_scores,
            "total": sum(dimension_scores.values()),
        }

    def ask_question(self, question: Question) -> List[Dict[str, str]]:
        if self.exam_state:
            self.exam_state.set_current_question(question)
        self.history.append({
            "role": "assistant",
            "content": question.content,
        })
        return [
            self._text_event("AI口试开始回答"),
            self._text_event(question.content),
            self._speak_event(question.content),
            self._text_event("AI口试结束回答"),
        ]

    def _text_event(self, content: str) -> Dict[str, str]:
        return {"type": "text", "content": content}

    def _speak_event(self, content: str) -> Dict[str, str]:
        return {"type": "speak", "content": content}

    def get_agent_response_content(self, response) -> str:
        """获取 langchain 的 Agent 结果的最后一个消息内容，作为 Agent 输出。"""
        message = response["messages"][-1]
        content = getattr(message, "content", None)
        if content is None and isinstance(message, dict):
            content = message.get("content")
        if isinstance(content, list):
            return "".join(str(item.get("text", item)) if isinstance(item, dict) else str(item) for item in content)
        return str(content)

    def parse_outer_json_block(self, response: str) -> dict:
        """解析 Agent 结果中的 JSON 块，返回 JSON 对象。"""
        text = response.strip()

        if text.startswith("```"):
            text = re.sub(r"^```(?:json|JSON)?\s*\n?", "", text, count=1)
            text = re.sub(r"\n?```\s*$", "", text, count=1)

        if not text.startswith("{"):
            obj_match = re.search(r"\{.*\}", text, flags=re.DOTALL)
            if not obj_match:
                raise ValueError("No JSON object found in model response")
            text = obj_match.group(0)

        return json.loads(text)
