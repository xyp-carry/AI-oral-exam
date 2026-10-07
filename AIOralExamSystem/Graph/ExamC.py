import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from AIOralExamSystem.Agent.General_Agent import GeneralAgent
from AIOralExamSystem.Exam.QAmanagerC import QAmanagerC
from AIOralExamSystem.Graph.ExamA import parse_agent_json, question_value


class CExamRecordStore:
    """Keep a compact JSON record of one C-mode exam."""

    def __init__(
        self,
        exam_id: str = "",
        user_id: str = "",
        exam_item_id: str = "",
        output_dir: Optional[Path] = None,
    ):
        self.exam_id = str(exam_id or "")
        self.user_id = str(user_id or "")
        self.exam_item_id = str(exam_item_id or "")
        self.status = "running"
        self.final_review: Dict[str, Any] = {}
        self.records: List[Dict[str, Any]] = []
        self._record_index: Dict[str, int] = {}
        repo_root = Path(__file__).resolve().parents[2]
        self.output_dir = Path(output_dir or repo_root / "exam_records" / "c_mode")
        self.output_path = self.output_dir / f"{self._safe_name(self.exam_id or 'unknown')}.json"

    def start_question(
        self,
        question: Mapping[str, Any],
        probe_turn_count: int = 0,
        source_generation: Optional[Mapping[str, Any]] = None,
    ) -> None:
        question_id = self._question_id(question)
        if not question_id:
            return
        existing = self._find_record(question_id)
        if existing is not None:
            existing["question"] = self._question_summary(question)
            self.save()
            return
        record = {
            "_question_id": question_id,
            "question": self._question_summary(question),
            "pre_generated_questions": [],
            "answer_fragments": [],
            "next_question": {},
        }
        self._record_index[question_id] = len(self.records)
        self.records.append(record)
        self.save()

    def update_answer(self, question_id: str, answer: Mapping[str, Any]) -> None:
        return

    def append_judgement(self, question_id: str, judgement: Mapping[str, Any]) -> None:
        record = self._find_record(question_id)
        if record is None:
            return
        result = judgement.get("result") if isinstance(judgement, Mapping) else {}
        if not isinstance(result, Mapping):
            result = {}
        payload = judgement.get("payload") if isinstance(judgement, Mapping) else {}
        if not isinstance(payload, Mapping):
            payload = {}
        fragment = {
            "text": str(payload.get("input_text") or ""),
            "fixed_text": str(result.get("fixed_text") or result.get("corrected_text") or ""),
            "has_error": self._truthy(result.get("has_fundamental_error")),
            "hit_core_point": self._truthy(result.get("has_core_point")),
            "matched_core_points": self._text_list(result.get("matched_core_points")),
        }
        record.setdefault("answer_fragments", []).append(fragment)
        self.save()

    def mark_generation_started(self, source_question_id: str, meta: Mapping[str, Any]) -> None:
        self._upsert_generation(source_question_id, meta, {})

    def mark_generation_finished(self, source_question_id: str, generation: Mapping[str, Any]) -> None:
        self._upsert_generation(
            source_question_id,
            generation,
            {"question": self._question_summary(generation.get("question") or {})},
        )

    def mark_generation_selected(
        self,
        source_question_id: str,
        generation_id: str,
        next_question: Mapping[str, Any],
    ) -> None:
        record = self._find_record(source_question_id)
        if record is None:
            return
        record["next_question"] = self._question_summary(next_question)
        self.save()

    def mark_question_completed(self, question_id: str, reason: str = "") -> None:
        return

    def finish_exam(
        self,
        status: str = "finished",
        reason: str = "",
        probe: Optional[Mapping[str, Any]] = None,
        final_review: Optional[Mapping[str, Any]] = None,
    ) -> None:
        self.status = str(status or "finished")
        if final_review:
            self.final_review = deepcopy(dict(final_review))
        self.save()

    def snapshot(self) -> Dict[str, Any]:
        return {
            "exam_id": self.exam_id,
            "exam_item_id": self.exam_item_id,
            "user_id": self.user_id,
            "mode": "C",
            "status": self.status,
            "final_review": deepcopy(self.final_review),
            "records": [self._public_record(record) for record in self.records],
        }

    def save(self) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(self.snapshot(), ensure_ascii=False, indent=2, default=str)
        tmp_path = self.output_path.with_suffix(self.output_path.suffix + ".tmp")
        tmp_path.write_text(payload, encoding="utf-8")
        tmp_path.replace(self.output_path)

    def _upsert_generation(
        self,
        source_question_id: str,
        generation: Mapping[str, Any],
        updates: Mapping[str, Any],
    ) -> None:
        record = self._find_record(source_question_id)
        if record is None:
            return
        generation_id = str(generation.get("generation_id") or "")
        if not generation_id:
            return
        generated_questions = record.setdefault("pre_generated_questions", [])
        target = None
        for existing in generated_questions:
            if str(existing.get("_generation_id") or "") == generation_id:
                target = existing
                break
        if target is None:
            target = {"_generation_id": generation_id, "question": {}}
            generated_questions.append(target)
        target.update(deepcopy(dict(updates or {})))
        self.save()

    def _find_record(self, question_id: str) -> Optional[Dict[str, Any]]:
        index = self._record_index.get(str(question_id or ""))
        if index is None or index < 0 or index >= len(self.records):
            return None
        return self.records[index]

    def _public_record(self, record: Mapping[str, Any]) -> Dict[str, Any]:
        pre_generated_questions = []
        for item in record.get("pre_generated_questions") or []:
            if not isinstance(item, Mapping):
                continue
            question = item.get("question") if isinstance(item.get("question"), Mapping) else {}
            if question.get("question_content") or question.get("standard_answer"):
                pre_generated_questions.append(deepcopy(dict(question)))
        public = {
            "question": deepcopy(dict(record.get("question") or {})),
            "pre_generated_questions": pre_generated_questions,
            "answer_fragments": deepcopy(list(record.get("answer_fragments") or [])),
            "next_question": deepcopy(dict(record.get("next_question") or {})),
        }
        if not public["next_question"]:
            public.pop("next_question")
        return public

    @staticmethod
    def _question_id(question: Any) -> str:
        if not isinstance(question, Mapping):
            return ""
        return str(question.get("question_id") or "")

    @staticmethod
    def _question_summary(question: Any) -> Dict[str, Any]:
        if not isinstance(question, Mapping):
            return {}
        return {
            "question_content": question_value(
                question,
                ("question_content", "content", "question", "text"),
            ),
            "standard_answer": question_value(
                question,
                ("standard_answer", "reference_answer", "answer_key", "correct_answer"),
            ),
        }

    @staticmethod
    def _safe_name(value: str) -> str:
        safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "_" for ch in value)
        return safe.strip("_") or "unknown"

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes", "y"}

    @staticmethod
    def _text_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value or "").strip()
        return [text] if text else []


class CAnswerJudgementRunner:
    """Judge one immutable version of a C-mode answer input."""

    RESULT_KEYS = {
        "fixed_text",
        "corrected_text",
        "has_core_point",
        "core_point_focus",
        "matched_core_points",
        "has_fundamental_error",
        "fundamental_error_points",
        "answer_status",
        "reason",
        "confidence",
    }

    def __init__(
        self,
        answer_judger: Optional[Any] = None,
        model_settings: Optional[Mapping[str, Any]] = None,
    ):
        self.answer_judger = answer_judger
        self.model_settings = dict(model_settings or {})
        self.general_agent: Optional[GeneralAgent] = None
        if self.answer_judger is None and self.model_settings.get("model_name"):
            self.general_agent = GeneralAgent(
                self.model_settings,
                thinking=True,
                response_format=True,
                temperature=float(self.model_settings.get("temperature", 0)),
                name="ExamCAnswerJudgementAgent",
            )

    async def run(
        self,
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
        input_text: str,
    ) -> Dict[str, Any]:
        payload = self.build_payload(question, answer, input_text)
        version = int(payload.get("answer_version") or 0)
        try:
            response = await self._call_agent(payload)
            if isinstance(response, Mapping) and response.get("agent_error"):
                raise RuntimeError(str(response["agent_error"]))
            result = self._normalize_result(response)
            status, error = "done", ""
        except Exception as exc:
            result = {}
            status, error = "error", str(exc)
        return {
            "type": "c_judgement_result",
            "job_id": f"c_judge_{version:08d}",
            "question_id": payload["question_id"],
            "answer_version": version,
            "finished": payload["finished"],
            "status": status,
            "error": error,
            "payload": payload,
            "result": result,
        }

    async def _call_agent(self, payload: Dict[str, Any]) -> Any:
        system_prompt, user_prompt = self.build_prompts(payload)
        if self.answer_judger is not None:
            if hasattr(self.answer_judger, "execute"):
                return await self.answer_judger.execute(system_prompt, user_prompt)
            if hasattr(self.answer_judger, "run"):
                return await self.answer_judger.run(payload=payload)
            raise RuntimeError("ANSWER_JUDGER_NOT_CALLABLE")
        if self.general_agent is None:
            raise RuntimeError("ANSWER_JUDGER_NOT_CONFIGURED")
        return await self.general_agent.execute(system_prompt, user_prompt)

    @staticmethod
    def build_payload(
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
        input_text: str,
    ) -> Dict[str, Any]:
        normalized_question = dict(question or {})
        return {
            "question_id": str(normalized_question.get("question_id") or ""),
            "question": question_value(
                normalized_question,
                ("question_content", "content", "question", "text"),
            ),
            "standard_answer": question_value(
                normalized_question,
                (
                    "standard_answer",
                    "reference_answer",
                    "answer_key",
                    "correct_answer",
                ),
            ),
            "input_text": str(input_text or ""),
            "answer_version": int(answer.get("version") or 0),
            "finished": bool(answer.get("finished")),
        }

    @staticmethod
    def build_prompts(payload: Mapping[str, Any]) -> tuple[str, str]:
        system_prompt = """
你是口试回答判断 Agent。你只判断用户本次输入文本，不使用历史回答上下文，也不判断是否值得继续深挖。

你只做三件事：
1. 对 input_text 中明显由 STT 识别导致的专业名词、技术术语、英文缩写、框架名、库名、API 名称、算法名、协议名、命令名进行规范化。
2. 判断 input_text 是否明确说出了与当前题目、standard_answer/reference_answer 匹配的正确核心观点。
3. 判断 input_text 是否出现了与当前题目直接相关的完全错误常识、严重事实错误、严重定义错误、严重因果错误或严重技术结论错误。

专业名词规范规则：
- 只修正明显的 STT 或转写错误，例如同音误识别、大小写错误、英文术语转写错误、技术名词误拆分。
- 可以把口语化或误识别的技术词规范为常见写法，例如“瑞艾克特”规范为“React”，“派森”规范为“Python”。
- 不得改写用户的技术结论。
- 不得纠正事实性错误。
- 不得补充新信息。
- 不得扩写答案。
- 不得把模糊表述改成确定表述。
- 必须保留用户原本的肯定、否定、条件、数值和因果关系。

判断规则：
- input_text 是唯一判断对象；不要根据历史上下文、上一轮追问或用户可能想表达的意思补全。
- standard_answer/reference_answer 只作为参考标准，用来识别核心观点和严重错误。
- 只把明确表达出来的内容算作核心观点。
- 泛泛而谈、复述题目、无关铺垫、模糊表态都不算核心观点。
- 只有明显错误且会误导后续回答的问题，才标记为 has_fundamental_error=true。
- 不要判断“是否可以深挖”。
- 不要生成追问。
- 不要输出 missing/deepen/completion 相关结论。

只返回一个 JSON 对象，不要返回 Markdown、解释或额外文本。
JSON 字段必须包含：
{
  "fixed_text": "规范化后的本次输入文本",
  "has_core_point": false,
  "matched_core_points": [],
  "core_point_focus": "",
  "has_fundamental_error": false,
  "fundamental_error_points": [],
  "answer_status": "partial|enough|off_topic|unclear",
  "reason": "简短判断理由",
  "confidence": 0.0
}
        """.strip()
        user_prompt = json.dumps(
            {
                "question_id": payload.get("question_id"),
                "question": payload.get("question"),
                "standard_answer": payload.get("standard_answer"),
                "input_text": payload.get("input_text"),
                "answer_version": payload.get("answer_version"),
                "finished": payload.get("finished"),
            },
            ensure_ascii=False,
            indent=2,
        )
        return system_prompt, user_prompt

    @classmethod
    def _normalize_result(cls, response: Any) -> Dict[str, Any]:
        if isinstance(response, Mapping):
            structured = response.get("structured_response")
            if isinstance(structured, Mapping):
                result = dict(structured)
                return cls._with_corrected_alias(result)
            if cls.RESULT_KEYS.intersection(response):
                return cls._with_corrected_alias(dict(response))
            if not any(key in response for key in ("messages", "content", "response")):
                return cls._with_corrected_alias(dict(response))

        result = parse_agent_json(response)
        return cls._with_corrected_alias(result)

    @staticmethod
    def _with_corrected_alias(result: Dict[str, Any]) -> Dict[str, Any]:
        fixed_text = str(
            result.get("fixed_text")
            or result.get("corrected_text")
            or result.get("fixed_accumulated_answer")
            or result.get("corrected_answer")
            or ""
        ).strip()
        if fixed_text:
            result["fixed_text"] = fixed_text
            result["corrected_text"] = fixed_text
        return result


class CFollowupQuestionRunner:
    """Generate a follow-up question for one C-mode judgement result."""

    def __init__(
        self,
        question_generator: Optional[Any] = None,
        model_settings: Optional[Mapping[str, Any]] = None,
    ):
        self.question_generator = question_generator
        self.model_settings = dict(model_settings or {})
        self.general_agent: Optional[GeneralAgent] = None
        if self.question_generator is None and self.model_settings.get("model_name"):
            self.general_agent = GeneralAgent(
                self.model_settings,
                thinking=True,
                response_format=True,
                temperature=float(self.model_settings.get("temperature", 0)),
                name="ExamCFollowupQuestionAgent",
            )

    @property
    def configured(self) -> bool:
        return self.question_generator is not None or self.general_agent is not None

    async def run(
        self,
        generation_id: str,
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
        judgement: Mapping[str, Any],
        input_text: str,
    ) -> Dict[str, Any]:
        payload = self.build_payload(question, answer, judgement, input_text)
        try:
            response = await self._call_agent(payload)
            if isinstance(response, Mapping) and response.get("agent_error"):
                raise RuntimeError(str(response["agent_error"]))
            result = self._normalize_result(response)
            relation = self._relation_from(payload, result)
            generated_question = self._normalize_question(result, relation)
            status, error = "done", ""
        except Exception as exc:
            result = {}
            generated_question = None
            relation = str(payload.get("relation") or "deepen")
            status, error = "error", str(exc)
        return {
            "type": "c_followup_question_result",
            "generation_id": generation_id,
            "source_question_id": payload["source_question_id"],
            "source_answer_version": payload["answer_version"],
            "relation": relation,
            "status": status,
            "error": error,
            "payload": payload,
            "question": generated_question,
            "result": result,
        }

    async def _call_agent(self, payload: Dict[str, Any]) -> Any:
        system_prompt, user_prompt = self.build_prompts(payload)
        if self.question_generator is not None:
            if hasattr(self.question_generator, "execute"):
                return await self.question_generator.execute(system_prompt, user_prompt)
            if hasattr(self.question_generator, "run"):
                return await self.question_generator.run(payload=payload)
            raise RuntimeError("QUESTION_GENERATOR_NOT_CALLABLE")
        if self.general_agent is None:
            raise RuntimeError("QUESTION_GENERATOR_NOT_CONFIGURED")
        return await self.general_agent.execute(system_prompt, user_prompt)

    @staticmethod
    def build_payload(
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
        judgement: Mapping[str, Any],
        input_text: str,
    ) -> Dict[str, Any]:
        normalized_question = dict(question or {})
        result = dict(judgement.get("result") or {})
        relation = str(result.get("relation") or "deepen").strip().lower()
        if relation not in QAmanagerC.VALID_RELATIONS:
            relation = "deepen"
        matched_core_points = CFollowupQuestionRunner._text_list(
            result.get("matched_core_points")
        )
        fundamental_error_points = CFollowupQuestionRunner._text_list(
            result.get("fundamental_error_points")
        )
        generation_focus = CFollowupQuestionRunner._first_text(
            result.get("generation_focus"),
            result.get("core_point_focus"),
            matched_core_points,
            fundamental_error_points,
            default="标准答案核心要点",
        )
        return {
            "source_question_id": str(normalized_question.get("question_id") or ""),
            "parent_question": question_value(
                normalized_question,
                ("question_content", "content", "question", "text"),
            ),
            "standard_answer": question_value(
                normalized_question,
                (
                    "standard_answer",
                    "reference_answer",
                    "answer_key",
                    "correct_answer",
                ),
            ),
            "input_text": str(input_text or ""),
            "fixed_text": str(
                result.get("fixed_text")
                or result.get("corrected_text")
                or input_text
                or ""
            ),
            "answer_version": int(answer.get("version") or 0),
            "relation": relation,
            "followup_kind": str(result.get("followup_kind") or ""),
            "generation_focus": generation_focus,
            "judgement_route": str(result.get("judgement_route") or ""),
            "core_point_focus": str(result.get("core_point_focus") or ""),
            "matched_core_points": matched_core_points,
            "fundamental_error_points": fundamental_error_points,
            "answer_status": str(result.get("answer_status") or ""),
            "judgement": result,
        }

    @staticmethod
    def build_prompts(payload: Mapping[str, Any]) -> tuple[str, str]:
        system_prompt = """
你是口试追问题目生成 Agent。你会收到当前父题、参考答案、用户本次输入、专业名词规范化后的文本、候选追问类型，以及回答判断结果。

任务：生成一道可以接在当前父题之后的口试追问题。

规则：
- followup_kind=missing_core 时，生成问题A：围绕 standard_answer/reference_answer 的关键核心点提问，用于面试者没有明确说出正确核心观点的情况，不要直接泄露答案。
- followup_kind=keyword_deepen 时，生成问题B：围绕 matched_core_points、core_point_focus 或 generation_focus 中已经命中的正确核心观点继续追问。
- followup_kind=serious_error 时，生成问题C：围绕 fundamental_error_points 或 generation_focus 中的严重错误点提问，目标是确认用户为什么产生该错误理解。
- 只生成一道题，不要生成多个备选。
- 题目必须适合口试现场直接问出。
- 不要在题目中泄露标准答案。

只返回一个 JSON 对象，不要返回 Markdown、解释或额外文本。
JSON 字段必须包含：
{
  "question_content": "追问题目",
  "standard_answer": "参考答案或参考要点",
  "relation": "deepen",
  "reason": "为什么生成这道追问"
}
        """.strip()
        user_prompt = json.dumps(
            {
                "parent_question": payload.get("parent_question"),
                "standard_answer": payload.get("standard_answer"),
                "input_text": payload.get("input_text"),
                "fixed_text": payload.get("fixed_text"),
                "relation": payload.get("relation"),
                "followup_kind": payload.get("followup_kind"),
                "generation_focus": payload.get("generation_focus"),
                "judgement_route": payload.get("judgement_route"),
                "core_point_focus": payload.get("core_point_focus"),
                "matched_core_points": payload.get("matched_core_points"),
                "fundamental_error_points": payload.get("fundamental_error_points"),
                "answer_status": payload.get("answer_status"),
                "judgement": payload.get("judgement"),
            },
            ensure_ascii=False,
            indent=2,
        )
        return system_prompt, user_prompt

    @staticmethod
    def _text_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value or "").strip()
        return [text] if text else []

    @staticmethod
    def _first_text(*values: Any, default: str = "") -> str:
        for value in values:
            if isinstance(value, list):
                for item in value:
                    text = str(item or "").strip()
                    if text:
                        return text
                continue
            text = str(value or "").strip()
            if text:
                return text
        return default

    @staticmethod
    def _normalize_result(response: Any) -> Dict[str, Any]:
        if isinstance(response, Mapping):
            structured = response.get("structured_response")
            if isinstance(structured, Mapping):
                return dict(structured)
            if not any(key in response for key in ("messages", "content", "response")):
                return dict(response)
        return parse_agent_json(response)

    @staticmethod
    def _normalize_question(
        result: Mapping[str, Any],
        relation: str,
    ) -> Dict[str, Any]:
        source = dict(result or {})
        nested_question = source.get("question")
        if isinstance(nested_question, Mapping):
            source.update(dict(nested_question))
        content = question_value(
            source,
            ("question_content", "content", "question", "text"),
        )
        if not content:
            raise ValueError("FOLLOWUP_QUESTION_CONTENT_REQUIRED")
        standard_answer = question_value(
            source,
            ("standard_answer", "reference_answer", "answer_key", "correct_answer"),
        )
        normalized_relation = str(source.get("relation") or relation).strip().lower()
        if normalized_relation not in QAmanagerC.VALID_RELATIONS:
            normalized_relation = relation
        question = dict(source)
        question["question_content"] = content
        question["content"] = content
        question["relation"] = normalized_relation
        if standard_answer:
            question["standard_answer"] = standard_answer
        return question

    @staticmethod
    def _relation_from(
        payload: Mapping[str, Any],
        result: Mapping[str, Any],
    ) -> str:
        relation = str(result.get("relation") or payload.get("relation") or "deepen")
        relation = relation.strip().lower()
        if relation not in QAmanagerC.VALID_RELATIONS:
            return "deepen"
        return relation


class CQuestionEvaluationRunner:
    """Evaluate a completed C-mode answer against the question standard."""

    RESULT_KEYS = {
        "is_correct", "correctness", "coverage", "summary",
        "missing_points", "wrong_points", "reason", "confidence",
    }

    def __init__(self, question_evaluator=None, model_settings=None):
        self.question_evaluator = question_evaluator
        self.model_settings = dict(model_settings or {})
        self.general_agent = None
        if self.question_evaluator is None and self.model_settings.get("model_name"):
            self.general_agent = GeneralAgent(
                self.model_settings,
                thinking=True,
                response_format=True,
                temperature=float(self.model_settings.get("temperature", 0)),
                name="ExamCQuestionEvaluationAgent",
            )

    @property
    def configured(self):
        return self.question_evaluator is not None or self.general_agent is not None

    async def run(self, question, answer):
        payload = self.build_payload(question, answer)
        try:
            response = await self._call_agent(payload)
            if isinstance(response, Mapping) and response.get("agent_error"):
                raise RuntimeError(str(response["agent_error"]))
            result = self._normalize_result(response)
            status, error = "done", ""
        except Exception as exc:
            result = {}
            status, error = "error", str(exc)
        return {
            "type": "c_question_evaluation_result",
            "question_id": payload["question_id"],
            "answer_version": payload["answer_version"],
            "status": status,
            "error": error,
            "payload": payload,
            "result": result,
        }

    async def _call_agent(self, payload):
        system_prompt, user_prompt = self.build_prompts(payload)
        if self.question_evaluator is not None:
            if hasattr(self.question_evaluator, "execute"):
                return await self.question_evaluator.execute(system_prompt, user_prompt)
            if hasattr(self.question_evaluator, "run"):
                return await self.question_evaluator.run(payload=payload)
            raise RuntimeError("QUESTION_EVALUATOR_NOT_CALLABLE")
        if self.general_agent is None:
            raise RuntimeError("QUESTION_EVALUATOR_NOT_CONFIGURED")
        return await self.general_agent.execute(system_prompt, user_prompt)

    @staticmethod
    def build_payload(question, answer):
        normalized_question = dict(question or {})
        return {
            "question_id": str(normalized_question.get("question_id") or ""),
            "question": question_value(normalized_question, ("question_content", "content", "question", "text")),
            "standard_answer": question_value(normalized_question, ("standard_answer", "reference_answer", "answer_key", "correct_answer")),
            "student_answer": str(answer.get("corrected_text") or answer.get("text") or ""),
            "answer_version": int(answer.get("version") or 0),
            "finished": bool(answer.get("finished")),
        }

    @staticmethod
    def build_prompts(payload):
        system_prompt = """
你是C模式口试单题评价 Agent。你只依据输入的 question、standard_answer 和 student_answer 判断学生是否回答正确。

规则：
- student_answer 是学生对当前题的完整回答。
- standard_answer/reference_answer 是判断依据；如果标准答案为空，则根据题目和常识谨慎评价。
- 不要因为学生没有逐字复述标准答案就判错，重点看核心含义是否覆盖。
- 不要编造学生没有表达的内容。
- 不生成追问，不重新设计题目。

只返回一个 JSON 对象，不要返回 Markdown、解释或额外文本。
JSON 字段必须包含：
{
  "is_correct": false,
  "correctness": "correct|partially_correct|incorrect|unclear",
  "coverage": "充分|部分|不足|无法判断",
  "summary": "对本题回答质量的简短总结",
  "missing_points": [],
  "wrong_points": [],
  "reason": "判断理由",
  "confidence": 0.0
}
        """.strip()
        user_prompt = json.dumps({
            "question_id": payload.get("question_id"),
            "question": payload.get("question"),
            "standard_answer": payload.get("standard_answer"),
            "student_answer": payload.get("student_answer"),
            "answer_version": payload.get("answer_version"),
            "finished": payload.get("finished"),
        }, ensure_ascii=False, indent=2)
        return system_prompt, user_prompt

    @classmethod
    def _normalize_result(cls, response):
        if isinstance(response, Mapping):
            structured = response.get("structured_response")
            if isinstance(structured, Mapping):
                return dict(structured)
            if cls.RESULT_KEYS.intersection(response):
                return dict(response)
            if not any(key in response for key in ("messages", "content", "response")):
                return dict(response)
        return parse_agent_json(response)


class ExamCFlow:
    """Receive answer input, judge it, and select one prepared follow-up question."""

    EVENT_START = "start"
    EVENT_ANSWER_CHUNK = "answer_chunk"
    EVENT_ANSWER_END = "answer_end"
    EVENT_JUDGE_REQUEST = "judge_request"
    EVENT_POLL_GENERATED_QUESTION = "poll_generated_question"
    EVENT_FINISH = "finish"
    EVENT_STOP = "stop"

    FOLLOWUP_KIND_MISSING_CORE = "missing_core"
    FOLLOWUP_KIND_KEYWORD_DEEPEN = "keyword_deepen"
    FOLLOWUP_KIND_SERIOUS_ERROR = "serious_error"
    FOLLOWUP_KIND_LABELS = {
        FOLLOWUP_KIND_MISSING_CORE: "A",
        FOLLOWUP_KIND_KEYWORD_DEEPEN: "B",
        FOLLOWUP_KIND_SERIOUS_ERROR: "C",
    }

    def __init__(
        self,
        qa_manager: QAmanagerC,
        answer_judger: Optional[Any] = None,
        model_settings: Optional[Mapping[str, Any]] = None,
        followup_generator: Optional[Any] = None,
        exam_id: str = "",
        user_id: str = "",
        exam_item_id: str = "",
        **_: Any,
    ):
        self.qa_manager = qa_manager
        self.judgement_runner = CAnswerJudgementRunner(
            answer_judger,
            model_settings,
        )
        self.question_runner = CFollowupQuestionRunner(
            followup_generator,
            model_settings,
        )
        self.question_evaluation_runner = CQuestionEvaluationRunner(
            model_settings=model_settings,
        )
        self.exam_id = str(exam_id or "")
        self.user_id = str(user_id or "")
        self.exam_item_id = str(exam_item_id or "")
        self.exam_record = CExamRecordStore(
            exam_id=self.exam_id,
            user_id=self.user_id,
            exam_item_id=self.exam_item_id,
        )
        self.answer_question_id: Optional[str] = None
        self.answer_segments: List[str] = []
        self.corrected_answer_segments: List[str] = []
        self.answer_version = 0
        self.answer_finished = False
        self.latest_judgement: Optional[Dict[str, Any]] = None
        self.probe_initial_score = 3
        self.probe_score = self.probe_initial_score
        self.probe_turn_count = 0
        self.max_probe_turns = 2
        self.probe_score_history: List[Dict[str, Any]] = []
        self.last_followup_kind = "none"
        self.last_followup_focus = ""
        self.question_generation_seq = 0
        self.pending_question_tasks: Dict[str, asyncio.Task] = {}
        self.pending_question_meta: Dict[str, Dict[str, Any]] = {}
        self.generated_question_buffer: List[Dict[str, Any]] = []
        self.generation_errors: List[Dict[str, Any]] = []
        self.completed_root_reviews: List[Dict[str, Any]] = []
        self.question_answers: Dict[str, Dict[str, Any]] = {}
        self.question_evaluation_tasks: Dict[str, asyncio.Task] = {}
        self.question_evaluations: Dict[str, Dict[str, Any]] = {}
        self.question_evaluation_errors: List[Dict[str, Any]] = []
        self.answer_trace: List[Dict[str, Any]] = []
        self.answer_signals = self._new_answer_signals()

    async def run(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        self._collect_finished_generation_tasks()
        self._collect_finished_question_evaluation_tasks()
        event = dict(event or {})
        event_type = self._event_type(event)
        if event_type == self.EVENT_START:
            return self._start()
        if event_type == self.EVENT_ANSWER_CHUNK:
            return await self._receive_answer(event, finished=False)
        if event_type in {self.EVENT_ANSWER_END, self.EVENT_JUDGE_REQUEST}:
            return await self._receive_answer(event, finished=True)
        if event_type == self.EVENT_POLL_GENERATED_QUESTION:
            return self._poll_generated_question()
        if event_type in {self.EVENT_FINISH, self.EVENT_STOP}:
            final_review_event = (
                self._final_review_message(event_type)
                if event_type == self.EVENT_FINISH
                else None
            )
            self._finish_exam_record(
                "closed",
                event_type,
                final_review=(
                    final_review_event.get("review")
                    if isinstance(final_review_event, Mapping)
                    else None
                ),
            )
            messages = []
            if final_review_event is not None:
                messages.append(final_review_event)
            messages.append({
                "type": "closed",
                "exam_completed": self.qa_manager.selection_complete,
                "pending_generation_ids": self._pending_generation_ids(),
                "exam_record_path": self.exam_record_path,
            })
            return messages
        return [{"type": "waiting", "reason": "unknown_event"}]

    def _start(self) -> List[Dict[str, Any]]:
        if self.qa_manager.current_question is not None:
            return [{"type": "waiting", "reason": "already_started"}]
        question = self.qa_manager.start()
        if question is None:
            return [{"type": "error", "error": "QUESTION_QUEUE_REQUIRED"}]
        self._reset_probe_state()
        self._reset_answer(str(question.get("question_id") or ""))
        self._record_question_started(question)
        messages = [{"type": "question", "question": question}]
        messages.extend(self._start_prefetched_followups(question))
        return messages

    async def _receive_answer(
        self,
        event: Dict[str, Any],
        finished: bool,
    ) -> List[Dict[str, Any]]:
        question = self.qa_manager.current_question
        if question is None:
            return [{"type": "waiting", "reason": "no_active_question"}]

        current_id = str(question.get("question_id") or "")
        supplied_id = str(event.get("question_id") or "")
        if supplied_id and supplied_id != current_id:
            return [{"type": "error", "error": "QUESTION_ID_NOT_CURRENT"}]
        if self.answer_question_id != current_id:
            self._reset_answer(current_id)
            self._start_prefetched_followups(question)
        if self.answer_finished:
            return [{"type": "error", "error": "ANSWER_ALREADY_FINISHED"}]

        input_text = str(event.get("text", event.get("answer")) or "").strip()
        if not input_text and not finished:
            return [{"type": "error", "error": "ANSWER_CHUNK_REQUIRED"}]
        if input_text:
            self.answer_segments.append(input_text)
            self.corrected_answer_segments.append(input_text)

        # An end marker also gets a new version because finished changes.
        self.answer_version += 1
        self.answer_finished = bool(finished)
        answer = self.answer_snapshot
        self._remember_question_answer(current_id, answer)
        self._record_answer_updated(current_id, answer)
        messages = [{
            "type": "answer_received",
            "question_id": current_id,
            "answer": answer,
        }]

        root_completed = False
        had_core_point_before_finish = self._truthy(
            self.answer_signals.get("has_core_point")
        )
        judgement = None
        if input_text and not (finished and had_core_point_before_finish):
            judgement = await self._run_answer_judgement(
                question=question,
                answer=answer,
                input_text=input_text,
                timeout_secs=7.0 if finished else None,
            )

            self._apply_corrected_text(answer, judgement, has_new_text=True)
            self._remember_question_answer(current_id, answer)
            self._record_answer_updated(current_id, answer)
            score_message = None
            if judgement.get("status") == "done":
                self._normalize_judgement_result(judgement)
                score_message = self._apply_probe_score(current_id, judgement)
                self._update_answer_signals(current_id, judgement)
                self.qa_manager.record_judgement(
                    question=question,
                    answer=answer,
                    judgement=judgement,
                )
            elif finished and judgement.get("error") == "ANSWER_JUDGEMENT_TIMEOUT":
                self._mark_core_point_timeout(current_id)
            self._record_judgement(current_id, judgement)
            self.latest_judgement = deepcopy(judgement)
            messages.extend(self._build_judgement_outputs(current_id, answer, judgement))
            if score_message is not None:
                messages.append(score_message)
            if self._should_start_serious_error_generation(judgement):
                generation_message = self._start_followup_generation(
                    question=question,
                    answer=answer,
                    judgement=judgement,
                    input_text=input_text,
                    followup_kind=self.FOLLOWUP_KIND_SERIOUS_ERROR,
                    event_type="followup_prefetch_started",
                )
                if generation_message is not None:
                    messages.append(generation_message)

        if finished and self._truthy(self.answer_signals.get("has_core_point")):
            self._start_question_evaluation(question, self.answer_snapshot)

        if finished:
            completion_message = self._probe_completion_message(current_id)
            if completion_message is not None:
                messages.append(completion_message)
                messages.extend(
                    self._complete_current_root_after_probe(
                        current_id,
                        str(completion_message.get("reason") or "probe_completed"),
                    )
                )
                root_completed = True
            if not root_completed:
                messages.extend(self._generation_status_messages(current_id))
        return messages


    def _remember_question_answer(
        self,
        question_id: str,
        answer: Mapping[str, Any],
    ) -> None:
        if question_id:
            self.question_answers[str(question_id)] = deepcopy(dict(answer or {}))

    async def _run_answer_judgement(
        self,
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
        input_text: str,
        timeout_secs: Optional[float] = None,
    ) -> Dict[str, Any]:
        run_coro = self.judgement_runner.run(
            question=dict(question),
            answer=answer,
            input_text=input_text,
        )
        if timeout_secs is None:
            return await run_coro
        try:
            return await asyncio.wait_for(run_coro, timeout=timeout_secs)
        except asyncio.TimeoutError:
            return {
                "type": "c_judgement_result",
                "job_id": f"c_judge_{int(answer.get('version') or 0):08d}",
                "question_id": str(question.get("question_id") or ""),
                "answer_version": int(answer.get("version") or 0),
                "finished": bool(answer.get("finished")),
                "status": "error",
                "error": "ANSWER_JUDGEMENT_TIMEOUT",
                "payload": CAnswerJudgementRunner.build_payload(
                    question,
                    answer,
                    input_text,
                ),
                "result": {},
            }

    def _mark_core_point_timeout(self, question_id: str) -> None:
        self.answer_signals["has_core_missing"] = True
        self.answer_signals["latest_route"] = "core_point_missing"
        self.answer_signals["latest_focus"] = self.answer_signals.get(
            "missing_focus",
            "标准答案核心要点",
        )
        self.answer_trace.append({
            "question_id": question_id,
            "answer_version": self.answer_version,
            "route": "core_point_missing",
            "focus": self.answer_signals["latest_focus"],
            "has_core_point": False,
            "matched_core_points": [],
            "core_point_focus": "",
            "has_fundamental_error": False,
            "fundamental_error_points": [],
            "answer_status": "timeout_default_missing",
        })

    def _start_question_evaluation(
        self,
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
    ) -> None:
        question_id = str(question.get("question_id") or "")
        student_answer = str(answer.get("corrected_text") or answer.get("text") or "").strip()
        if not question_id or not student_answer:
            return
        if not self.question_evaluation_runner.configured:
            self.question_evaluation_errors.append({
                "question_id": question_id,
                "answer_version": int(answer.get("version") or 0),
                "status": "error",
                "error": "QUESTION_EVALUATOR_NOT_CONFIGURED",
            })
            return
        existing = self.question_evaluations.get(question_id)
        if existing and int(existing.get("answer_version") or 0) >= int(answer.get("version") or 0):
            return
        running = self.question_evaluation_tasks.get(question_id)
        if running is not None and not running.done():
            return
        task = asyncio.create_task(
            self.question_evaluation_runner.run(
                question=dict(question),
                answer=dict(answer),
            )
        )
        self.question_evaluation_tasks[question_id] = task
        task.add_done_callback(
            lambda done, qid=question_id: self._store_question_evaluation_result(qid, done)
        )

    def _store_question_evaluation_result(self, question_id: str, task: asyncio.Task) -> None:
        if self.question_evaluation_tasks.get(question_id) is not task:
            return
        self.question_evaluation_tasks.pop(question_id, None)
        try:
            result = task.result()
        except asyncio.CancelledError:
            result = {
                "type": "c_question_evaluation_result",
                "question_id": question_id,
                "status": "error",
                "error": "QUESTION_EVALUATION_CANCELLED",
            }
        except Exception as exc:
            result = {
                "type": "c_question_evaluation_result",
                "question_id": question_id,
                "status": "error",
                "error": str(exc),
            }
        if isinstance(result, Mapping):
            result = dict(result)
        else:
            result = {
                "type": "c_question_evaluation_result",
                "question_id": question_id,
                "status": "error",
                "error": "INVALID_QUESTION_EVALUATION_RESULT",
            }
        result.setdefault("question_id", question_id)
        if result.get("status") == "done":
            self.question_evaluations[question_id] = result
        else:
            self.question_evaluation_errors.append(result)

    def _collect_finished_question_evaluation_tasks(self) -> None:
        for question_id, task in list(self.question_evaluation_tasks.items()):
            if task.done():
                self._store_question_evaluation_result(question_id, task)

    def _apply_corrected_text(
        self,
        answer: Dict[str, Any],
        judgement: Mapping[str, Any],
        has_new_text: bool,
    ) -> None:
        if not has_new_text or judgement.get("status") != "done":
            return
        result = judgement.get("result")
        if not isinstance(result, Mapping):
            return
        corrected = str(result.get("fixed_text") or "").strip()
        if corrected and self.corrected_answer_segments:
            self.corrected_answer_segments[-1] = corrected
            answer["corrected_text"] = self.corrected_answer_text

    def _normalize_judgement_result(
        self,
        judgement: Dict[str, Any],
    ) -> None:
        result = judgement.get("result")
        if not isinstance(result, dict):
            return

        matched_core_points = self._text_list(result.get("matched_core_points"))
        fundamental_error_points = self._text_list(
            result.get("fundamental_error_points")
        )
        has_fundamental_error = self._truthy(result.get("has_fundamental_error"))
        has_core_point = self._truthy(result.get("has_core_point"))
        core_point_focus = self._first_text(
            result.get("core_point_focus"),
            matched_core_points,
            default="",
        )

        if has_fundamental_error:
            judgement_route = "fundamental_error"
            suggested_followup_kind = self.FOLLOWUP_KIND_SERIOUS_ERROR
            generation_focus = self._first_text(
                fundamental_error_points,
                core_point_focus,
                default="明显错误常识",
            )
        elif has_core_point:
            judgement_route = "core_point_hit"
            suggested_followup_kind = self.FOLLOWUP_KIND_KEYWORD_DEEPEN
            generation_focus = self._first_text(
                core_point_focus,
                matched_core_points,
                default="正确核心观点",
            )
        else:
            judgement_route = "core_point_missing"
            suggested_followup_kind = self.FOLLOWUP_KIND_MISSING_CORE
            generation_focus = self._first_text(
                core_point_focus,
                default="标准答案核心要点",
            )

        score_delta = self._score_delta_for_route(judgement_route)
        result.update({
            "has_core_point": has_core_point,
            "matched_core_points": matched_core_points,
            "core_point_focus": core_point_focus,
            "has_fundamental_error": has_fundamental_error,
            "fundamental_error_points": fundamental_error_points,
            "judgement_route": judgement_route,
            "suggested_followup_kind": suggested_followup_kind,
            "generation_focus": generation_focus,
            "score_delta": score_delta,
            "score_reason": judgement_route,
        })

    def _apply_probe_score(
        self,
        question_id: str,
        judgement: Mapping[str, Any],
    ) -> Dict[str, Any]:
        result = judgement.get("result") if isinstance(judgement, Mapping) else {}
        if not isinstance(result, Mapping):
            result = {}
        delta = int(result.get("score_delta") or 0)
        route = str(result.get("judgement_route") or "unknown")
        before = self.probe_score
        self.probe_score += delta
        record = {
            "question_id": question_id,
            "answer_version": judgement.get("answer_version"),
            "route": route,
            "score_reason": result.get("score_reason") or route,
            "delta": delta,
            "score_before": before,
            "score_after": self.probe_score,
        }
        self.probe_score_history.append(record)
        return {
            "type": "probe_score_update",
            **record,
        }

    def _update_answer_signals(
        self,
        question_id: str,
        judgement: Mapping[str, Any],
    ) -> None:
        result = judgement.get("result") if isinstance(judgement, Mapping) else {}
        if not isinstance(result, Mapping):
            return
        route = str(result.get("judgement_route") or "unknown")
        focus = str(
            result.get("generation_focus")
            or result.get("core_point_focus")
            or ""
        )
        matched_core_points = self._text_list(result.get("matched_core_points"))
        fundamental_error_points = self._text_list(
            result.get("fundamental_error_points")
        )
        has_core_point = self._truthy(result.get("has_core_point"))
        has_fundamental_error = self._truthy(result.get("has_fundamental_error"))
        trace = {
            "question_id": question_id,
            "answer_version": judgement.get("answer_version"),
            "route": route,
            "focus": focus,
            "has_core_point": has_core_point,
            "matched_core_points": matched_core_points,
            "core_point_focus": str(result.get("core_point_focus") or ""),
            "has_fundamental_error": has_fundamental_error,
            "fundamental_error_points": fundamental_error_points,
            "answer_status": str(result.get("answer_status") or ""),
        }
        self.answer_trace.append(trace)

        if has_fundamental_error:
            self.answer_signals["has_fundamental_error"] = True
            self.answer_signals["fundamental_error_resolved"] = False
            self.answer_signals["fundamental_error_focus"] = self._first_text(
                fundamental_error_points,
                focus,
                default="明显错误常识",
            )
        elif has_core_point:
            self.answer_signals["has_core_point"] = True
            self.answer_signals["core_point_focus"] = self._first_text(
                result.get("core_point_focus"),
                matched_core_points,
                focus,
                default=self.answer_signals.get("core_point_focus", ""),
            )
            if self.answer_signals.get("has_fundamental_error"):
                self.answer_signals["fundamental_error_resolved"] = True
        else:
            self.answer_signals["has_core_missing"] = True
            self.answer_signals["missing_focus"] = self._first_text(
                focus,
                default=self.answer_signals.get("missing_focus", "标准答案核心要点"),
            )
        self.answer_signals["latest_route"] = route
        self.answer_signals["latest_focus"] = focus

    def _should_start_serious_error_generation(self, judgement: Mapping[str, Any]) -> bool:
        if judgement.get("status") != "done":
            return False
        result = judgement.get("result")
        if not isinstance(result, Mapping):
            return False
        if not self._truthy(result.get("has_fundamental_error")):
            return False
        current_id = str((self.qa_manager.current_question or {}).get("question_id") or "")
        return not self._candidate_generation_exists(
            current_id,
            self.FOLLOWUP_KIND_SERIOUS_ERROR,
        )

    def _probe_completion_message(
        self,
        question_id: str,
    ) -> Optional[Dict[str, Any]]:
        if self.probe_turn_count >= self.max_probe_turns:
            return self._probe_completed_message(question_id, "max_probe_turns_reached")
        return None

    def _probe_completed_message(
        self,
        question_id: str,
        reason: str,
    ) -> Dict[str, Any]:
        return {
            "type": "question_probe_completed",
            "question_id": question_id,
            "probe_score": self.probe_score,
            "probe_turn_count": self.probe_turn_count,
            "max_probe_turns": self.max_probe_turns,
            "reason": reason,
        }

    def _complete_current_root_after_probe(
        self,
        question_id: str,
        reason: str,
    ) -> List[Dict[str, Any]]:
        score = self.probe_score
        score_history = deepcopy(self.probe_score_history)
        self._record_question_completed(question_id, reason)
        root_id = self.qa_manager.complete_current_root(reason)
        self._cancel_generations_for_source(question_id)
        root_review = {
            "root_question_id": root_id,
            "question_id": question_id,
            "probe_score": score,
            "probe_score_history": score_history,
            "reason": reason,
            "final_evaluation": self._root_final_evaluation(
                next(root for root in self.qa_manager.roots if root["question_id"] == root_id),
                score,
                reason,
            ),
        }
        self.completed_root_reviews.append(root_review)
        messages: List[Dict[str, Any]] = [{
            "type": "root_question_completed",
            "root_question_id": root_id,
            "question_id": question_id,
            "probe_score": score,
            "probe_score_history": score_history,
            "reason": reason,
        }]

        next_question = self.qa_manager.next_root()
        if next_question is None:
            final_review_event = self._final_review_message("all_root_questions_completed")
            self._finish_exam_record(
                "finished",
                "all_root_questions_completed",
                final_review=final_review_event.get("review"),
            )
            messages.append(final_review_event)
            messages.append({
                "type": "finished",
                "exam_completed": self.qa_manager.selection_complete,
                "reason": "all_root_questions_completed",
                "exam_record_path": self.exam_record_path,
            })
            return messages

        self._reset_probe_state()
        self._reset_answer(str(next_question.get("question_id") or ""))
        self._record_question_started(next_question)
        messages.append({"type": "question", "question": next_question})
        messages.extend(self._start_prefetched_followups(next_question))
        return messages

    def _final_review_message(self, reason: str) -> Dict[str, Any]:
        review = self._build_final_review(reason)
        return {
            "type": "final_review",
            "mode": "C",
            "review": review,
        }

    def _root_final_evaluation(self, root, score, reason):
        self._collect_finished_question_evaluation_tasks()
        root_id = str(root["question_id"])
        chain = self.qa_manager.question_chains.get(root_id, [])
        chain_questions = [root] + list(chain)
        question_chain = []
        student_answers = []

        for question in chain_questions:
            question_id = str(question.get("question_id") or "")
            answer = deepcopy(self.question_answers.get(question_id) or {})
            student_answer = str(
                answer.get("corrected_text")
                or answer.get("text")
                or ""
            ).strip()
            if student_answer:
                student_answers.append(student_answer)
            evaluation = deepcopy(self.question_evaluations.get(question_id) or {})
            error = next(
                (
                    item
                    for item in reversed(self.question_evaluation_errors)
                    if str(item.get("question_id") or "") == question_id
                ),
                {},
            )
            pending = question_id in self.question_evaluation_tasks
            evaluation_status = (
                "done" if evaluation else "pending" if pending else "error" if error else "not_started"
            )
            question_chain.append({
                "question_id": question_id,
                "root_question_id": root_id,
                "parent_question_id": question.get("parent_question_id"),
                "chain_depth": int(question.get("chain_depth") or 0),
                "relation": str(question.get("relation") or "root"),
                "question": question_value(
                    question,
                    ("question_content", "content", "question", "text"),
                ),
                "standard_answer": question_value(
                    question,
                    (
                        "standard_answer",
                        "reference_answer",
                        "answer_key",
                        "correct_answer",
                    ),
                ),
                "student_answer": student_answer,
                "answer_version": int(answer.get("version") or 0),
                "question_evaluation_status": evaluation_status,
                "question_evaluation": evaluation.get("result") or {},
                "question_evaluation_error": str(error.get("error") or ""),
            })

        return {
            "root_question_id": root_id,
            "question": question_value(
                root,
                ("question_content", "content", "question", "text"),
            ),
            "standard_answer": question_value(
                root,
                (
                    "standard_answer",
                    "reference_answer",
                    "answer_key",
                    "correct_answer",
                ),
            ),
            "student_answer": "\n\n".join(student_answers),
            "question_chain": question_chain,
            "status": str(root.get("status") or "pending"),
            "score": max(0.0, self._float_value(score)),
            "max_score": self.probe_initial_score + self.max_probe_turns,
            "question_count": 0 if root.get("status") == "pending" else len(question_chain),
            "followup_count": len(chain),
            "completion_reason": reason,
        }

    def _build_final_review(self, reason: str) -> Dict[str, Any]:
        self._collect_finished_question_evaluation_tasks()
        roots = [dict(root or {}) for root in self.qa_manager.roots]
        history = deepcopy(self.qa_manager.judgement_history)
        completed_by_root = {
            str(item.get("root_question_id") or ""): dict(item)
            for item in self.completed_root_reviews
            if isinstance(item, Mapping)
        }
        all_stats = self._judgement_stats(history)
        dimension_scores: Dict[str, float] = {}
        dimension_summaries: List[Dict[str, Any]] = []
        question_reviews = []

        for index, root in enumerate(roots, start=1):
            label = f"根题 {index}"
            root_id = str(root.get("question_id") or "")
            root_history = [
                item
                for item in history
                if str(item.get("root_question_id") or "") == root_id
            ]
            root_stats = self._judgement_stats(root_history)
            completion = completed_by_root.get(root_id, {})
            active_score = self.probe_score if root_id == self.qa_manager.current_root_id else 0
            score = max(0.0, self._float_value(completion.get("probe_score"), active_score))
            question_reviews.append(self._root_final_evaluation(root, score, reason))
            dimension_scores[label] = score
            question_text = question_value(
                root,
                ("question_content", "content", "question", "text"),
            )
            status = str(root.get("status") or "pending")
            summary = (
                f"{question_text}。状态：{status}；"
                f"判断 {root_stats['judged']} 次，"
                f"命中核心点 {root_stats['core_hits']} 次，"
                f"核心点缺失 {root_stats['core_missing']} 次，"
                f"严重错误 {root_stats['fundamental_errors']} 次。"
            )
            dimension_summaries.append({
                "dimension": label,
                "summary": summary,
            })

        completed_count = sum(1 for root in roots if root.get("status") == "completed")
        total_score = sum(dimension_scores.values())
        max_total = len(roots) * (self.probe_initial_score + self.max_probe_turns)
        overall_summary = (
            f"C模式口试已生成最终评审：共 {len(roots)} 道根题，"
            f"完成 {completed_count} 道；累计判断 {all_stats['judged']} 次，"
            f"命中核心点 {all_stats['core_hits']} 次，"
            f"严重错误 {all_stats['fundamental_errors']} 次。"
        )

        strengths = []
        if all_stats["core_hits"] > 0:
            strengths.append(f"学生共有 {all_stats['core_hits']} 次明确命中参考答案核心观点。")
        if completed_count == len(roots) and roots:
            strengths.append("已完成全部根题及对应追问链。")
        if not strengths:
            strengths.append("已完成可用回答片段的实时判断和记录。")

        weaknesses = []
        if all_stats["core_missing"] > 0:
            weaknesses.append(f"有 {all_stats['core_missing']} 次回答未明确覆盖核心观点。")
        if all_stats["fundamental_errors"] > 0:
            weaknesses.append(f"检测到 {all_stats['fundamental_errors']} 次严重错误或事实性偏差。")
        if completed_count < len(roots):
            weaknesses.append("仍有根题未自然完成，本次评审基于当前已收集回答。")

        suggestions = [
            "复盘未命中核心点的题目，补充关键概念、条件和推理链。",
            "针对严重错误点进行专项澄清，避免后续回答沿错误理解展开。",
        ]

        return {
            "mode": "C",
            "status": "finished" if self.qa_manager.selection_complete else "partial",
            "reason": reason,
            "overall_summary": overall_summary,
            "dimension_summaries": dimension_summaries,
            "question_reviews": question_reviews,
            "strengths": strengths,
            "weaknesses": weaknesses,
            "suggestions": suggestions,
            "scores": {
                "dimensions": dimension_scores,
                "total": total_score,
                "max_total": max_total,
            },
            "stats": all_stats,
            "exam_record_path": self.exam_record_path,
        }

    def _judgement_stats(self, history: List[Dict[str, Any]]) -> Dict[str, int]:
        stats = {
            "judged": 0,
            "core_hits": 0,
            "core_missing": 0,
            "fundamental_errors": 0,
        }
        for item in history or []:
            if not isinstance(item, Mapping):
                continue
            judgement = item.get("judgement")
            if not isinstance(judgement, Mapping):
                continue
            result = judgement.get("result")
            if not isinstance(result, Mapping):
                continue
            stats["judged"] += 1
            if self._truthy(result.get("has_fundamental_error")):
                stats["fundamental_errors"] += 1
            elif self._truthy(result.get("has_core_point")):
                stats["core_hits"] += 1
            else:
                stats["core_missing"] += 1
        return stats

    @staticmethod
    def _float_value(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    def _score_delta_for_route(self, route: str) -> int:
        if route == "core_point_hit":
            return 1
        if route in {"fundamental_error", "core_point_missing"}:
            return -1
        return 0

    def _generation_focus_for_followup(
        self,
        result: Mapping[str, Any],
        followup_kind: str,
    ) -> str:
        if followup_kind == self.FOLLOWUP_KIND_SERIOUS_ERROR:
            return self._first_text(
                result.get("fundamental_error_points"),
                result.get("generation_focus"),
                result.get("core_point_focus"),
                default="明显错误常识",
            )
        if followup_kind == self.FOLLOWUP_KIND_KEYWORD_DEEPEN:
            return self._first_text(
                result.get("core_point_focus"),
                result.get("matched_core_points"),
                result.get("generation_focus"),
                default="正确核心观点",
            )
        return self._first_text(
            result.get("generation_focus"),
            result.get("core_point_focus"),
            default="标准答案核心要点",
        )

    @staticmethod
    def _text_list(value: Any) -> List[str]:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        text = str(value or "").strip()
        return [text] if text else []

    @staticmethod
    def _first_text(*values: Any, default: str = "") -> str:
        for value in values:
            if isinstance(value, list):
                for item in value:
                    text = str(item or "").strip()
                    if text:
                        return text
                continue
            text = str(value or "").strip()
            if text:
                return text
        return default

    def _build_judgement_outputs(
        self,
        question_id: str,
        answer: Dict[str, Any],
        judgement: Dict[str, Any],
    ) -> List[Dict[str, Any]]:
        if judgement.get("status") == "done":
            return [{
                "type": "judgement_update",
                "question_id": question_id,
                "answer": answer,
                "job_id": judgement.get("job_id"),
                "result": deepcopy(judgement.get("result") or {}),
                "answer_signals": deepcopy(self.answer_signals),
            }]
        return [{
            "type": "judgement_error",
            "question_id": question_id,
            "answer_version": self.answer_version,
            "finished": self.answer_finished,
            "job_id": judgement.get("job_id"),
            "error": str(judgement.get("error") or "JUDGING_FAILED"),
        }]

    def _start_prefetched_followups(self, question: Mapping[str, Any]) -> List[Dict[str, Any]]:
        if self.probe_turn_count >= self.max_probe_turns:
            return []
        if not self.question_runner.configured:
            return []
        messages: List[Dict[str, Any]] = []
        for followup_kind in (
            self.FOLLOWUP_KIND_MISSING_CORE,
            self.FOLLOWUP_KIND_KEYWORD_DEEPEN,
        ):
            generation_message = self._start_followup_generation(
                question=question,
                answer=self.answer_snapshot,
                judgement=self._prefetch_judgement(followup_kind),
                input_text="",
                followup_kind=followup_kind,
                event_type="followup_prefetch_started",
            )
            if generation_message is not None:
                messages.append(generation_message)
        return messages

    def _prefetch_judgement(self, followup_kind: str) -> Dict[str, Any]:
        if followup_kind == self.FOLLOWUP_KIND_KEYWORD_DEEPEN:
            route = "core_point_hit"
            focus = "参考答案核心观点"
            has_core_point = True
        else:
            route = "core_point_missing"
            focus = "标准答案核心要点"
            has_core_point = False
        result = {
            "has_core_point": has_core_point,
            "matched_core_points": [],
            "core_point_focus": focus if has_core_point else "",
            "has_fundamental_error": False,
            "fundamental_error_points": [],
            "answer_status": "partial",
            "judgement_route": route,
            "suggested_followup_kind": followup_kind,
            "generation_focus": focus,
            "followup_kind": followup_kind,
        }
        return {
            "type": "c_judgement_result",
            "job_id": f"c_prefetch_{followup_kind}",
            "answer_version": self.answer_version,
            "finished": False,
            "status": "done",
            "error": "",
            "result": result,
        }

    def _start_followup_generation(
        self,
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
        judgement: Mapping[str, Any],
        input_text: str,
        followup_kind: str,
        event_type: str = "followup_generation_started",
    ) -> Optional[Dict[str, Any]]:
        source_question_id = str(question.get("question_id") or "")
        if self.probe_turn_count >= self.max_probe_turns:
            return None
        if self._candidate_generation_exists(source_question_id, followup_kind):
            return None
        if not self.question_runner.configured:
            return {
                "type": "followup_generation_error",
                "question_id": source_question_id,
                "followup_kind": followup_kind,
                "candidate_label": self.FOLLOWUP_KIND_LABELS.get(followup_kind, ""),
                "answer_version": int(answer.get("version") or 0),
                "error": "QUESTION_GENERATOR_NOT_CONFIGURED",
            }

        normalized_judgement = deepcopy(dict(judgement or {}))
        result = normalized_judgement.get("result")
        if not isinstance(result, dict):
            result = {}
            normalized_judgement["result"] = result
        result["followup_kind"] = followup_kind
        if followup_kind == self.FOLLOWUP_KIND_SERIOUS_ERROR:
            result["has_fundamental_error"] = True
        generation_focus = self._generation_focus_for_followup(result, followup_kind)
        result["generation_focus"] = generation_focus

        self.question_generation_seq += 1
        label = self.FOLLOWUP_KIND_LABELS.get(followup_kind, "")
        generation_id = f"c_followup_{label.lower() or 'x'}_{self.question_generation_seq:08d}"
        task = asyncio.create_task(
            self.question_runner.run(
                generation_id=generation_id,
                question=dict(question),
                answer=dict(answer),
                judgement=normalized_judgement,
                input_text=input_text,
            )
        )
        meta = {
            "generation_id": generation_id,
            "source_question_id": source_question_id,
            "source_answer_version": int(answer.get("version") or 0),
            "relation": "deepen",
            "generation_focus": generation_focus,
            "followup_kind": followup_kind,
            "candidate_label": label,
        }
        self.pending_question_tasks[generation_id] = task
        self.pending_question_meta[generation_id] = meta
        self._record_generation_started(source_question_id, meta)
        return {
            "type": event_type,
            **meta,
            "question_id": source_question_id,
            "answer_version": int(answer.get("version") or 0),
        }

    def _candidate_generation_exists(self, source_question_id: str, followup_kind: str) -> bool:
        for meta in self.pending_question_meta.values():
            if (
                str(meta.get("source_question_id") or "") == str(source_question_id)
                and meta.get("followup_kind") == followup_kind
            ):
                return True
        for result in self.generated_question_buffer:
            if (
                str(result.get("source_question_id") or "") == str(source_question_id)
                and result.get("followup_kind") == followup_kind
            ):
                return True
        for result in self.generation_errors:
            if (
                str(result.get("source_question_id") or "") == str(source_question_id)
                and result.get("followup_kind") == followup_kind
            ):
                return True
        return False

    def _collect_finished_generation_tasks(self) -> None:
        for generation_id, task in list(self.pending_question_tasks.items()):
            if not task.done():
                continue
            self.pending_question_tasks.pop(generation_id, None)
            meta = self.pending_question_meta.pop(generation_id, {})
            try:
                result = task.result()
            except asyncio.CancelledError:
                result = {
                    "type": "c_followup_question_result",
                    "generation_id": generation_id,
                    "status": "error",
                    "error": "GENERATION_CANCELLED",
                }
            except Exception as exc:
                result = {
                    "type": "c_followup_question_result",
                    "generation_id": generation_id,
                    "status": "error",
                    "error": str(exc),
                }
            if isinstance(result, Mapping):
                result = dict(result)
            else:
                result = {"status": "error", "error": "INVALID_GENERATION_RESULT"}
            result.update({key: value for key, value in meta.items() if key not in result})
            self._record_generation_finished(
                str(result.get("source_question_id") or meta.get("source_question_id") or ""),
                result,
            )
            if result.get("status") == "done":
                self.generated_question_buffer.append(result)
            else:
                self.generation_errors.append(result)

    def _generation_status_messages(self, source_question_id: str) -> List[Dict[str, Any]]:
        self._collect_finished_generation_tasks()
        selected_kind = self._select_followup_kind()
        if not selected_kind:
            return []
        ready_messages = self._pop_ready_generated_question(source_question_id, selected_kind)
        if ready_messages:
            return ready_messages
        error_messages = self._pop_generation_errors(source_question_id, selected_kind)
        if error_messages:
            return error_messages
        pending_ids = self._pending_generation_ids(source_question_id, selected_kind)
        if pending_ids:
            return [{
                "type": "followup_generation_pending",
                "question_id": source_question_id,
                "pending_generation_ids": pending_ids,
                "followup_kind": selected_kind,
                "candidate_label": self.FOLLOWUP_KIND_LABELS.get(selected_kind, ""),
                "selection_reason": self._selection_reason(selected_kind),
            }]
        if not self.question_runner.configured:
            return [{
                "type": "followup_generation_error",
                "question_id": source_question_id,
                "followup_kind": selected_kind,
                "candidate_label": self.FOLLOWUP_KIND_LABELS.get(selected_kind, ""),
                "error": "QUESTION_GENERATOR_NOT_CONFIGURED",
            }]
        return [{
            "type": "followup_generation_error",
            "question_id": source_question_id,
            "followup_kind": selected_kind,
            "candidate_label": self.FOLLOWUP_KIND_LABELS.get(selected_kind, ""),
            "error": "SELECTED_FOLLOWUP_NOT_AVAILABLE",
        }]

    def _poll_generated_question(self) -> List[Dict[str, Any]]:
        self._collect_finished_generation_tasks()
        question = self.qa_manager.current_question
        if question is None:
            return [{"type": "waiting", "reason": "no_active_question"}]
        current_id = str(question.get("question_id") or "")
        if not self.answer_finished:
            pending_ids = self._pending_generation_ids(current_id)
            if pending_ids:
                return [{
                    "type": "followup_generation_pending",
                    "question_id": current_id,
                    "pending_generation_ids": pending_ids,
                    "reason": "answer_not_finished",
                }]
            return [{"type": "waiting", "reason": "answer_not_finished"}]
        messages = self._generation_status_messages(current_id)
        if messages:
            if any(
                self._event_type(message) == "followup_generation_error"
                for message in messages
            ):
                messages.append(
                    self._probe_completed_message(
                        current_id,
                        "followup_generation_error",
                    )
                )
                messages.extend(
                    self._complete_current_root_after_probe(
                        current_id,
                        "followup_generation_error",
                    )
                )
            return messages
        return [{"type": "waiting", "reason": "no_generated_question"}]

    def _select_followup_kind(self) -> str:
        if (
            self.answer_signals.get("has_fundamental_error")
            and not self.answer_signals.get("fundamental_error_resolved")
        ):
            return self.FOLLOWUP_KIND_SERIOUS_ERROR
        if not self.answer_signals.get("has_core_point"):
            return self.FOLLOWUP_KIND_MISSING_CORE
        return self.FOLLOWUP_KIND_KEYWORD_DEEPEN

    def _selection_reason(self, followup_kind: str) -> str:
        if followup_kind == self.FOLLOWUP_KIND_SERIOUS_ERROR:
            return "serious_error"
        if followup_kind == self.FOLLOWUP_KIND_MISSING_CORE:
            return "core_point_missing"
        if followup_kind == self.FOLLOWUP_KIND_KEYWORD_DEEPEN:
            return "core_point_hit_without_fundamental_error"
        return "unknown"

    def _pop_ready_generated_question(
        self,
        source_question_id: str,
        followup_kind: str,
    ) -> List[Dict[str, Any]]:
        current_question = self.qa_manager.current_question
        current_id = str((current_question or {}).get("question_id") or "")
        if not current_id or current_id != str(source_question_id or ""):
            return []

        for index, result in enumerate(list(self.generated_question_buffer)):
            if str(result.get("source_question_id") or "") != current_id:
                continue
            if result.get("followup_kind") != followup_kind:
                continue
            self.generated_question_buffer.pop(index)
            generated_question = result.get("question")
            if not isinstance(generated_question, Mapping):
                return [{
                    "type": "followup_generation_error",
                    "generation_id": result.get("generation_id"),
                    "question_id": current_id,
                    "followup_kind": followup_kind,
                    "candidate_label": self.FOLLOWUP_KIND_LABELS.get(followup_kind, ""),
                    "error": "GENERATED_QUESTION_REQUIRED",
                }]
            relation = str(result.get("relation") or generated_question.get("relation") or "deepen")
            relation = relation.strip().lower()
            if relation not in QAmanagerC.VALID_RELATIONS:
                relation = "deepen"
            try:
                question = self.qa_manager.add_generated_question(
                    parent_question_id=current_id,
                    raw_question=generated_question,
                    relation=relation,
                )
            except Exception as exc:
                return [{
                    "type": "followup_generation_error",
                    "generation_id": result.get("generation_id"),
                    "question_id": current_id,
                    "followup_kind": followup_kind,
                    "candidate_label": self.FOLLOWUP_KIND_LABELS.get(followup_kind, ""),
                    "error": str(exc),
                }]
            payload = result.get("payload") if isinstance(result.get("payload"), Mapping) else {}
            self._cancel_generations_for_source(current_id, keep_generation_id=str(result.get("generation_id") or ""))
            self._record_generation_selected(
                current_id,
                str(result.get("generation_id") or ""),
                question,
            )
            self.probe_turn_count += 1
            self.last_followup_kind = str(
                payload.get("followup_kind")
                or result.get("followup_kind")
                or followup_kind
                or "none"
            )
            self.last_followup_focus = str(
                payload.get("generation_focus")
                or result.get("generation_focus")
                or result.get("relation")
                or ""
            )
            self._reset_answer(str(question.get("question_id") or ""))
            self._record_question_started(question, source_generation=result)
            messages = [{
                "type": "generated_question_ready",
                "generation_id": result.get("generation_id"),
                "source_question_id": current_id,
                "source_answer_version": result.get("source_answer_version"),
                "relation": relation,
                "followup_kind": followup_kind,
                "candidate_label": self.FOLLOWUP_KIND_LABELS.get(followup_kind, ""),
                "selection_reason": self._selection_reason(followup_kind),
                "probe_turn_count": self.probe_turn_count,
                "max_probe_turns": self.max_probe_turns,
                "probe_limit_reached": self.probe_turn_count >= self.max_probe_turns,
                "question": question,
                "result": deepcopy(result.get("result") or {}),
            }]
            messages.extend(self._start_prefetched_followups(question))
            return messages
        self._drop_stale_generated_questions(current_id)
        return []

    def _pop_generation_errors(
        self,
        source_question_id: str,
        followup_kind: Optional[str] = None,
    ) -> List[Dict[str, Any]]:
        messages: List[Dict[str, Any]] = []
        remaining: List[Dict[str, Any]] = []
        for result in self.generation_errors:
            result_source_id = str(
                result.get("source_question_id")
                or (result.get("payload") or {}).get("source_question_id")
                or ""
            )
            result_kind = result.get("followup_kind")
            if result_source_id == str(source_question_id or "") and (
                followup_kind is None or result_kind == followup_kind
            ):
                messages.append({
                    "type": "followup_generation_error",
                    "generation_id": result.get("generation_id"),
                    "question_id": result_source_id,
                    "followup_kind": result_kind,
                    "candidate_label": result.get("candidate_label") or self.FOLLOWUP_KIND_LABELS.get(str(result_kind), ""),
                    "error": str(result.get("error") or "QUESTION_GENERATION_FAILED"),
                })
            else:
                remaining.append(result)
        self.generation_errors = remaining
        return messages

    def _drop_stale_generated_questions(self, current_question_id: str) -> None:
        self.generated_question_buffer = [
            result
            for result in self.generated_question_buffer
            if str(result.get("source_question_id") or "") == current_question_id
        ]

    def _cancel_generations_for_source(
        self,
        source_question_id: str,
        keep_generation_id: str = "",
    ) -> None:
        for generation_id, meta in list(self.pending_question_meta.items()):
            if str(meta.get("source_question_id") or "") != str(source_question_id or ""):
                continue
            if keep_generation_id and generation_id == keep_generation_id:
                continue
            task = self.pending_question_tasks.pop(generation_id, None)
            self.pending_question_meta.pop(generation_id, None)
            self._record_generation_finished(
                str(meta.get("source_question_id") or source_question_id or ""),
                {
                    **meta,
                    "status": "cancelled",
                    "error": "GENERATION_CANCELLED",
                },
            )
            if task and not task.done():
                task.cancel()
        self.generated_question_buffer = [
            result
            for result in self.generated_question_buffer
            if str(result.get("source_question_id") or "") != str(source_question_id or "")
            or (keep_generation_id and str(result.get("generation_id") or "") == keep_generation_id)
        ]
        self.generation_errors = [
            result
            for result in self.generation_errors
            if str(result.get("source_question_id") or "") != str(source_question_id or "")
        ]

    def _pending_generation_ids(
        self,
        source_question_id: Optional[str] = None,
        followup_kind: Optional[str] = None,
    ) -> List[str]:
        self._collect_finished_generation_tasks()
        if source_question_id is None and followup_kind is None:
            return list(self.pending_question_tasks.keys())
        return [
            generation_id
            for generation_id, meta in self.pending_question_meta.items()
            if (source_question_id is None or str(meta.get("source_question_id") or "") == str(source_question_id))
            and (followup_kind is None or meta.get("followup_kind") == followup_kind)
        ]

    @property
    def exam_record_path(self) -> str:
        return str(self.exam_record.output_path)

    def _record_question_started(
        self,
        question: Mapping[str, Any],
        source_generation: Optional[Mapping[str, Any]] = None,
    ) -> None:
        try:
            self.exam_record.start_question(
                question,
                probe_turn_count=self.probe_turn_count,
                source_generation=source_generation,
            )
        except Exception:
            pass

    def _record_answer_updated(
        self,
        question_id: str,
        answer: Mapping[str, Any],
    ) -> None:
        try:
            self.exam_record.update_answer(question_id, answer)
        except Exception:
            pass

    def _record_judgement(
        self,
        question_id: str,
        judgement: Mapping[str, Any],
    ) -> None:
        try:
            self.exam_record.append_judgement(question_id, judgement)
        except Exception:
            pass

    def _record_generation_started(
        self,
        source_question_id: str,
        meta: Mapping[str, Any],
    ) -> None:
        try:
            self.exam_record.mark_generation_started(source_question_id, meta)
        except Exception:
            pass

    def _record_generation_finished(
        self,
        source_question_id: str,
        generation: Mapping[str, Any],
    ) -> None:
        try:
            self.exam_record.mark_generation_finished(source_question_id, generation)
        except Exception:
            pass

    def _record_generation_selected(
        self,
        source_question_id: str,
        generation_id: str,
        next_question: Mapping[str, Any],
    ) -> None:
        try:
            self.exam_record.mark_generation_selected(
                source_question_id,
                generation_id,
                next_question,
            )
        except Exception:
            pass

    def _record_question_completed(self, question_id: str, reason: str = "") -> None:
        try:
            self.exam_record.mark_question_completed(question_id, reason)
        except Exception:
            pass

    def _finish_exam_record(
        self,
        status: str,
        reason: str = "",
        final_review: Optional[Mapping[str, Any]] = None,
    ) -> None:
        try:
            current_id = str((self.qa_manager.current_question or {}).get("question_id") or self.answer_question_id or "")
            if current_id:
                self.exam_record.mark_question_completed(current_id, reason)
            self.exam_record.finish_exam(
                status=status,
                reason=reason,
                probe={
                    "score": self.probe_score,
                    "initial_score": self.probe_initial_score,
                    "turn_count": self.probe_turn_count,
                    "max_turns": self.max_probe_turns,
                    "score_history": deepcopy(self.probe_score_history),
                    "last_followup_kind": self.last_followup_kind,
                    "last_followup_focus": self.last_followup_focus,
                },
                final_review=final_review,
            )
        except Exception:
            pass

    def _new_answer_signals(self) -> Dict[str, Any]:
        return {
            "has_fundamental_error": False,
            "fundamental_error_resolved": False,
            "fundamental_error_focus": "",
            "has_core_point": False,
            "core_point_focus": "",
            "has_core_missing": False,
            "missing_focus": "标准答案核心要点",
            "latest_route": "none",
            "latest_focus": "",
        }

    def _reset_probe_state(self) -> None:
        self.probe_score = self.probe_initial_score
        self.probe_turn_count = 0
        self.probe_score_history = []
        self.last_followup_kind = "none"
        self.last_followup_focus = ""

    def _reset_answer(self, question_id: str) -> None:
        self.answer_question_id = str(question_id)
        self.answer_segments = []
        self.corrected_answer_segments = []
        self.answer_version = 0
        self.answer_finished = False
        self.latest_judgement = None
        self.answer_trace = []
        self.answer_signals = self._new_answer_signals()

    @property
    def answer_text(self) -> str:
        return "\n".join(self.answer_segments)

    @property
    def corrected_answer_text(self) -> str:
        return "\n".join(self.corrected_answer_segments)

    @property
    def answer_snapshot(self) -> Dict[str, Any]:
        return {
            "question_id": self.answer_question_id,
            "text": self.answer_text,
            "corrected_text": self.corrected_answer_text,
            "segments": list(self.answer_segments),
            "version": self.answer_version,
            "finished": self.answer_finished,
        }

    @property
    def state(self) -> Dict[str, Any]:
        self._collect_finished_generation_tasks()
        self._collect_finished_question_evaluation_tasks()
        return {
            "answer": self.answer_snapshot,
            "latest_judgement": deepcopy(self.latest_judgement),
            "answer_trace": deepcopy(self.answer_trace),
            "answer_signals": deepcopy(self.answer_signals),
            "question_answers": deepcopy(self.question_answers),
            "question_evaluations": deepcopy(self.question_evaluations),
            "question_evaluation_errors": deepcopy(self.question_evaluation_errors),
            "pending_question_evaluations": list(self.question_evaluation_tasks.keys()),
            "question_state": self.qa_manager.snapshot(),
            "pending_question_generations": deepcopy(
                list(self.pending_question_meta.values())
            ),
            "generated_question_buffer": deepcopy(self.generated_question_buffer),
            "generation_errors": deepcopy(self.generation_errors),
            "exam_record": self.exam_record.snapshot(),
            "probe": {
                "score": self.probe_score,
                "initial_score": self.probe_initial_score,
                "turn_count": self.probe_turn_count,
                "max_turns": self.max_probe_turns,
                "score_history": deepcopy(self.probe_score_history),
                "last_followup_kind": self.last_followup_kind,
                "last_followup_focus": self.last_followup_focus,
            },
        }

    @staticmethod
    def _event_type(event: Dict[str, Any]) -> str:
        return str(event.get("type") or "").strip().lower()

    @staticmethod
    def _truthy(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return value != 0
        return str(value or "").strip().lower() in {"1", "true", "yes", "y"}
