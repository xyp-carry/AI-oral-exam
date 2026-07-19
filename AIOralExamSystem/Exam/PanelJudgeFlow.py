import asyncio
import json
import re
from collections import Counter
from typing import Dict, List, Optional

from AIOralExamSystem.Exam.Examdata.judge_config_repository import build_agent_model_settings
from AIOralExamSystem.Exam.Judger import StageJudgeAdjudicatorAgent, StageJudgerAgent


QUALITY_ORDER = {
    "excellent": 5,
    "correct": 4,
    "average": 3,
    "wrong": 2,
    "absurd": 1,
}

CORRECT_LEVELS = {"excellent", "correct", "average"}


class PanelJudgeFlow:
    def __init__(
        self,
        scorer_agents: List[StageJudgerAgent],
        adjudicator_agent: Optional[StageJudgeAdjudicatorAgent] = None,
        fail_policy: str = "majority",
    ):
        if not scorer_agents:
            raise ValueError("PANEL_JUDGE_FLOW_REQUIRES_SCORER")
        self.scorer_agents = scorer_agents
        self.adjudicator_agent = adjudicator_agent
        self.fail_policy = (fail_policy or "majority").strip().lower()

    @classmethod
    def from_config(
        cls,
        judge_config: Dict[str, object],
        default_model_settings: dict,
        source: str,
    ) -> "PanelJudgeFlow":
        scorers = []
        for scorer_config in judge_config.get("scorers") or []:
            model_settings = build_agent_model_settings(
                scorer_config,
                default_model_settings,
            )
            scorers.append(
                StageJudgerAgent(
                    model_settings,
                    source,
                    thinking=True,
                    response_format=True,
                    temperature=float(model_settings.get("temperature", 0)),
                )
            )

        adjudicator_agent = None
        adjudicator_config = judge_config.get("adjudicator")
        if len(scorers) > 1 and adjudicator_config:
            model_settings = build_agent_model_settings(
                adjudicator_config,
                default_model_settings,
            )
            adjudicator_agent = StageJudgeAdjudicatorAgent(
                model_settings,
                source,
                thinking=True,
                response_format=True,
                temperature=float(model_settings.get("temperature", 0)),
            )

        return cls(
            scorer_agents=scorers,
            adjudicator_agent=adjudicator_agent,
            fail_policy=str(judge_config.get("fail_policy") or "majority"),
        )

    async def run(self, payload: Dict[str, object]) -> Dict[str, object]:
        scorer_results = await self._run_scorers(payload)
        valid_results = [
            result
            for result in scorer_results
            if not result.get("agent_error")
        ]
        if len(self.scorer_agents) == 1 and valid_results:
            return normalize_judge_result(valid_results[0])
        if not valid_results:
            return {
                "answer_correct": False,
                "correctness_level": "wrong",
                "reason": "全部评分 Agent 调用失败，系统按错误回答处理。",
            }

        if self.adjudicator_agent is not None:
            adjudicated = await self._run_adjudicator(payload, valid_results)
            if adjudicated:
                return normalize_judge_result(adjudicated)

        return normalize_judge_result(self._majority_fallback(valid_results))

    async def _run_scorers(self, payload: Dict[str, object]) -> List[Dict[str, object]]:
        tasks = [
            self._run_single_scorer(agent, payload)
            for agent in self.scorer_agents
        ]
        return await asyncio.gather(*tasks)

    async def _run_single_scorer(
        self,
        agent: StageJudgerAgent,
        payload: Dict[str, object],
    ) -> Dict[str, object]:
        try:
            response = await agent.run(history=[{
                "role": "user",
                "content": json.dumps(payload, ensure_ascii=False),
            }])
            result = parse_agent_json_response(response)
            return normalize_judge_result(result)
        except Exception as exc:
            return {
                "agent_error": {
                    "error_class": exc.__class__.__name__,
                    "error_message": str(exc),
                },
            }

    async def _run_adjudicator(
        self,
        payload: Dict[str, object],
        scorer_results: List[Dict[str, object]],
    ) -> Optional[Dict[str, object]]:
        adjudication_payload = {
            "source_payload": payload,
            "judge_results": scorer_results,
        }
        try:
            response = await self.adjudicator_agent.run(history=[{
                "role": "user",
                "content": json.dumps(adjudication_payload, ensure_ascii=False),
            }])
            return parse_agent_json_response(response)
        except Exception as exc:
            return {
                "answer_correct": False,
                "correctness_level": self._majority_quality(scorer_results),
                "reason": f"裁决 Agent 调用失败，已按评分 Agent 多数结果兜底：{exc}",
            }

    def _majority_fallback(self, valid_results: List[Dict[str, object]]) -> Dict[str, object]:
        quality = self._majority_quality(valid_results)
        reasons = [
            str(result.get("reason", "")).strip()
            for result in valid_results
            if str(result.get("reason", "")).strip()
        ]
        return {
            "answer_correct": quality in CORRECT_LEVELS,
            "correctness_level": quality,
            "reason": "；".join(reasons[:3]) or "根据多个评分 Agent 的多数结果裁决。",
        }

    def _majority_quality(self, results: List[Dict[str, object]]) -> str:
        qualities = [
            normalize_quality(result.get("correctness_level"))
            for result in results
            if normalize_quality(result.get("correctness_level"))
        ]
        if not qualities:
            return "wrong"
        counts = Counter(qualities)
        max_count = max(counts.values())
        tied = [quality for quality, count in counts.items() if count == max_count]
        return sorted(tied, key=lambda quality: QUALITY_ORDER.get(quality, 0))[0]


def parse_agent_json_response(response) -> Dict[str, object]:
    content = get_agent_response_content(response)
    return parse_outer_json_block(content)


def get_agent_response_content(response) -> str:
    message = response["messages"][-1]
    content = getattr(message, "content", None)
    if content is None and isinstance(message, dict):
        content = message.get("content")
    if isinstance(content, list):
        return "".join(
            str(item.get("text", item)) if isinstance(item, dict) else str(item)
            for item in content
        )
    return str(content)


def parse_outer_json_block(response: str) -> dict:
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


def normalize_judge_result(result: Dict[str, object]) -> Dict[str, object]:
    source = dict(result or {})
    quality = normalize_quality(source.get("correctness_level")) or "wrong"
    answer_correct = bool(source.get("answer_correct", quality in CORRECT_LEVELS))
    if quality in CORRECT_LEVELS:
        answer_correct = True
    elif quality in {"wrong", "absurd"}:
        answer_correct = False
    return {
        "answer_correct": answer_correct,
        "correctness_level": quality,
        "reason": str(source.get("reason") or "").strip(),
    }


def normalize_quality(value) -> Optional[str]:
    quality = str(value or "").strip().lower()
    if quality in QUALITY_ORDER:
        return quality
    for candidate in QUALITY_ORDER:
        if candidate in quality:
            return candidate
    return None
