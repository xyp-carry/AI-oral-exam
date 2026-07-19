import asyncio
import fnmatch
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool


MAX_RESULTS = 50
EXCLUDED_DIRECTORIES = {".git", "node_modules", ".venv", "venv", "__pycache__"}
DEFAULT_CODE_FILE_PATTERNS = (
    "*.py", "*.js", "*.jsx", "*.ts", "*.tsx", "*.java", "*.c", "*.h",
    "*.cc", "*.cpp", "*.cxx", "*.hpp", "*.cs", "*.go", "*.rs", "*.php",
    "*.rb", "*.swift", "*.kt", "*.kts", "*.scala", "*.sh", "*.bash",
    "*.zsh", "*.sql", "*.html", "*.css", "*.scss", "*.sass", "*.vue",
)


class CodeSearchToolInput(BaseModel):
    keyword: str = Field(description="要搜索的关键词。")
    file_pattern: str = Field(
        default="",
        description='可选的文件匹配模式，例如 "*.py"。不填写时匹配所有代码文件。',
    )
    relative_dir: str = Field(
        default="",
        description="可选的仓库相对目录。不填写时搜索整个代码根目录。",
    )


CodeSearchDescription = (
    "搜索当前用户绑定仓库中的代码关键词。file_pattern 和 relative_dir 可选；"
    "未填写 file_pattern 时搜索常见代码文件，未填写 relative_dir 时搜索整个代码根目录。"
    "最多返回 50 条结果，超过时在结果末尾增加提示。"
)


class CodeSearchTool(BaseTool):
    """Search user-scoped cached repository code and return matching lines."""

    def __init__(self, name: str, user_uuid: str, git_local_address: Optional[str] = None):
        super().__init__(name)
        self.user_uuid = self._require_text(user_uuid, "user_uuid")
        self.git_local_address = self._normalize_optional_text(git_local_address)
        self.description = CodeSearchDescription

    async def _run(self, keyword: str, file_pattern: str = "", relative_dir: str = "") -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor, self.search, keyword, file_pattern, relative_dir
            )

    def search(self, keyword: str, file_pattern: str = "", relative_dir: str = "") -> str:
        logs = []
        matches = []
        active_git_address = None
        truncated = False

        try:
            keyword = self._require_text(keyword, "keyword")
            active_git_address = self._require_text(self.git_local_address, "git_local_address")
            safe_git_address = "/".join(
                self._safe_path_part(part)
                for part in str(active_git_address).replace("\\", "/").split("/")
                if part.strip()
            )
            safe_user_uuid = self._safe_path_part(self.user_uuid)
            code_root = self._code_root(safe_user_uuid, active_git_address)
            logs.append({
                "step": "start",
                "status": "success",
                "user_uuid": self.user_uuid,
                "git_local_address": active_git_address,
                "safe_git_local_address": safe_git_address,
                "code_root": str(code_root),
            })

            if not code_root.is_dir():
                raise ValueError(f"Code root does not exist: {code_root}")

            search_root = self._resolve_code_path(code_root, relative_dir)
            if not search_root.is_dir():
                raise ValueError(
                    f"Search directory does not exist: "
                    f"{self._relative_to_code_root(code_root, search_root)}"
                )

            patterns = self._normalize_file_patterns(file_pattern)
            for file_path in self._iter_files(search_root, patterns):
                for line_number, line in self._iter_matching_lines(file_path, keyword):
                    if len(matches) >= MAX_RESULTS:
                        truncated = True
                        break
                    relative_path = self._relative_to_code_root(code_root, file_path)
                    line_content = line.rstrip("\r\n")
                    matches.append({
                        "relative_path": relative_path,
                        "line_number": line_number,
                        "line_content": line_content,
                        "display": f"{relative_path}:{line_number}: {line_content}",
                    })
                if truncated:
                    break

            result = {
                "mode": "code_search",
                "git_local_address": active_git_address,
                "safe_git_local_address": safe_git_address,
                "code_root": str(code_root),
                "keyword": keyword,
                "file_pattern": file_pattern or "common_code_files",
                "relative_dir": relative_dir or ".",
                "requested_count": MAX_RESULTS,
                "count": len(matches),
                "truncated": truncated,
                "matches": matches,
                "errors": [],
                "logs": logs,
            }
            if truncated:
                result["message"] = (
                    f"搜索结果超过 {MAX_RESULTS} 条，仅返回前 {MAX_RESULTS} 条；"
                    "请缩小搜索目录或指定更具体的文件匹配模式。"
                )
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            logs.append({"step": "error", "status": "error", "message": str(exc)})
            return json.dumps({
                "mode": "code_search",
                "git_local_address": active_git_address,
                "count": len(matches),
                "truncated": truncated,
                "matches": matches,
                "errors": [{"message": str(exc)}],
                "logs": logs,
            }, ensure_ascii=False)

    def _iter_files(self, search_root: Path, patterns: tuple[str, ...]):
        for current_root, directories, filenames in os.walk(search_root, followlinks=False):
            directories[:] = sorted(
                directory for directory in directories if directory not in EXCLUDED_DIRECTORIES
            )
            for filename in sorted(filenames):
                if any(fnmatch.fnmatch(filename, pattern) for pattern in patterns):
                    yield Path(current_root) / filename

    def _iter_matching_lines(self, file_path: Path, keyword: str):
        try:
            with file_path.open("rb") as binary_file:
                if b"\x00" in binary_file.read(4096):
                    return
            with file_path.open("r", encoding="utf-8", errors="replace") as text_file:
                for line_number, line in enumerate(text_file, start=1):
                    if keyword in line:
                        yield line_number, line
        except (OSError, UnicodeError):
            return

    def _normalize_file_patterns(self, file_pattern: str) -> tuple[str, ...]:
        pattern = str(file_pattern or "").strip()
        if not pattern or pattern == "*":
            return DEFAULT_CODE_FILE_PATTERNS
        if pattern.startswith(".") and "/" not in pattern and "*" not in pattern:
            return (f"*{pattern}",)
        return (pattern,)

    def _resolve_code_path(self, code_root: Path, relative_path: str) -> Path:
        clean_path = str(relative_path or "").replace("\\", "/").strip()
        if not clean_path or clean_path == ".":
            return code_root.resolve()
        if clean_path.startswith("/") or re.match(r"^[A-Za-z]:/", clean_path):
            raise ValueError(f"relative_dir must not be absolute: {relative_path}")
        candidate = (code_root / clean_path).resolve()
        code_root_real = code_root.resolve()
        if candidate != code_root_real and code_root_real not in candidate.parents:
            raise ValueError(f"Path escapes repository code root: {relative_path}")
        return candidate

    def _relative_to_code_root(self, code_root: Path, file_path: Path) -> str:
        return file_path.resolve().relative_to(code_root.resolve()).as_posix()

    def _code_root(self, safe_user_uuid: str, safe_git_address: str) -> Path:
        return (
            self._default_storage_root() / "Gitrepositorys" / safe_user_uuid
            / self._safe_repository_path(safe_git_address)
        )

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
        return Path(*parts) if parts else Path("repository")

    def _normalize_optional_text(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _require_text(self, value: Optional[str], name: str) -> str:
        if value is None or not str(value).strip():
            raise ValueError(f"{name} is required.")
        return str(value).strip()

    def get_description(self) -> str:
        return self.description
