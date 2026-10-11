"""B-mode answer evaluation and on-demand follow-up questions."""

from __future__ import annotations

import asyncio
import json
from copy import deepcopy
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional

from AIOralExamSystem.Exam.QAmanager import QAmanager
from AIOralExamSystem.Graph.ExamA import question_value
from AIOralExamSystem.Graph.ExamC import (
    CAnswerJudgementRunner,
    CExamRecordStore,
    CFollowupQuestionRunner,
    CQuestionEvaluationRunner,
)


class BExamRecordStore(CExamRecordStore):
    """Keep B-mode records in the existing exam-record format."""

    def snapshot(self) -> Dict[str, Any]:
        snapshot = super().snapshot()
        snapshot["mode"] = "B"
        return snapshot


class BFollowupQuestionRunner(CFollowupQuestionRunner):
    """Choose an actual answer point and ask one corrective follow-up."""

    @staticmethod
    def build_payload(
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
        judgement: Mapping[str, Any],
        input_text: str,
    ) -> Dict[str, Any]:
        payload = CFollowupQuestionRunner.build_payload(
            question, answer, judgement, input_text
        )
        payload["student_answer"] = str(
            answer.get("text") or input_text or ""
        ).strip()
        result = judgement.get("result") if isinstance(judgement, Mapping) else {}
        result = result if isinstance(result, Mapping) else {}
        payload["evaluation_correctness"] = str(result.get("evaluation_correctness") or "")
        payload["evaluation_reason"] = str(result.get("evaluation_reason") or "")
        return payload

    @staticmethod
    def build_prompts(payload: Mapping[str, Any]) -> tuple[str, str]:
        system_prompt = """
你是B模式口试追问 Agent。学生已完成当前题的回答，单题评价认为回答尚未正确。
从 student_answer 中选一个确实出现的、与当前问题有关的具体观点或说法，结合 standard_answer 找到它的错误或欠缺，再生成一道针对这个点的追问。

要求：
- selected_answer_point 必须逐字摘自 student_answer，不得补写或推测学生没有说过的话。
- 追问应让学生解释或修正该观点，并与正确答案的关键要求对应；不要直接泄露答案。
- 只生成一道口试题。若回答没有可用的具体观点，返回空的 question_content 和 selected_answer_point。
- standard_answer 写这道追问的参考要点，不要原样复制整段原题答案。
- 只返回 JSON，不要 Markdown 或额外说明。

JSON 字段：
{"selected_answer_point":"回答中的原文片段","question_content":"追问题目","standard_answer":"追问参考要点","relation":"deepen","reason":"追问理由"}
        """.strip()
        user_prompt = json.dumps(
            {
                "parent_question": payload.get("parent_question"),
                "standard_answer": payload.get("standard_answer"),
                "student_answer": payload.get("student_answer"),
                "evaluation_correctness": payload.get("evaluation_correctness"),
                "evaluation_reason": payload.get("evaluation_reason"),
            },
            ensure_ascii=False,
            indent=2,
        )
        return system_prompt, user_prompt

    async def run(
        self,
        generation_id: str,
        question: Mapping[str, Any],
        answer: Mapping[str, Any],
        judgement: Mapping[str, Any],
        input_text: str,
    ) -> Dict[str, Any]:
        result = await super().run(
            generation_id, question, answer, judgement, input_text
        )
        if result.get("status") != "done":
            return result
        generated = result.get("question") or {}
        selected_point = str(
            generated.get("selected_answer_point")
            or (result.get("result") or {}).get("selected_answer_point")
            or ""
        ).strip()
        student_answer = str((result.get("payload") or {}).get("student_answer") or "")
        if not selected_point or " ".join(selected_point.split()) not in " ".join(student_answer.split()):
            result.update(
                status="error",
                error="B_FOLLOWUP_ANSWER_POINT_INVALID",
                question=None,
            )
            return result
        generated["selected_answer_point"] = selected_point
        return result


class ExamBFlow:
    """Own B-mode question state; evaluate only a completed answer."""

    FOLLOWUP_KIND_ANSWER_CORRECTION = "answer_correction"

    def __init__(
        self, qa_manager: QAmanager, answer_judger: Optional[Any] = None,
        model_settings: Optional[Mapping[str, Any]] = None,
        followup_generator: Optional[Any] = None, exam_id: str = "",
        user_id: str = "", exam_item_id: str = "",
        question_evaluator: Optional[Any] = None, max_probe_turns: int = 2,
        mode: str = "B",
    ) -> None:
        if mode != "B":
            raise ValueError("EXAM_MODE_INVALID")
        if isinstance(max_probe_turns, bool) or not isinstance(max_probe_turns, int) or not 1 <= max_probe_turns <= 20:
            raise ValueError("B_MAX_PROBE_TURNS_INVALID")
        self.mode = "B"
        self.qa_manager = qa_manager
        self.exam_id = str(exam_id or "")
        self.user_id = str(user_id or "")
        self.exam_item_id = str(exam_item_id or "")
        self.judgement_runner = CAnswerJudgementRunner(answer_judger, model_settings)
        self.question_evaluation_runner = CQuestionEvaluationRunner(question_evaluator, model_settings)
        self.question_runner = BFollowupQuestionRunner(followup_generator, model_settings)
        self.exam_record = BExamRecordStore(
            exam_id=self.exam_id, user_id=self.user_id, exam_item_id=self.exam_item_id,
            output_dir=Path(__file__).resolve().parents[2] / "exam_records" / "b_mode",
        )
        self.max_probe_turns = max_probe_turns
        self.probe_initial_score = 2 * max_probe_turns
        self.probe_max_score = 4 * max_probe_turns
        self.probe_score = self.probe_initial_score
        self.probe_turn_count = 0
        self.probe_score_history: List[Dict[str, Any]] = []
        self.answer_question_id = ""
        self.answer_segments: List[str] = []
        self.corrected_answer_segments: List[str] = []
        self.answer_version = 0
        self.answer_finished = False
        self.latest_judgement: Optional[Dict[str, Any]] = None
        self.question_answers: Dict[str, Dict[str, Any]] = {}
        self.question_evaluations: Dict[str, Dict[str, Any]] = {}
        self.question_evaluation_errors: List[Dict[str, Any]] = []
        self.completed_root_reviews: List[Dict[str, Any]] = []
        self.question_generation_seq = 0
        self.pending_question_tasks: Dict[str, asyncio.Task] = {}
        self.pending_question_meta: Dict[str, Dict[str, Any]] = {}

    async def run(self, event: Dict[str, Any]) -> List[Dict[str, Any]]:
        event = dict(event or {})
        kind = str(event.get("type") or "").strip().lower() 
        if kind == "start":
            return self._start()
        if kind == "answer_chunk":
            return self._receive_chunk(event)
        if kind in {"answer_end", "judge_request"}:
            return await self._finish_answer(event)
        if kind == "poll_generated_question":
            return self._poll_generated_question()
        if kind in {"finish", "stop"}:
            review_event = self._final_review_message(kind) if kind == "finish" else None
            self._finish_exam_record("closed", kind, review_event["review"] if review_event else None)
            messages = [review_event] if review_event else []
            messages.append({
                "type": "closed", "exam_completed": self.qa_manager.selection_complete,
                "pending_generation_ids": list(self.pending_question_tasks),
                "exam_record_path": self.exam_record_path,
            })
            self._cancel_pending_generation()
            return messages
        return [{"type": "waiting", "reason": "unknown_event"}]

    def _start(self) -> List[Dict[str, Any]]:
        if self.qa_manager.current_question is not None:
            return [{"type": "waiting", "reason": "already_started"}]
        question = self.qa_manager.start()
        if question is None:
            return [{"type": "error", "error": "QUESTION_QUEUE_REQUIRED"}]
        self._reset_probe()
        self._reset_answer(str(question.get("question_id") or ""))
        self.exam_record.start_question(question)
        return [{"type": "question", "question": question}]

    def _active_question(self, event: Mapping[str, Any]) -> tuple[Optional[Dict[str, Any]], Optional[List[Dict[str, Any]]]]:
        question = self.qa_manager.current_question
        if question is None:
            return None, [{"type": "waiting", "reason": "no_active_question"}]
        current_id = str(question.get("question_id") or "")
        supplied_id = str(event.get("question_id") or "")
        if supplied_id and supplied_id != current_id:
            return None, [{"type": "error", "error": "QUESTION_ID_NOT_CURRENT"}]
        if self.answer_finished:
            return None, [{"type": "error", "error": "ANSWER_ALREADY_FINISHED"}]
        return question, None

    def _receive_chunk(self, event: Mapping[str, Any]) -> List[Dict[str, Any]]:
        question, error = self._active_question(event)
        if error is not None:
            return error
        text = str(event.get("text", event.get("answer")) or "").strip()
        if not text:
            return [{"type": "error", "error": "ANSWER_CHUNK_REQUIRED"}]
        # Keep fragments for the final answer. No judgement, scoring, record write,
        # question generation, or public event happens here.
        self.answer_segments.append(text)
        return []

    async def _finish_answer(self, event: Mapping[str, Any]) -> List[Dict[str, Any]]:
        question, error = self._active_question(event)
        if error is not None:
            return error
        question_id = str(question.get("question_id") or "")
        text = str(event.get("text", event.get("answer")) or "").strip()
        if text:
            self.answer_segments.append(text)
        self.answer_version += 1
        self.answer_finished = True
        self.corrected_answer_segments = list(self.answer_segments)
        answer = self.answer_snapshot
        self.question_answers[question_id] = deepcopy(answer)
        messages = [{"type": "answer_received", "question_id": question_id, "answer": answer}]

        judgement: Dict[str, Any] = {"result": {}}
        if self.answer_text:
            try:
                judgement = await asyncio.wait_for(
                    self.judgement_runner.run(question, answer, self.answer_text), timeout=7.0,
                )
            except asyncio.TimeoutError:
                judgement = {"status": "error", "error": "ANSWER_JUDGEMENT_TIMEOUT", "result": {}}
            if judgement.get("status") == "done":
                fixed = str((judgement.get("result") or {}).get("fixed_text") or "").strip()
                if fixed:
                    self.corrected_answer_segments = [fixed]
                    answer = self.answer_snapshot
                    self.question_answers[question_id] = deepcopy(answer)
                self.qa_manager.record_judgement(question, answer, judgement)
            self.latest_judgement = deepcopy(judgement)
            self.exam_record.append_judgement(question_id, judgement)
            messages.append({
                "type": "judgement_update" if judgement.get("status") == "done" else "judgement_error",
                "question_id": question_id,
                "answer": answer,
                "result": deepcopy(judgement.get("result") or {}),
                "error": str(judgement.get("error") or ""),
            })

        evaluation = await self._evaluate(question, self.answer_snapshot)
        if evaluation.get("status") != "done":
            self.answer_finished = False
            self.question_evaluation_errors.append({"question_id": question_id, **evaluation})
            return messages + [{
                "type": "question_evaluation_error", "question_id": question_id,
                "error": evaluation.get("error") or "QUESTION_EVALUATION_FAILED",
                "retryable": True,
            }]
        self.question_evaluations[question_id] = deepcopy(evaluation)
        messages.append(self._apply_probe_score(question_id, evaluation))
        correctness = str((evaluation.get("result") or {}).get("correctness") or "").lower()
        if correctness == "correct":
            completion_reason = "answered_correctly"
        elif not self.answer_text:
            completion_reason = "no_answer_key_point"
        elif self.probe_score <= 0:
            completion_reason = "score_zero"
        elif self.probe_turn_count >= self.max_probe_turns:
            completion_reason = "max_probe_turns_reached"
        else:
            completion_reason = ""
        if completion_reason:
            messages.extend(self._complete_root(question_id, completion_reason))
            return messages

        generation_judgement = deepcopy(judgement)
        generation_judgement["result"] = {
            **dict(judgement.get("result") or {}),
            "evaluation_correctness": correctness,
            "evaluation_reason": str((evaluation.get("result") or {}).get("reason") or ""),
        }
        generation = self._start_generation(question, self.answer_snapshot, generation_judgement)
        messages.append(generation)
        if generation["type"] == "followup_generation_error":
            messages.extend(self._complete_root(question_id, "followup_generation_error"))
        else:
            messages.append(self._pending_generation_event(question_id))
        return messages

    async def _evaluate(self, question: Mapping[str, Any], answer: Mapping[str, Any]) -> Dict[str, Any]:
        question_id = str(question.get("question_id") or "")
        if not self.answer_text:
            return {"status": "done", "question_id": question_id,
                    "answer_version": self.answer_version,
                    "result": {"correctness": "missing", "reason": "未提供回答"}}
        if not self.question_evaluation_runner.configured:
            return {"status": "error", "error": "QUESTION_EVALUATOR_NOT_CONFIGURED"}
        try:
            result = await asyncio.wait_for(self.question_evaluation_runner.run(question, answer), timeout=30)
        except asyncio.TimeoutError:
            return {"status": "error", "error": "QUESTION_EVALUATION_TIMEOUT"}
        correctness = str((result.get("result") or {}).get("correctness") or "").lower()
        if result.get("status") == "done" and correctness not in {
            "correct", "partially_correct", "incorrect", "missing", "unclear",
        }:
            return {"status": "error", "error": "QUESTION_EVALUATION_INVALID"}
        return result

    def _apply_probe_score(self, question_id: str, evaluation: Mapping[str, Any]) -> Dict[str, Any]:
        result = evaluation.get("result") or {}
        route = str(result.get("correctness") or "").lower()
        if route == "unclear":
            route = "missing"
        rule_delta = {"correct": 2, "partially_correct": 1, "incorrect": -2, "missing": -2}[route]
        before = self.probe_score
        if route == "correct" and self.probe_turn_count == 0:
            self.probe_score = self.probe_max_score
        else:
            self.probe_score = max(0, min(self.probe_max_score, before + rule_delta))
        record = {
            "question_id": question_id, "answer_version": evaluation.get("answer_version"),
            "route": route, "score_reason": result.get("reason") or route,
            "delta": self.probe_score - before, "rule_delta": rule_delta,
            "score_before": before, "score_after": self.probe_score,
        }
        self.probe_score_history.append(record)
        return {"type": "probe_score_update", **record}

    def _start_generation(self, question, answer, judgement) -> Dict[str, Any]:
        question_id = str(question.get("question_id") or "")
        if not self.question_runner.configured:
            return {"type": "followup_generation_error", "question_id": question_id,
                    "followup_kind": self.FOLLOWUP_KIND_ANSWER_CORRECTION,
                    "error": "QUESTION_GENERATOR_NOT_CONFIGURED"}
        self.question_generation_seq += 1
        generation_id = f"b_followup_{self.question_generation_seq:08d}"
        meta = {
            "generation_id": generation_id, "source_question_id": question_id,
            "source_answer_version": self.answer_version,
            "followup_kind": self.FOLLOWUP_KIND_ANSWER_CORRECTION,
            "candidate_label": "B", "relation": "deepen",
        }
        self.pending_question_tasks[generation_id] = asyncio.create_task(
            self.question_runner.run(generation_id, question, answer, judgement,
                                     self.corrected_answer_text or self.answer_text)
        )
        self.pending_question_meta[generation_id] = meta
        self.exam_record.mark_generation_started(question_id, meta)
        return {"type": "followup_generation_started", **meta,
                "question_id": question_id, "answer_version": self.answer_version}

    def _pending_generation_event(self, question_id: str) -> Dict[str, Any]:
        return {
            "type": "followup_generation_pending", "question_id": question_id,
            "pending_generation_ids": list(self.pending_question_tasks),
            "followup_kind": self.FOLLOWUP_KIND_ANSWER_CORRECTION,
            "candidate_label": "B", "selection_reason": "answer_not_correct",
        }

    def _poll_generated_question(self) -> List[Dict[str, Any]]:
        question = self.qa_manager.current_question
        if question is None:
            return [{"type": "waiting", "reason": "no_active_question"}]
        question_id = str(question.get("question_id") or "")
        if not self.answer_finished:
            return [{"type": "waiting", "reason": "answer_not_finished"}]
        for generation_id, task in list(self.pending_question_tasks.items()):
            if not task.done():
                continue
            self.pending_question_tasks.pop(generation_id)
            meta = self.pending_question_meta.pop(generation_id)
            try:
                result = task.result()
            except asyncio.CancelledError:
                result = {"status": "error", "error": "GENERATION_CANCELLED"}
            except Exception as exc:
                result = {"status": "error", "error": str(exc)}
            result = {**meta, **dict(result)} if isinstance(result, Mapping) else {
                **meta, "status": "error", "error": "INVALID_GENERATION_RESULT",
            }
            self.exam_record.mark_generation_finished(question_id, result)
            if result.get("status") != "done":
                error = {"type": "followup_generation_error", "question_id": question_id,
                         "generation_id": generation_id,
                         "error": result.get("error") or "QUESTION_GENERATION_FAILED"}
                return [error, *self._complete_root(question_id, "followup_generation_error")]
            generated = result.get("question")
            if not isinstance(generated, Mapping):
                error = {"type": "followup_generation_error", "question_id": question_id,
                         "generation_id": generation_id, "error": "GENERATED_QUESTION_REQUIRED"}
                return [error, *self._complete_root(question_id, "followup_generation_error")]
            relation = str(result.get("relation") or generated.get("relation") or "deepen").lower()
            if relation not in QAmanager.VALID_RELATIONS:
                relation = "deepen"
            try:
                next_question = self.qa_manager.add_generated_question(question_id, generated, relation)
            except Exception as exc:
                error = {"type": "followup_generation_error", "question_id": question_id,
                         "generation_id": generation_id, "error": str(exc)}
                return [error, *self._complete_root(question_id, "followup_generation_error")]
            self.exam_record.mark_generation_selected(question_id, generation_id, next_question)
            self.probe_turn_count += 1
            self._reset_answer(str(next_question.get("question_id") or ""))
            self.exam_record.start_question(next_question, probe_turn_count=self.probe_turn_count,
                                            source_generation=result)
            return [{
                "type": "generated_question_ready", "generation_id": generation_id,
                "source_question_id": question_id,
                "source_answer_version": result.get("source_answer_version"),
                "relation": relation,
                "followup_kind": self.FOLLOWUP_KIND_ANSWER_CORRECTION,
                "candidate_label": "B", "selection_reason": "answer_not_correct",
                "probe_turn_count": self.probe_turn_count,
                "max_probe_turns": self.max_probe_turns,
                "probe_limit_reached": self.probe_turn_count >= self.max_probe_turns,
                "question": next_question, "result": deepcopy(result.get("result") or {}),
            }]
        return [self._pending_generation_event(question_id)] if self.pending_question_tasks else [
            {"type": "waiting", "reason": "no_generated_question"}
        ]

    def _complete_root(self, question_id: str, reason: str) -> List[Dict[str, Any]]:
        score = self.probe_score
        score_history = deepcopy(self.probe_score_history)
        self.exam_record.mark_question_completed(question_id, reason)
        root_id = self.qa_manager.complete_current_root(reason)
        self._cancel_pending_generation()
        self.completed_root_reviews.append({
            "root_question_id": root_id, "question_id": question_id,
            "probe_score": score, "probe_score_history": score_history,
            "reason": reason,
        })
        messages = [{
            "type": "question_probe_completed", "question_id": question_id,
            "probe_score": score, "probe_turn_count": self.probe_turn_count,
            "max_probe_turns": self.max_probe_turns, "reason": reason,
        }, {
            "type": "root_question_completed", "root_question_id": root_id,
            "question_id": question_id, "probe_score": score,
            "probe_score_history": score_history, "reason": reason,
        }]
        next_question = self.qa_manager.next_root()
        if next_question is None:
            final_event = self._final_review_message("all_root_questions_completed")
            self._finish_exam_record("finished", "all_root_questions_completed", final_event["review"])
            messages.extend([final_event, {
                "type": "finished", "exam_completed": self.qa_manager.selection_complete,
                "reason": "all_root_questions_completed", "exam_record_path": self.exam_record_path,
            }])
            return messages
        self._reset_probe()
        self._reset_answer(str(next_question.get("question_id") or ""))
        self.exam_record.start_question(next_question)
        messages.append({"type": "question", "question": next_question})
        return messages

    def _cancel_pending_generation(self) -> None:
        for task in self.pending_question_tasks.values():
            if not task.done():
                task.cancel()
        self.pending_question_tasks.clear()
        self.pending_question_meta.clear()

    def _reset_probe(self) -> None:
        self.probe_score = self.probe_initial_score
        self.probe_turn_count = 0
        self.probe_score_history = []

    def _reset_answer(self, question_id: str) -> None:
        self.answer_question_id = question_id
        self.answer_segments = []
        self.corrected_answer_segments = []
        self.answer_version = 0
        self.answer_finished = False
        self.latest_judgement = None

    @property
    def answer_text(self) -> str:
        return "\n".join(self.answer_segments)

    @property
    def corrected_answer_text(self) -> str:
        return "\n".join(self.corrected_answer_segments)

    @property
    def answer_snapshot(self) -> Dict[str, Any]:
        return {
            "question_id": self.answer_question_id, "text": self.answer_text,
            "corrected_text": self.corrected_answer_text,
            "segments": list(self.answer_segments), "version": self.answer_version,
            "finished": self.answer_finished,
        }

    @property
    def exam_record_path(self) -> str:
        return str(self.exam_record.output_path)

    def _root_max_score(self, root: Mapping[str, Any]) -> float:
        score = self._float_value(root.get("score"))
        if score <= 0:
            raise ValueError("B_PRESET_QUESTION_SCORE_INVALID")
        return score

    def _root_earned_score(self, root: Mapping[str, Any], probe_score: Any) -> float:
        score = max(0.0, min(self.probe_max_score, self._float_value(probe_score)))
        return self._root_max_score(root) * score / self.probe_max_score

    def _root_final_evaluation(self, root, score, reason, score_history=None) -> Dict[str, Any]:
        root_id = str(root["question_id"])
        chain = [root, *self.qa_manager.question_chains.get(root_id, [])]
        question_chain = []
        student_answers = []
        for question in chain:
            question_id = str(question.get("question_id") or "")
            answer = self.question_answers.get(question_id) or {}
            student_answer = str(answer.get("corrected_text") or answer.get("text") or "").strip()
            if student_answer:
                student_answers.append(student_answer)
            evaluation = self.question_evaluations.get(question_id) or {}
            error = next((item for item in reversed(self.question_evaluation_errors)
                          if item.get("question_id") == question_id), {})
            question_chain.append({
                "question_id": question_id, "root_question_id": root_id,
                "parent_question_id": question.get("parent_question_id"),
                "chain_depth": int(question.get("chain_depth") or 0),
                "relation": str(question.get("relation") or "root"),
                "question": question_value(question, ("question_content", "content", "question", "text")),
                "standard_answer": question_value(question, ("standard_answer", "reference_answer", "answer_key", "correct_answer")),
                "student_answer": student_answer, "answer_version": int(answer.get("version") or 0),
                "question_evaluation_status": "done" if evaluation else "error" if error else "not_started",
                "question_evaluation": deepcopy(evaluation.get("result") or {}),
                "question_evaluation_error": str(error.get("error") or ""),
            })
        return {
            "root_question_id": root_id,
            "question": question_value(root, ("question_content", "content", "question", "text")),
            "standard_answer": question_value(root, ("standard_answer", "reference_answer", "answer_key", "correct_answer")),
            "student_answer": "\n\n".join(student_answers), "question_chain": question_chain,
            "status": str(root.get("status") or "pending"),
            "score": self._root_earned_score(root, score), "max_score": self._root_max_score(root),
            "dimension": str(root.get("question_dimension") or "").strip(),
            "score_history": deepcopy(score_history or []),
            "question_count": 0 if root.get("status") == "pending" else len(question_chain),
            "followup_count": len(chain) - 1, "completion_reason": reason,
        }

    def _build_final_review(self, reason: str) -> Dict[str, Any]:
        dimensions: Dict[str, float] = {}
        maximums: Dict[str, float] = {}
        summaries = []
        question_reviews = []
        completed = {str(item["root_question_id"]): item for item in self.completed_root_reviews}
        for root in self.qa_manager.roots:
            root_id = str(root.get("question_id") or "")
            dimension = str(root.get("question_dimension") or "").strip()
            if not dimension:
                raise ValueError("B_PRESET_QUESTION_DIMENSION_REQUIRED")
            completion = completed.get(root_id, {})
            active_score = self.probe_score if root_id == self.qa_manager.current_root_id and self.probe_score_history else 0
            score = completion.get("probe_score", active_score)
            history = completion.get("probe_score_history") or (
                self.probe_score_history if root_id == self.qa_manager.current_root_id else []
            )
            review = self._root_final_evaluation(root, score, reason, history)
            question_reviews.append(review)
            dimensions[dimension] = dimensions.get(dimension, 0.0) + review["score"]
            maximums[dimension] = maximums.get(dimension, 0.0) + review["max_score"]
            summaries.append({"dimension": dimension,
                              "summary": f"{review['question']}。状态：{review['status']}。"})
        return {
            "mode": "B", "status": "finished" if self.qa_manager.selection_complete else "partial",
            "reason": reason, "overall_summary": f"B模式口试共 {len(self.qa_manager.roots)} 道根题。",
            "dimension_summaries": summaries, "question_reviews": question_reviews,
            "strengths": [], "weaknesses": [], "suggestions": [],
            "scores": {"dimensions": dimensions, "max_dimensions": maximums,
                       "total": sum(dimensions.values()), "max_total": sum(maximums.values())},
            "scoring": {"version": 2, "max_probe_turns": self.max_probe_turns,
                        "initial_score": self.probe_initial_score,
                        "max_root_score": self.probe_max_score,
                        "policy": "preset_question_scaled_probe"},
            "stats": {"judged": len(self.qa_manager.judgement_history)},
            "exam_record_path": self.exam_record_path,
        }

    def _final_review_message(self, reason: str) -> Dict[str, Any]:
        return {"type": "final_review", "mode": "B", "review": self._build_final_review(reason)}

    def _finish_exam_record(self, status: str, reason: str, review: Optional[Mapping[str, Any]]) -> None:
        self.exam_record.finish_exam(
            status=status, reason=reason,
            probe={"score": self.probe_score, "initial_score": self.probe_initial_score,
                   "turn_count": self.probe_turn_count, "max_turns": self.max_probe_turns,
                   "score_history": deepcopy(self.probe_score_history)},
            final_review=review,
        )

    @property
    def state(self) -> Dict[str, Any]:
        return {
            "answer": self.answer_snapshot, "latest_judgement": deepcopy(self.latest_judgement),
            "question_answers": deepcopy(self.question_answers),
            "question_evaluations": deepcopy(self.question_evaluations),
            "question_evaluation_errors": deepcopy(self.question_evaluation_errors),
            "question_state": self.qa_manager.snapshot(),
            "pending_question_generations": deepcopy(list(self.pending_question_meta.values())),
            "exam_record": self.exam_record.snapshot(),
            "probe": {"score": self.probe_score, "initial_score": self.probe_initial_score,
                      "turn_count": self.probe_turn_count, "max_turns": self.max_probe_turns,
                      "score_history": deepcopy(self.probe_score_history)},
        }

    @staticmethod
    def _float_value(value: Any, default: float = 0.0) -> float:
        try:
            return float(value)
        except (TypeError, ValueError):
            return default
