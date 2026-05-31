import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool


MAX_CODE_RANGES = 10


class CodeReadRange(BaseModel):
    relative_path: str = Field(
        description="File path relative to the selected repository code root.",
    )
    start_line: int = Field(
        description="Inclusive 1-based start line.",
    )
    end_line: int = Field(
        description="Inclusive 1-based end line.",
    )


class CodeReaderToolInput(BaseModel):
    git_local_address: Optional[str] = Field(
        default=None,
        description=(
            "Git repository address or local repository cache identifier. It is normalized before "
            "locating Gitrepositorys/{user_uuid}/{normalized_git_local_address}/code. "
            "May be omitted only when the tool was registered with a bound git_local_address."
        ),
    )
    ranges: list[CodeReadRange] = Field(
        description="Array of code fragments to read. At most 10 fragments are allowed in one call.",
    )


CodeReaderDescription = (
    "Read source-code line ranges from a specific current-user repository cache. "
    "Input must be a dictionary containing `git_local_address` and a `ranges` array, unless "
    "the tool was registered with a bound `git_local_address`. `git_local_address` is normalized "
    "the same way as GitRepositoryTool cache names, then resolved under "
    "Gitrepositorys/{user_uuid}/{normalized_git_local_address}/code. Each range contains "
    "`relative_path`, `start_line`, and `end_line`. At most 10 ranges are allowed per call. "
    "Paths must be relative and cannot escape the selected repository code root."
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
        ranges: list[dict[str, Any]] | list[CodeReadRange],
        git_local_address: Optional[str] = None,
    ) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.read_ranges,
                ranges,
                git_local_address,
            )

    def read_ranges(
        self,
        ranges: list[dict[str, Any]] | list[CodeReadRange],
        git_local_address: Optional[str] = None,
    ) -> str:
        logs = []
        fragments = []
        errors = []

        active_git_address = self._normalize_optional_text(git_local_address) or self.git_local_address
        try:
            active_git_address = self._require_text(active_git_address, "git_local_address")
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

            normalized_ranges = self._normalize_ranges(ranges)
            if len(normalized_ranges) > MAX_CODE_RANGES:
                raise ValueError(f"At most {MAX_CODE_RANGES} code ranges are allowed per call.")

            for index, requested_range in enumerate(normalized_ranges):
                try:
                    fragments.append(self._read_one_range(code_root, requested_range))
                except Exception as exc:
                    errors.append(
                        {
                            "index": index,
                            "relative_path": requested_range.get("relative_path"),
                            "message": str(exc),
                        }
                    )

            return json.dumps(
                {
                    "mode": "code_reader",
                    "git_local_address": active_git_address,
                    "safe_git_local_address": safe_git_address,
                    "code_root": str(code_root),
                    "requested_count": len(normalized_ranges),
                    "count": len(fragments),
                    "max_ranges": MAX_CODE_RANGES,
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
                    "max_ranges": MAX_CODE_RANGES,
                    "fragments": fragments,
                    "errors": errors + [{"message": str(exc)}],
                    "logs": logs,
                },
                ensure_ascii=False,
            )

    def _read_one_range(self, code_root: Path, requested_range: dict[str, Any]) -> dict[str, Any]:
        relative_path = self._require_text(requested_range.get("relative_path"), "relative_path")
        start_line = self._require_positive_int(requested_range.get("start_line"), "start_line")
        end_line = self._require_positive_int(requested_range.get("end_line"), "end_line")
        if end_line < start_line:
            raise ValueError("end_line must be greater than or equal to start_line.")

        file_path = self._resolve_code_path(code_root, relative_path)
        if not file_path.is_file():
            raise ValueError(f"Code file does not exist: {relative_path}")

        lines = file_path.read_text(encoding="utf-8", errors="replace").splitlines()
        total_lines = len(lines)
        if start_line > total_lines:
            raise ValueError(
                f"start_line {start_line} exceeds file length {total_lines}: {relative_path}"
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

    def _normalize_ranges(self, ranges: list[dict[str, Any]] | list[CodeReadRange]) -> list[dict[str, Any]]:
        if not isinstance(ranges, list):
            raise ValueError("ranges must be an array.")
        normalized = []
        for item in ranges:
            if isinstance(item, BaseModel):
                normalized.append(item.model_dump())
            elif isinstance(item, dict):
                normalized.append(item)
            else:
                raise ValueError("Each range must be a dictionary.")
        return normalized

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
        return self._default_storage_root() / "Gitrepositorys" / safe_user_uuid / self._safe_repository_path(safe_git_address) / "code"

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
