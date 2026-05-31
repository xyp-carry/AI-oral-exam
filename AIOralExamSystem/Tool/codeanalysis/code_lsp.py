import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from typing import Literal, Optional

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool
from AIOralExamSystem.Tool.codeanalysis.python import generate_project_maps, query_user_symbols


class CodeLspToolInput(BaseModel):
    action: Literal["project_map", "symbols"] = Field(
        default="project_map",
        description="LSP action. Use project_map for repository overview, symbols for symbol lookup.",
    )
    query: Optional[str] = Field(
        default=None,
        description="Optional symbol search query. Only used when action is symbols.",
    )
    git_local_address: Optional[str] = Field(
        default=None,
        description=(
            "Optional Git repository address or local repository cache identifier. "
            "It is normalized before limiting LSP analysis to one cached repository. "
            "May be omitted when the tool was registered with a bound git_local_address."
        ),
    )
    limit: int = Field(
        default=20,
        description="Maximum symbol matches to return when query is provided. Clamped to 1-100.",
    )
    timeout: int = Field(
        default=20,
        description="LSP server timeout seconds.",
    )


CodeLspDescription = (
    "Use Python LSP analysis for the current user's cached repositories. "
    "action='project_map' returns repository structure, files, imports, diagnostics, and symbol outlines. "
    "action='symbols' returns structured Pyright symbols; pass query to search by function, class, method, "
    "container, or relative path, and optionally pass git_local_address to limit lookup to one repository. "
    "git_local_address may be omitted if the tool instance was initialized with a bound repository address. "
    "This tool is for locating files, symbols, and line numbers; it does not read source snippets directly."
)


class CodeLspTool(BaseTool):
    """User-scoped Python LSP project map and symbol lookup tool."""

    def __init__(self, name: str, user_uuid: str, git_local_address: Optional[str] = None):
        super().__init__(name)
        self.user_uuid = self._require_text(user_uuid, "user_uuid")
        self.git_local_address = self._normalize_optional_text(git_local_address)
        self.description = CodeLspDescription

    async def _run(
        self,
        action: Literal["project_map", "symbols"] = "project_map",
        query: Optional[str] = None,
        git_local_address: Optional[str] = None,
        limit: int = 20,
        timeout: int = 20,
    ) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.analyze_lsp,
                action,
                query,
                git_local_address,
                limit,
                timeout,
            )

    def analyze_lsp(
        self,
        action: str = "project_map",
        query: Optional[str] = None,
        git_local_address: Optional[str] = None,
        limit: int = 20,
        timeout: int = 20,
    ) -> str:
        action = self._require_text(action, "action").strip().lower()
        active_git_address = self._normalize_optional_text(git_local_address) or self.git_local_address
        try:
            if action == "project_map":
                result = generate_project_maps(
                    user_uuid=self.user_uuid,
                    timeout=timeout,
                    git_local_address=active_git_address,
                )
            elif action == "symbols":
                result = query_user_symbols(
                    user_uuid=self.user_uuid,
                    timeout=timeout,
                    query=query,
                    git_local_address=active_git_address,
                    limit=limit,
                )
            else:
                raise ValueError("action must be project_map or symbols.")
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            return json.dumps(
                {
                    "mode": "lsp",
                    "lsp_action": action,
                    "git_local_address": active_git_address,
                    "error": str(exc),
                    "logs": [{"step": "error", "status": "error", "message": str(exc)}],
                },
                ensure_ascii=False,
            )

    def _require_text(self, value: Optional[str], name: str) -> str:
        if value is None or not str(value).strip():
            raise ValueError(f"{name} is required.")
        return str(value).strip()

    def _normalize_optional_text(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def get_description(self) -> str:
        return self.description
