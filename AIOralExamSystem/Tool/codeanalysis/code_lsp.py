import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool
from AIOralExamSystem.Tool.codeanalysis.python import (
    generate_project_map_for_code_root,
    query_symbols_for_code_root,
)


class CodeLspToolInput(BaseModel):
    action: Literal["project_map", "symbols"] = Field(
        default="project_map",
        description="LSP action. Use project_map for repository overview, symbols for symbol lookup.",
    )
    query: Optional[str] = Field(
        default=None,
        description="Optional symbol search query. Only used when action is symbols.",
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
    "container, or relative path. The repository address is bound by the system when this tool is initialized "
    "and cannot be overridden by tool input. "
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
                limit,
                timeout,
            )

    def analyze_lsp(
        self,
        action: str = "project_map",
        query: Optional[str] = None,
        limit: int = 20,
        timeout: int = 20,
    ) -> str:
        active_git_address = None
        try:
            active_git_address = self._require_text(self.git_local_address, "git_local_address")
            safe_user_uuid = self._safe_path_part(self.user_uuid)
            code_root = self._code_root(safe_user_uuid, active_git_address)
            repository_name = self._repository_name(safe_user_uuid, code_root)
            
            if not code_root.is_dir():
                raise ValueError(f"Code root does not exist: {code_root}")
            action = self._require_text(action, "action").strip().lower()
            if action == "project_map":
                result = generate_project_map_for_code_root(
                    user_uuid=self.user_uuid,
                    repository_name=repository_name,
                    code_root=code_root,
                    timeout=timeout,
                    git_local_address=active_git_address,
                )
            elif action == "symbols":
                result = query_symbols_for_code_root(
                    user_uuid=self.user_uuid,
                    repository_name=repository_name,
                    code_root=code_root,
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

    def _code_root(self, safe_user_uuid: str, git_local_address: str) -> Path:
        return (
            self._default_storage_root()
            / "Gitrepositorys"
            / safe_user_uuid
            / self._safe_repository_path(git_local_address)
        )

    def _repository_name(self, safe_user_uuid: str, code_root: Path) -> str:
        user_root = self._default_storage_root() / "Gitrepositorys" / safe_user_uuid
        return code_root.parent.relative_to(user_root).as_posix()

    def _default_storage_root(self) -> Path:
        current_file = Path(__file__).resolve()
        for parent in current_file.parents:
            if parent.name == "AIOralExamSystem":
                return parent.parent
        return Path.cwd()

    def _safe_path_part(self, value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", str(value).strip())
        return safe.strip("._-") or "repository"

    def _safe_repository_path(self, value: str) -> Path:
        parts = [
            self._safe_path_part(part)
            for part in str(value).replace("\\", "/").split("/")
            if part.strip()
        ]
        if not parts:
            return Path("repository")
        return Path(*parts)

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
