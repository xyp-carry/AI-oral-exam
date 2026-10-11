"""General-purpose, scoped file and Git agent.

The caller binds identity and repository URL when constructing the agent. The
model can select a branch, but cannot choose another user's cache directory.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

from AIOralExamSystem.Agent.base_Agent import BaseAgent
from AIOralExamSystem.Agent.scope_binding import ScopeBinding
from AIOralExamSystem.Agent.ToolOperator import ToolOperator
from AIOralExamSystem.Agent.universal_prompt_builder import UniversalPromptBuilder
from AIOralExamSystem.Graph.dag_task import SharedDagContext
from AIOralExamSystem.Tool.git.git_tool import GitRepositoryTool


class UniversalAgent(ToolOperator, BaseAgent):
    """Use file and Git tools within one user/course/exam repository cache."""

    def __init__(
        self,
        model_settings: dict,
        user_id: str,
        course_id: str,
        exam_id: str,
        repository_url: str | None = None,
        branch: str | None = None,
        thinking: bool = False,
        temperature: float = 0,
        show_tool_io: bool = False,
        tool_event_callback=None,
        dag_model_settings: dict | None = None,
        report_path: str | Path | None = None,
        model_round_callback=None,
    ):
        for key, value in (
            ("user_id", user_id),
            ("course_id", course_id),
            ("exam_id", exam_id),
        ):
            if not str(value or "").strip():
                raise ValueError(f"{key} is required")

        # BaseAgent calls get_tools() in its constructor, so bind scope first.
        self.user_id = str(user_id).strip()
        self.course_id = str(course_id).strip()
        self.exam_id = str(exam_id).strip()
        self.repository_url = str(repository_url or "").strip() or None
        self.requested_branch = str(branch or "").strip() or None
        self.branch_info: dict | None = None
        self.active_branch: str | None = None
        self.active_root: Path | None = None
        self.report_path: Path | None = None
        self.report_write_count = 0
        self.oral_questions: list[dict] = []
        if report_path is not None:
            candidate = Path(report_path).expanduser()
            if candidate.is_symlink():
                raise ValueError("报告文件不能是符号链接")
            candidate = candidate.resolve(strict=True)
            if not candidate.is_file():
                raise ValueError("报告文件不存在或不是普通文件")
            if candidate.name != "report.md" or candidate.parent.name != "AIreport":
                raise ValueError("报告文件必须是 AIreport/report.md")
            self.report_path = candidate
        self._execution_lock = asyncio.Lock()
        # The DAG model uses the main configuration by default, but owns its copy.
        self.dag_model_settings = dict(
            model_settings if dag_model_settings is None else dag_model_settings
        )
        self._dag_context: SharedDagContext | None = None

        cache_tool = GitRepositoryTool("universal_repository_cache")
        project_root = cache_tool._default_storage_root().resolve()
        self.exam_root = (
            project_root
            / "Gitrepositorys"
            / cache_tool._safe_path_part(self.user_id)
            / cache_tool._safe_path_part(self.course_id)
            / cache_tool._safe_path_part(self.exam_id)
        ).resolve()
        try:
            self.exam_root.relative_to(project_root)
        except ValueError as exc:
            raise ValueError("repository cache path escapes the project root") from exc
        self.scope_binding = ScopeBinding(
            user_id=self.user_id,
            course_id=self.course_id,
            exam_id=self.exam_id,
            exam_root=self.exam_root,
            report_path=self.report_path,
        )

        if self.requested_branch and not self.repository_url:
            candidate = (
                self.exam_root / cache_tool._safe_path_part(self.requested_branch)
            ).resolve()
            if candidate.is_dir():
                self.active_root = candidate
                self.active_branch = self.requested_branch

        self.prompt_builder = UniversalPromptBuilder.create()
        universal_model_settings = dict(model_settings)
        universal_model_settings["max_agent_model_calls"] = 100
        super().__init__(
            "UniversalAgent",
            universal_model_settings,
            thinking=thinking,
            response_format=False,
            temperature=temperature,
            show_tool_io=show_tool_io,
            tool_event_callback=tool_event_callback,
            model_round_callback=model_round_callback,
        )
        self.system_prompt = self.prompt_builder.build_system_prompt()

    @staticmethod
    def _json(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, dict):
            messages = value.get("messages") or []
            if messages:
                return UniversalAgent._text(messages[-1])
            if "content" in value:
                return UniversalAgent._text(value["content"])
        content = getattr(value, "content", value)
        if isinstance(content, list):
            return "".join(
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content or "")

    async def execute(self, user_prompt: str) -> dict:
        async with self._execution_lock:
            current_task = UniversalPromptBuilder.build_current_task(
                user_prompt,
                repository_url=self.repository_url,
                requested_branch=self.requested_branch,
            )
            UniversalPromptBuilder.update_runtime(
                self.prompt_builder,
                active_branch=self.active_branch if self._scope() else None,
            )
            self.system_prompt = self.prompt_builder.build_system_prompt()
            self._dag_context = SharedDagContext(
                current_task, self.dag_model_settings.get("max_input_tokens", 12000)
            )
            response = await self.agent.ainvoke(
                {"messages": self.build_prompt_messages(current_task)},
                config={"recursion_limit": 256},
            )
            dag_context_snapshot = await self._dag_context.snapshot()
            dag_step_outputs = await self._dag_context.raw_results()
        return {
            "answer": self._text(response),
            "repository_root": str(self.active_root) if self.active_root else None,
            "branch": self.active_branch,
            "branch_count": (self.branch_info or {}).get("branch_count"),
            "default_branch": (self.branch_info or {}).get("default_branch"),
            "dag_context": dag_context_snapshot,
            "dag_step_outputs": dag_step_outputs,
            "report_write_count": self.report_write_count,
            "oral_questions": list(self.oral_questions),
        }
