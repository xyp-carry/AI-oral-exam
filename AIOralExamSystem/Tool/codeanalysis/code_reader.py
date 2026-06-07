import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool

class CodeReaderToolInput(BaseModel):
    file_path: str = Field(
        description="File path relative to the selected repository code root.",
    )
    start_line: int = Field(
        description="Inclusive 1-based start line.",
    )
    end_line: int = Field(
        description="Inclusive 1-based end line.",
    )


CodeReaderDescription = (
    "Read one source-code line range from the system-bound current-user repository cache. "
    "`file_path` must be relative to the bound repository code root and cannot escape it. "
    "The repository address is bound by the system when this tool is initialized and cannot be overridden "
    "by tool input. Pass `file_path`, `start_line`, and `end_line`."
)


class CodeReaderTool(BaseTool):
    """Read user-scoped cached repository code by relative path and line range."""

    def __init__(self, name: str, user_uuid: str, git_local_address: Optional[str] = None):
        super().__init__(name)
        self.user_uuid = self._require_text(user_uuid, "user_uuid")
        self.git_local_address = self._normalize_optional_text(git_local_address)
        self.description = CodeReaderDescription

    async def _run(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
    ) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.read_range,
                file_path,
                start_line,
                end_line,
            )

    def read_range(
        self,
        file_path: str,
        start_line: int,
        end_line: int,
    ) -> str:
        logs = []
        fragments = []
        errors = []

        active_git_address = None
        try:
            active_git_address = self._require_text(self.git_local_address, "git_local_address")
            safe_git_address = "/".join(
                self._safe_path_part(part)
                for part in str(active_git_address).replace("\\", "/").split("/")
                if part.strip()
            )
            safe_user_uuid = self._safe_path_part(self.user_uuid)
            code_root = self._code_root(safe_user_uuid, active_git_address)

            logs.append(
                {
                    "step": "start",
                    "status": "success",
                    "user_uuid": self.user_uuid,
                    "git_local_address": active_git_address,
                    "safe_git_local_address": safe_git_address,
                    "code_root": str(code_root),
                }
            )

            if not code_root.is_dir():
                raise ValueError(f"Code root does not exist: {code_root}")

            target_path = self._resolve_code_path(code_root, file_path)
            fragments.append(self._read_one_range(code_root, target_path, start_line, end_line))

            return json.dumps(
                {
                    "mode": "code_reader",
                    "git_local_address": active_git_address,
                    "safe_git_local_address": safe_git_address,
                    "code_root": str(code_root),
                    "requested_count": 1,
                    "count": len(fragments),
                    "fragments": fragments,
                    "errors": errors,
                    "logs": logs,
                },
                ensure_ascii=False,
            )
        except Exception as exc:
            logs.append({"step": "error", "status": "error", "message": str(exc)})
            return json.dumps(
                {
                    "mode": "code_reader",
                    "git_local_address": active_git_address,
                    "count": 0,
                    "fragments": fragments,
                    "errors": errors + [{"message": str(exc)}],
                    "logs": logs,
                },
                ensure_ascii=False,
            )

    def _read_one_range(self, code_root: Path, file_path: Path, start_line: int, end_line: int) -> dict[str, Any]:
        start_line = self._require_positive_int(start_line, "start_line")
        end_line = self._require_positive_int(end_line, "end_line")
        if end_line < start_line:
            raise ValueError("end_line must be greater than or equal to start_line.")

        if not file_path.is_file():
            raise ValueError(f"Code file does not exist: {self._relative_to_code_root(code_root, file_path)}")

        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines = len(lines)
        if start_line > total_lines:
            raise ValueError(
                f"start_line {start_line} exceeds file length {total_lines}: "
                f"{self._relative_to_code_root(code_root, file_path)}"
            )

        actual_end_line = min(end_line, total_lines)
        content = "\n".join(lines[start_line - 1:actual_end_line])
        return {
            "relative_path": self._relative_to_code_root(code_root, file_path),
            "start_line": start_line,
            "end_line": end_line,
            "actual_start_line": start_line,
            "actual_end_line": actual_end_line,
            "total_lines": total_lines,
            "content": content,
        }

    def _resolve_code_path(self, code_root: Path, relative_path: str) -> Path:
        clean_path = relative_path.replace("\\", "/").strip()
        if not clean_path:
            raise ValueError("relative_path is required.")
        if clean_path.startswith("/") or re.match(r"^[A-Za-z]:/", clean_path):
            raise ValueError(f"relative_path must not be absolute: {relative_path}")

        candidate = (code_root / clean_path).resolve()
        code_root_real = code_root.resolve()
        if candidate != code_root_real and code_root_real not in candidate.parents:
            raise ValueError(f"Path escapes repository code root: {relative_path}")
        return candidate

    def _relative_to_code_root(self, code_root: Path, file_path: Path) -> str:
        return file_path.resolve().relative_to(code_root.resolve()).as_posix()

    def _code_root(self, safe_user_uuid: str, safe_git_address: str) -> Path:
        return self._default_storage_root() / "Gitrepositorys" / safe_user_uuid / self._safe_repository_path(safe_git_address)

    def _default_storage_root(self) -> Path:
        current_file = Path(__file__).resolve()
        for parent in current_file.parents:
            if parent.name == "AIOralExamSystem":
                return parent.parent
        return Path.cwd()

    def _safe_path_part(self, value: str) -> str:
        safe = re.sub(r"[^A-Za-z0-9._-]+", "_", value.strip())
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

    def _normalize_optional_text(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _require_text(self, value: Optional[str], name: str) -> str:
        if value is None or not str(value).strip():
            raise ValueError(f"{name} is required.")
        return str(value).strip()

    def _require_positive_int(self, value: Any, name: str) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError(f"{name} must be an integer.") from exc
        if number < 1:
            raise ValueError(f"{name} must be greater than or equal to 1.")
        return number

    def get_description(self) -> str:
        return self.description
