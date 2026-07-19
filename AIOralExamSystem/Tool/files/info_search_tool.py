import json
import os
import re
import shlex
import shutil
import subprocess
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool


PROJECT_ROOT = Path("/root/AI-Oral-exam").resolve()
MAX_STDOUT_BYTES = 2 * 1024 * 1024

NEXT_ACTIONS = {
    "FILE_NOT_FOUND": ["search_similar_file", "list_parent_folder", "retry_with_relative_path"],
    "PATH_OUTSIDE_SCOPE": ["retry_inside_scope_root", "search_by_filename"],
    "PATH_OUTSIDE_PROJECT": ["retry_inside_project_root", "search_by_filename"],
    "SCOPE_NOT_FOUND": ["retry_with_repository_root", "list_available_scope"],
    "FILE_DECODE_FAILED": ["search_filename_only", "skip_binary_file"],
    "TIMEOUT": ["narrow_scope_path", "add_file_globs", "reduce_max_matches"],
    "SEARCH_FAILED": ["narrow_scope_path", "retry_plain_query"],
    "INVALID_INPUT": ["retry_with_non_empty_query_or_file_path"],
}

NON_RECOVERABLE_ERRORS = {"INFO_SEARCH_TOOL_FAILED"}


class InfoSearchToolInput(BaseModel):
    scope_path: str = Field(default=".", description="搜索范围，必须位于 /root/AI-Oral-exam 项目目录内。")
    query: str = Field(default="", description="要搜索的字段或文本。")
    shell_command: Optional[str] = Field(
        default=None,
        description="可选的只读搜索命令，只允许 rg 或 grep，禁止写入、删除和更新操作。",
    )
    file_globs: list[str] = Field(default_factory=list, description="可选的文件匹配规则，例如 *.py 或 *.md。")
    case_sensitive: bool = Field(default=False, description="是否区分大小写。")
    regex: bool = Field(default=False, description="是否将 query 作为正则表达式处理。")
    context_lines: int = Field(default=0, description="每个匹配项附带的上下文行数，范围为 0–5。")
    max_matches: int = Field(default=200, description="最多返回的匹配数量，范围为 1–1000。")
    timeout_seconds: int = Field(default=10, description="搜索命令超时时间，范围为 1–30 秒。")


class FileReadToolInput(BaseModel):
    scope_path: str = Field(default=".", description="读取范围，必须位于 /root/AI-Oral-exam 项目目录内。")
    file_path: str = Field(default="", description="要读取的文件路径，必须位于 scope_path 和项目目录内。")
    start_line: Optional[int] = Field(default=None, description="可选的起始行号，从 1 开始。")
    end_line: Optional[int] = Field(default=None, description="可选的结束行号。")
    max_bytes: int = Field(default=200_000, description="最多返回的 UTF-8 字节数，范围为 1KB–1MB。")


InfoSearchDescription = (
    "在 /root/AI-Oral-exam 项目范围内执行受保护的只读文本搜索。"
    "仅允许 rg 或 grep 查询命令，禁止 shell 控制符、重定向、命令串联、写入删除操作以及项目范围外的路径。"
    "使用 scope_path、query 和 file_globs 定位相关材料。"
    "shell_command 为可选参数，但仍会经过安全校验。"
)

FileReadDescription = (
    "在 /root/AI-Oral-exam 项目范围内读取文件或指定行区间。"
    "file_path 必须位于 scope_path 和项目目录内，不执行任何搜索或写入操作。"
)

class _InfoSearchToolBase(BaseTool):
    """搜索和文件读取工具共用的项目路径、安全校验和结果处理逻辑。"""

    def _resolve_scope(self, scope_path: str, read_mode: bool = False) -> tuple[Optional[Path], Optional[str]]:
        scope = self._resolve_scope_path(scope_path)
        if scope is None:
            if read_mode:
                return None, self._json_read_error("PATH_OUTSIDE_PROJECT", scope_path, "scope_path must be inside /root/AI-Oral-exam")
            return None, self._json_error("PATH_OUTSIDE_PROJECT", scope_path, "scope_path must be inside /root/AI-Oral-exam")
        if not scope.exists():
            if read_mode:
                return None, self._json_read_error("SCOPE_NOT_FOUND", str(scope), "scope_path does not exist")
            return None, self._json_error("SCOPE_NOT_FOUND", str(scope), "scope_path does not exist")
        if not scope.is_dir() and not scope.is_file():
            if read_mode:
                return None, self._json_read_error("INVALID_SCOPE_PATH", str(scope), "scope_path must be a file or directory")
            return None, self._json_error("INVALID_SCOPE_PATH", str(scope), "scope_path must be a file or directory")
        return scope, None

    def _search_impl(
        self,
        scope_path: str = ".",
        query: str = "",
        shell_command: Optional[str] = None,
        file_globs: Optional[list[str]] = None,
        case_sensitive: bool = False,
        regex: bool = False,
        context_lines: int = 0,
        max_matches: int = 200,
        timeout_seconds: int = 10,
    ) -> str:
        scope, error = self._resolve_scope(scope_path)
        if error:
            return error

        max_matches = self._clamp_int(max_matches, 1, 1000, 200)
        timeout_seconds = self._clamp_int(timeout_seconds, 1, 30, 10)
        context_lines = self._clamp_int(context_lines, 0, 5, 0)

        if shell_command:
            guard = self._guard_shell_command(shell_command, scope)
            if not guard["ok"]:
                return json.dumps(guard, ensure_ascii=False)
            command = guard["command"]
            parser_mode = guard["parser_mode"]
        else:
            command, parser_mode = self._build_search_command(
                scope=scope,
                query=query,
                file_globs=file_globs or [],
                case_sensitive=case_sensitive,
                regex=regex,
                context_lines=context_lines,
                max_matches=max_matches,
            )

        return self._run_query_command(
            command=command,
            scope=scope,
            query=query,
            parser_mode=parser_mode,
            max_matches=max_matches,
            timeout_seconds=timeout_seconds,
        )
    def _build_search_command(
        self,
        scope: Path,
        query: str,
        file_globs: list[str],
        case_sensitive: bool,
        regex: bool,
        context_lines: int,
        max_matches: int,
    ) -> tuple[list[str], str]:
        rg_executable = self._find_executable("rg")
        if rg_executable:
            return self._build_rg_command(
                rg_executable,
                scope=scope,
                query=query,
                file_globs=file_globs,
                case_sensitive=case_sensitive,
                regex=regex,
                context_lines=context_lines,
                max_matches=max_matches,
            ), "rg"
        grep_executable = self._find_executable("grep") or "grep"
        return self._build_grep_command(
            grep_executable,
            scope=scope,
            query=query,
            file_globs=file_globs,
            case_sensitive=case_sensitive,
            regex=regex,
            context_lines=context_lines,
            max_matches=max_matches,
        ), "grep"

    def _build_rg_command(
        self,
        executable: str,
        scope: Path,
        query: str,
        file_globs: list[str],
        case_sensitive: bool,
        regex: bool,
        context_lines: int,
        max_matches: int,
    ) -> list[str]:
        query = self._require_text(query, "query")
        command = [executable, "--json", "--color", "never", "-n", "--no-heading", "--max-count", str(max_matches)]
        if not case_sensitive:
            command.append("-i")
        if not regex:
            command.append("-F")
        if context_lines:
            command.extend(["-C", str(context_lines)])
        for glob_pattern in file_globs:
            command.extend(["-g", self._require_safe_glob(glob_pattern)])
        command.extend([query, str(scope)])
        return command

    def _build_grep_command(
        self,
        executable: str,
        scope: Path,
        query: str,
        file_globs: list[str],
        case_sensitive: bool,
        regex: bool,
        context_lines: int,
        max_matches: int,
    ) -> list[str]:
        query = self._require_text(query, "query")
        command = [executable, "-n", "-I"]
        if scope.is_dir():
            command.append("-R")
        if not case_sensitive:
            command.append("-i")
        if not regex:
            command.append("-F")
        if context_lines:
            command.extend(["-C", str(context_lines)])
        for glob_pattern in file_globs:
            command.append(f"--include={self._require_safe_glob(glob_pattern)}")
        command.extend(["-m", str(max_matches), query, str(scope)])
        return command

    def _run_query_command(
        self,
        command: list[str],
        scope: Path,
        query: str,
        parser_mode: str,
        max_matches: int,
        timeout_seconds: int,
    ) -> str:
        try:
            completed = subprocess.run(
                command,
                cwd=str(scope if scope.is_dir() else scope.parent),
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=timeout_seconds,
                shell=False,
                check=False,
            )
        except FileNotFoundError as exc:
            return self._json_error("COMMAND_NOT_FOUND", str(scope), str(exc), command=command)
        except PermissionError as exc:
            return self._json_error("COMMAND_NOT_EXECUTABLE", str(scope), str(exc), command=command)
        except subprocess.TimeoutExpired:
            return self._json_error("TIMEOUT", str(scope), f"query command timed out after {timeout_seconds}s", command=command)
        except OSError as exc:
            return self._json_error("SEARCH_FAILED", str(scope), str(exc), command=command)

        stdout = completed.stdout or ""
        stderr = completed.stderr or ""
        truncated = False
        if len(stdout.encode("utf-8", errors="replace")) > MAX_STDOUT_BYTES:
            stdout = stdout.encode("utf-8", errors="replace")[:MAX_STDOUT_BYTES].decode("utf-8", errors="replace")
            truncated = True

        matches = self._parse_rg_json(stdout, max_matches) if parser_mode == "rg" else self._parse_plain_matches(stdout, max_matches)
        if len(matches) >= max_matches:
            truncated = True

        ok = completed.returncode in {0, 1}
        error_type = None if ok else "SEARCH_FAILED"
        payload = {
            "ok": ok,
            "mode": "info_search",
            "scope_path": str(scope),
            "relative_scope_path": self._relative_to_project(scope),
            "query": query,
            "command": command,
            "returncode": completed.returncode,
            "count": len(matches),
            "truncated": truncated,
            "matches": matches,
            "stderr": stderr[:4000],
            "error_type": error_type,
        }
        if error_type:
            payload.update(self._error_meta(error_type))
        return json.dumps(payload, ensure_ascii=False)

    def _read_file(
        self,
        scope: Path,
        file_path: str,
        start_line: Optional[int],
        end_line: Optional[int],
        max_bytes: int,
    ) -> str:
        base_scope = scope if scope.is_dir() else scope.parent
        path = self._resolve_path_against_scope(file_path, base_scope)
        if path is None:
            return self._json_read_error("PATH_OUTSIDE_PROJECT", file_path, "file_path escapes project root")
        if scope.is_dir():
            try:
                path.relative_to(scope.resolve())
            except ValueError:
                return self._json_read_error("PATH_OUTSIDE_SCOPE", str(path), "file_path escapes scope_path")
        elif path != scope.resolve():
            return self._json_read_error("PATH_OUTSIDE_SCOPE", str(path), "file_path must match scope_path when scope_path is a file")
        if not path.exists():
            return self._json_read_error("FILE_NOT_FOUND", str(path), "file_path does not exist")
        if not path.is_file():
            return self._json_read_error("PATH_IS_NOT_FILE", str(path), "file_path is not a file")

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return self._json_read_error("FILE_DECODE_FAILED", str(path), str(exc))
        except OSError as exc:
            return self._json_read_error("FILE_READ_FAILED", str(path), str(exc))

        total_lines = len(text.splitlines())
        resolved_start, resolved_end = self._normalize_line_range(start_line, end_line, total_lines)
        if resolved_start is not None:
            lines = text.splitlines()
            content = "\n".join(lines[resolved_start - 1 : resolved_end])
        else:
            content = text

        max_bytes = self._clamp_int(max_bytes, 1024, 1024 * 1024, 200_000)
        encoded = content.encode("utf-8", errors="replace")
        truncated = len(encoded) > max_bytes
        if truncated:
            content = encoded[:max_bytes].decode("utf-8", errors="replace")

        return json.dumps(
            {
                "ok": True,
                "mode": "file_read",
                "file_path": str(path),
                "relative_file_path": self._relative_to_project(path),
                "start_line": resolved_start,
                "end_line": resolved_end,
                "total_lines": total_lines,
                "returned_lines": len(content.splitlines()),
                "max_bytes": max_bytes,
                "truncated": truncated,
                "content": content,
            },
            ensure_ascii=False,
        )

    def _normalize_line_range(
        self,
        start_line: Optional[int],
        end_line: Optional[int],
        total_lines: int,
    ) -> tuple[Optional[int], Optional[int]]:
        if start_line is None and end_line is None:
            return None, None
        start = self._clamp_int(start_line, 1, max(1, total_lines), 1)
        end_default = total_lines or start
        end = self._clamp_int(end_line, start, max(start, total_lines), end_default)
        return start, end

    def _parse_rg_json(self, stdout: str, max_matches: int) -> list[dict]:
        matches = []
        for line in stdout.splitlines():
            if len(matches) >= max_matches:
                break
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            if event.get("type") != "match":
                continue
            data = event.get("data") or {}
            path_text = ((data.get("path") or {}).get("text") or "").strip()
            line_text = ((data.get("lines") or {}).get("text") or "").rstrip("\n")
            submatches = []
            for item in data.get("submatches") or []:
                submatches.append(
                    {
                        "match": ((item.get("match") or {}).get("text") or ""),
                        "start": item.get("start"),
                        "end": item.get("end"),
                    }
                )
            matches.append(
                {
                    "file_path": self._relative_to_project(Path(path_text)) if path_text else "",
                    "absolute_file_path": path_text,
                    "line_number": data.get("line_number"),
                    "line": line_text,
                    "submatches": submatches,
                }
            )
        return matches

    def _parse_plain_matches(self, stdout: str, max_matches: int) -> list[dict]:
        matches = []
        for line in stdout.splitlines():
            if len(matches) >= max_matches:
                break
            file_path = ""
            line_number = None
            text = line
            parts = line.split(":", 2)
            if len(parts) == 3 and parts[1].isdigit():
                file_path, line_number_text, text = parts
                line_number = int(line_number_text)
            matches.append(
                {
                    "file_path": self._relative_to_project(Path(file_path)) if file_path else "",
                    "absolute_file_path": file_path,
                    "line_number": line_number,
                    "line": text,
                    "submatches": [],
                }
            )
        return matches

    def _guard_shell_command(self, shell_command: str, scope: Path) -> dict:
        raw = self._require_text(shell_command, "shell_command")
        forbidden_chars = [";", "|", "&", ">", "<", "`", "$", "\n", "\r"]
        if any(char in raw for char in forbidden_chars):
            return self._guard_error("FORBIDDEN_SHELL_OPERATOR", raw, "shell control operators, redirection, substitution, and chaining are not allowed")
        try:
            tokens = shlex.split(raw)
        except ValueError as exc:
            return self._guard_error("INVALID_COMMAND", raw, str(exc))
        if not tokens:
            return self._guard_error("EMPTY_COMMAND", raw, "shell_command is empty")

        command_name = Path(tokens[0]).name
        if command_name not in {"rg", "grep"}:
            return self._guard_error("COMMAND_NOT_ALLOWED", raw, "only read-only query commands rg and grep are allowed")

        dangerous_options = {
            "--files-without-match",
            "--generate",
            "--pcre2-version",
            "--pre",
            "--pre-glob",
            "--replace",
            "--passthru",
        }
        dangerous_words = {
            "rm", "mv", "cp", "sed", "awk", "perl", "python", "python3", "node", "sh", "bash", "chmod", "chown",
            "touch", "truncate", "tee", "curl", "wget", "git", "find", "xargs", "dd", "mkfs", "rmdir", "mkdir",
        }
        for token in tokens[1:]:
            lower = token.lower()
            if lower in dangerous_words:
                return self._guard_error("FORBIDDEN_MUTATION_WORD", raw, f"forbidden token: {token}")
            if lower in dangerous_options or lower.startswith("--replace=") or lower.startswith("--pre="):
                return self._guard_error("FORBIDDEN_QUERY_OPTION", raw, f"forbidden option: {token}")

        command = list(tokens)
        parser_mode = command_name
        if command_name == "rg":
            executable = self._find_executable("rg")
            if executable:
                command[0] = executable
            if "--json" not in command:
                command.insert(1, "--json")
            if "--color" not in command and not any(item.startswith("--color=") for item in command):
                command[1:1] = ["--color", "never"]
            parser_mode = "rg"
        else:
            executable = self._find_executable("grep")
            if executable:
                command[0] = executable
            if not any(item.startswith("-n") or item == "--line-number" for item in command):
                command.insert(1, "-n")
            if not any("R" in item or "r" in item for item in command if item.startswith("-")):
                command.insert(1, "-R")
            parser_mode = "grep"

        path_tokens = self._extract_existing_path_tokens(command[1:])
        for path_token in path_tokens:
            path = self._resolve_path_against_scope(path_token, scope)
            if path is None:
                return self._guard_error("PATH_OUTSIDE_PROJECT", raw, f"path argument escapes project root: {path_token}")
        if not path_tokens:
            command.append(str(scope))
        return {"ok": True, "command": command, "parser_mode": parser_mode}

    def _extract_existing_path_tokens(self, tokens: list[str]) -> list[str]:
        result = []
        skip_next = False
        options_with_value = {"-g", "--glob", "-e", "--regexp", "-C", "-A", "-B", "-m", "--max-count", "--context"}
        for index, token in enumerate(tokens):
            if skip_next:
                skip_next = False
                continue
            if token in options_with_value:
                skip_next = True
                continue
            if token.startswith("-"):
                continue
            candidate = Path(token).expanduser()
            if candidate.exists() or (index == len(tokens) - 1 and ("/" in token or token in {".", ".."})):
                result.append(token)
        return result

    def _resolve_scope_path(self, scope_path: str) -> Optional[Path]:
        if not scope_path:
            return None
        raw_path = Path(str(scope_path)).expanduser()
        if not raw_path.is_absolute():
            raw_path = PROJECT_ROOT / raw_path
        path = raw_path.resolve()
        try:
            path.relative_to(PROJECT_ROOT)
        except ValueError:
            return None
        return path

    def _resolve_path_against_scope(self, value: str, scope: Path) -> Optional[Path]:
        raw_path = Path(str(value)).expanduser()
        if not raw_path.is_absolute():
            raw_path = scope / raw_path
        path = raw_path.resolve()
        try:
            path.relative_to(PROJECT_ROOT)
        except ValueError:
            return None
        return path

    def _require_safe_glob(self, glob_pattern: str) -> str:
        text = self._require_text(glob_pattern, "file_glob")
        if any(item in text for item in [";", "|", "&", ">", "<", "`", "$", "\n", "\r"]):
            raise ValueError(f"unsafe file_glob: {glob_pattern}")
        return text

    def _relative_to_project(self, path: Path) -> str:
        try:
            return path.resolve().relative_to(PROJECT_ROOT).as_posix()
        except (OSError, ValueError):
            return str(path)

    def _find_executable(self, name: str) -> Optional[str]:
        executable = shutil.which(name)
        if executable and os.access(executable, os.X_OK):
            return executable
        return None

    def _clamp_int(self, value, minimum: int, maximum: int, default: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            number = default
        return min(max(number, minimum), maximum)

    def _require_text(self, value: Optional[str], name: str) -> str:
        if value is None or not str(value).strip():
            raise ValueError(f"{name} is required.")
        return str(value).strip()

    def _guard_error(self, error_type: str, raw_command: str, message: str) -> dict:
        payload = {
            "ok": False,
            "mode": "info_search",
            "error_type": error_type,
            "error_message": message,
            "raw_command": raw_command,
            "matches": [],
        }
        payload.update(self._error_meta(error_type))
        return payload

    def _json_error(self, error_type: str, scope_path: str, error_message: str, command: Optional[list[str]] = None) -> str:
        payload = {
            "ok": False,
            "mode": "info_search",
            "error_type": error_type,
            "project_root": str(PROJECT_ROOT),
            "scope_path": str(scope_path or ""),
            "command": command or [],
            "error_message": error_message,
            "matches": [],
        }
        payload.update(self._error_meta(error_type))
        return json.dumps(payload, ensure_ascii=False)
    def _json_read_error(self, error_type: str, file_path: str, error_message: str) -> str:
        payload = {
            "ok": False,
            "mode": "file_read",
            "error_type": error_type,
            "project_root": str(PROJECT_ROOT),
            "file_path": str(file_path or ""),
            "error_message": error_message,
            "content": "",
        }
        payload.update(self._error_meta(error_type))
        return json.dumps(payload, ensure_ascii=False)
    def _error_meta(self, error_type: str) -> dict:
        error_type = str(error_type or "UNKNOWN_ERROR")
        return {
            "recoverable": error_type not in NON_RECOVERABLE_ERRORS,
            "next_actions": NEXT_ACTIONS.get(error_type, ["try_alternative_path", "continue_with_available_evidence"]),
        }


class InfoSearchTool(_InfoSearchToolBase):
    """仅负责项目范围内的只读文本搜索。"""

    def __init__(self, name: str):
        super().__init__(name)
        self.description = InfoSearchDescription

    def _run(
        self,
        scope_path: str = ".",
        query: str = "",
        shell_command: Optional[str] = None,
        file_globs: Optional[list[str]] = None,
        case_sensitive: bool = False,
        regex: bool = False,
        context_lines: int = 0,
        max_matches: int = 200,
        timeout_seconds: int = 10,
    ) -> str:
        try:
            return self._search_impl(
                scope_path=scope_path,
                query=query,
                shell_command=shell_command,
                file_globs=file_globs,
                case_sensitive=case_sensitive,
                regex=regex,
                context_lines=context_lines,
                max_matches=max_matches,
                timeout_seconds=timeout_seconds,
            )
        except ValueError as exc:
            return self._json_error("INVALID_INPUT", scope_path, str(exc))
        except Exception as exc:
            return self._json_error("INFO_SEARCH_TOOL_FAILED", scope_path, str(exc))


class FileReadTool(_InfoSearchToolBase):
    """仅负责项目范围内的文件或行区间读取。"""

    def __init__(self, name: str):
        super().__init__(name)
        self.description = FileReadDescription

    def _run(
        self,
        scope_path: str = ".",
        file_path: str = "",
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
        max_bytes: int = 200_000,
    ) -> str:
        try:
            scope, error = self._resolve_scope(scope_path, read_mode=True)
            if error:
                return error
            return self._read_file(
                scope=scope,
                file_path=file_path or str(scope),
                start_line=start_line,
                end_line=end_line,
                max_bytes=max_bytes,
            )
        except ValueError as exc:
            return self._json_read_error("INVALID_INPUT", file_path or scope_path, str(exc))
        except Exception as exc:
            return self._json_read_error("INFO_SEARCH_TOOL_FAILED", file_path or scope_path, str(exc))