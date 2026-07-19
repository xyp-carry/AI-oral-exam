import json
import re
from typing import Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from AIOralExamSystem.Agent.base_Agent import BaseAgent
from AIOralExamSystem.Tool.git.git_tool import (
    GitHistoryDescription,
    GitHistoryTool,
    GitHistoryToolInput,
)


class GitHistoryAnswerFormat(BaseModel):
    ok: bool = True
    flag: str = "GIT_HISTORY_ANSWER"
    answer: str = ""
    branch: str = ""
    commits: list[dict] = Field(default_factory=list)
    commit_detail: dict = Field(default_factory=dict)
    evidence: list[str] = Field(default_factory=list)
    missing_information: list[str] = Field(default_factory=list)


class GitHistoryReaderAgent(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        thinking: bool = False,
        response_format: bool = True,
        temperature: float = 0,
        show_tool_io: bool = False,
    ):
        super().__init__(
            "GitHistoryReaderAgent",
            model_settings,
            thinking,
            response_format,
            temperature,
            show_tool_io=show_tool_io,
        )
        self.system_prompt = self.build_system_prompt()

    def build_system_prompt(self) -> str:
        return """
        ## Role
        You are a git history reading agent. You answer by using gitHistory only.

        ## Tool
        - gitHistory: reads local git branch/history or a commit detail with read-only commands.

        ## Rules
        - git clone is forbidden. Do not clone, fetch, pull, or download repositories.
        - repo_path must be an existing local repository path. If the input looks like a git URL, return GIT_CLONE_NOT_ALLOWED.
        - If gitHistory returns NO_GIT_BRANCH_INFO, answer that there is no git branch information.
        - For history questions, summarize recent commits, current branch, and working-tree status.
        - For commit detail questions, summarize changed files, key patch points, and possible impact.
        - Do not invent commits or diffs. Use only tool evidence.
        - Return strict JSON only.

        ## Output
        {
          "ok": true,
          "flag": "GIT_HISTORY_ANSWER",
          "answer": "summary",
          "branch": "current branch",
          "commits": [],
          "commit_detail": {},
          "evidence": [],
          "missing_information": []
        }
        """

    def get_tools(self):
        @tool(args_schema=GitHistoryToolInput, description=GitHistoryDescription)
        async def gitHistory(
            repo_path: str,
            mode: str = "history",
            commit_hash: Optional[str] = None,
        ) -> str:
            url_error = self.git_clone_not_allowed_result(repo_path)
            if url_error is not None:
                return json.dumps(url_error, ensure_ascii=False)
            git_history_tool = GitHistoryTool("git_history_tool")
            return await git_history_tool.execute(
                repo_path=repo_path,
                mode=mode,
                commit_hash=commit_hash,
            )

        return [gitHistory]

    async def execute(
        self,
        repo_path: str,
        prompt: str = "",
        commit_hash: Optional[str] = None,
    ) -> dict:
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        try:
            url_error = self.git_clone_not_allowed_result(repo_path)
            if url_error is not None:
                return url_error
            mode = "commit_detail" if commit_hash else "history"
            raw_context = await self.load_git_context(repo_path, mode, commit_hash)
            context = self.loads_json(raw_context)
            if not context.get("ok") and context.get("flag") == "NO_GIT_BRANCH_INFO":
                return {
                    "ok": False,
                    "flag": "NO_GIT_BRANCH_INFO",
                    "answer": "没有git分支信息。",
                    "branch": "",
                    "commits": [],
                    "commit_detail": {},
                    "evidence": [],
                    "missing_information": ["repo_path下不存在可读取的git仓库信息。"],
                }

            messages = [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": self.build_user_prompt(
                        repo_path=repo_path,
                        prompt=prompt,
                        mode=mode,
                        commit_hash=commit_hash,
                        context=context,
                    ),
                },
            ]
            response = await self.agent.ainvoke({"messages": messages})
            return self.normalize_result(self.extract_json_object(self.message_to_text(response)), context)
        except Exception as exc:
            return {
                "ok": False,
                "flag": "GIT_HISTORY_READER_FAILED",
                "answer": "",
                "branch": "",
                "commits": [],
                "commit_detail": {},
                "evidence": [],
                "missing_information": [f"{exc.__class__.__name__}: {exc}"],
            }
        finally:
            await self.stop_heartbeat()

    async def load_git_context(
        self,
        repo_path: str,
        mode: str,
        commit_hash: Optional[str],
    ) -> str:
        url_error = self.git_clone_not_allowed_result(repo_path)
        if url_error is not None:
            return json.dumps(url_error, ensure_ascii=False)
        git_history_tool = GitHistoryTool("git_history_tool")
        return await git_history_tool.execute(
            repo_path=repo_path,
            mode=mode,
            commit_hash=commit_hash,
        )

    def build_user_prompt(
        self,
        repo_path: str,
        prompt: str,
        mode: str,
        commit_hash: Optional[str],
        context: dict,
    ) -> str:
        return f"""
        repo_path: {repo_path}
        mode: {mode}
        commit_hash: {commit_hash}

        User request:
        {prompt or "Summarize git history or commit details."}

        gitHistory context:
        {json.dumps(context, ensure_ascii=False)}

        Answer from the context only. Return strict JSON.
        """

    def normalize_result(self, data: dict, context: dict) -> dict:
        return {
            "ok": bool(data.get("ok", context.get("ok", True))),
            "flag": str(data.get("flag") or "GIT_HISTORY_ANSWER"),
            "answer": str(data.get("answer") or "").strip(),
            "branch": str(data.get("branch") or context.get("branch") or "").strip(),
            "commits": data.get("commits") if isinstance(data.get("commits"), list) else context.get("history") or [],
            "commit_detail": data.get("commit_detail")
            if isinstance(data.get("commit_detail"), dict)
            else context.get("commit_detail") or {},
            "evidence": self.normalize_list(data.get("evidence")),
            "missing_information": self.normalize_list(data.get("missing_information")),
        }

    def git_clone_not_allowed_result(self, repo_path: str) -> dict | None:
        text = str(repo_path or "").strip()
        if not text:
            return None
        if self.looks_like_git_remote(text):
            return {
                "ok": False,
                "flag": "GIT_CLONE_NOT_ALLOWED",
                "answer": "",
                "branch": "",
                "commits": [],
                "commit_detail": {},
                "evidence": [],
                "missing_information": ["GitHistoryReaderAgent only reads existing local git repositories; git clone is not allowed."],
            }
        return None

    def looks_like_git_remote(self, value: str) -> bool:
        text = str(value or "").strip()
        if re.match(r"^(https?|ssh|git)://", text, re.IGNORECASE):
            return True
        if re.match(r"^[A-Za-z0-9._%+-]+@[^:]+:.+\.git/?$", text):
            return True
        if re.match(r"^[^/\\\s]+/[^/\\\s]+(?:/[^/\\\s]+)*\.git/?$", text) and not text.startswith((".", "/")):
            return True
        return False

    def message_to_text(self, response) -> str:
        if isinstance(response, dict):
            messages = response.get("messages")
            if messages:
                return self.message_to_text(messages[-1])
            content = response.get("content")
            if content is not None:
                return self.message_to_text(content)

        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content or "")

    def extract_json_object(self, text: str) -> dict:
        cleaned = text.strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not json_match:
                raise
            data = json.loads(json_match.group(0))
        if not isinstance(data, dict):
            raise ValueError("GitHistoryReaderAgent response must be a JSON object")
        return data

    def loads_json(self, value) -> dict:
        if isinstance(value, dict):
            return value
        return json.loads(str(value or "{}"))

    def normalize_list(self, value) -> list:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    def get_response_format(self):
        return GitHistoryAnswerFormat


Githistoryreader = GitHistoryReaderAgent
