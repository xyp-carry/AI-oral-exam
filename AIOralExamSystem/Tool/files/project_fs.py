"""Read-only, project-scoped filesystem operations shared by Agent tools."""

from __future__ import annotations

import base64
from collections import deque
from datetime import datetime, timezone
import fnmatch
import json
import os
from pathlib import Path
import re
import shutil
import stat
import subprocess


PROJECT_ROOT = Path("/root/AI-Oral-exam").resolve()
MAX_READ_BYTES = 1024 * 1024
MAX_LINES = 1000
MAX_ENTRIES = 5000
MAX_SEARCH_OUTPUT_BYTES = 2 * 1024 * 1024


class FileToolError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


class ProjectFiles:
    """Filesystem access confined to a project root and one allowed scope."""

    def __init__(self, root: str | Path = PROJECT_ROOT, scope: str | Path | None = None):
        self.root = Path(root).resolve()
        self.scope = Path(scope or self.root).resolve()
        if not self._inside(self.scope, self.root):
            raise FileToolError("PATH_OUTSIDE_PROJECT", "scope escapes project root")
        if not self.scope.is_dir():
            raise FileToolError("SCOPE_NOT_FOUND", "scope must be an existing directory")

    @staticmethod
    def _inside(path: Path, root: Path) -> bool:
        try:
            path.relative_to(root)
            return True
        except ValueError:
            return False

    def resolve(self, path: str) -> Path:
        if not str(path or "").strip():
            raise FileToolError("PATH_REQUIRED", "path is required")
        raw = Path(str(path)).expanduser()
        candidate = Path(os.path.abspath(str(raw if raw.is_absolute() else self.scope / raw)))
        resolved = candidate.resolve(strict=False)
        if not self._inside(candidate, self.root) or not self._inside(resolved, self.root):
            raise FileToolError("PATH_OUTSIDE_PROJECT", "path escapes project root")
        if not self._inside(candidate, self.scope) or not self._inside(resolved, self.scope):
            raise FileToolError("PATH_OUTSIDE_SCOPE", "path escapes allowed scope")
        return candidate

    def relative(self, path: Path) -> str:
        return path.relative_to(self.root).as_posix()

    def _file(self, path: str) -> Path:
        target = self.resolve(path)
        if not target.exists():
            raise FileToolError("FILE_NOT_FOUND", "file does not exist")
        if not target.is_file():
            raise FileToolError("PATH_IS_NOT_FILE", "path is not a file")
        return target

    def _directory(self, path: str) -> Path:
        target = self.resolve(path)
        if not target.exists():
            raise FileToolError("DIRECTORY_NOT_FOUND", "directory does not exist")
        if not target.is_dir():
            raise FileToolError("PATH_IS_NOT_DIRECTORY", "path is not a directory")
        return target

    @staticmethod
    def _positive(value: int, name: str, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError) as exc:
            raise FileToolError("INVALID_INPUT", f"{name} must be an integer") from exc
        if not 1 <= number <= maximum:
            raise FileToolError("INVALID_INPUT", f"{name} must be between 1 and {maximum}")
        return number

    def read_file(
        self, path: str, offset: int = 0, length: int | None = None,
        mode: str = "text",
    ) -> dict:
        target = self._file(path)
        try:
            offset = int(offset)
        except (TypeError, ValueError) as exc:
            raise FileToolError("INVALID_INPUT", "offset must be an integer") from exc
        if offset < 0:
            raise FileToolError("INVALID_INPUT", "offset must be nonnegative")
        if length is not None:
            length = self._positive(length, "length", 2**63 - 1)
        if mode not in {"text", "binary"}:
            raise FileToolError("INVALID_INPUT", "mode must be text or binary")
        size = target.stat().st_size
        read_limit = min(length if length is not None else MAX_READ_BYTES, MAX_READ_BYTES)
        try:
            with target.open("rb") as stream:
                stream.seek(offset)
                chunk = stream.read(read_limit)
        except OSError as exc:
            raise FileToolError("FILE_READ_FAILED", str(exc)) from exc
        next_offset = offset + len(chunk)
        result = {
            "path": self.relative(target),
            "mode": mode,
            "offset": offset,
            "bytes_read": len(chunk),
            "total_bytes": size,
            "next_offset": next_offset if next_offset < size else None,
            "eof": next_offset >= size,
            "truncated": next_offset < size,
        }
        if mode == "binary":
            result["encoding"] = "base64"
            result["content"] = base64.b64encode(chunk).decode("ascii")
        else:
            codec = "utf-8-sig" if offset == 0 else "utf-8"
            try:
                result["content"] = chunk.decode(codec)
                result["decode_replaced"] = False
            except UnicodeDecodeError:
                result["content"] = chunk.decode(codec, errors="replace")
                result["decode_replaced"] = True
        return result

    def read_lines(self, path: str, start_line: int = 1, end_line: int | None = None) -> dict:
        target = self._file(path)
        start_line = self._positive(start_line, "start_line", 2**63 - 1)
        if end_line is not None:
            end_line = self._positive(end_line, "end_line", 2**63 - 1)
            if end_line < start_line:
                raise FileToolError("INVALID_INPUT", "end_line must be >= start_line")
        selected = []
        used_bytes = 0
        total_lines = 0
        limited = False
        try:
            with target.open("r", encoding="utf-8-sig", errors="replace") as stream:
                for total_lines, raw_line in enumerate(stream, start=1):
                    if total_lines < start_line or (end_line is not None and total_lines > end_line):
                        continue
                    if limited:
                        continue
                    if len(selected) >= MAX_LINES:
                        limited = True
                        continue
                    line = raw_line.rstrip("\n").rstrip("\r")
                    line_bytes = len(line.encode("utf-8"))
                    if line_bytes > MAX_READ_BYTES:
                        raise FileToolError("LINE_TOO_LONG", "line exceeds 1 MiB; use read_file with byte ranges")
                    if used_bytes + line_bytes > MAX_READ_BYTES:
                        limited = True
                        continue
                    selected.append({"line_number": total_lines, "text": line})
                    used_bytes += line_bytes
        except OSError as exc:
            raise FileToolError("FILE_READ_FAILED", str(exc)) from exc
        last_line = selected[-1]["line_number"] if selected else None
        has_more = limited or (
            last_line is not None
            and total_lines > last_line
            and (end_line is None or end_line > last_line)
        )
        return {
            "path": self.relative(target),
            "start_line": start_line,
            "end_line": end_line,
            "total_lines": total_lines,
            "returned_lines": len(selected),
            "lines": selected,
            "truncated": has_more,
            "next_line": last_line + 1 if has_more and last_line is not None else None,
        }

    def read_head(self, path: str, n: int = 10) -> dict:
        n = self._positive(n, "n", MAX_LINES)
        result = self.read_lines(path, 1, n)
        result["n"] = n
        return result

    def read_tail(self, path: str, n: int = 10) -> dict:
        target = self._file(path)
        n = self._positive(n, "n", MAX_LINES)
        tail = deque(maxlen=n)
        total_lines = 0
        try:
            with target.open("r", encoding="utf-8-sig", errors="replace") as stream:
                for total_lines, raw_line in enumerate(stream, start=1):
                    tail.append({"line_number": total_lines, "text": raw_line.rstrip("\n").rstrip("\r")})
        except OSError as exc:
            raise FileToolError("FILE_READ_FAILED", str(exc)) from exc
        lines = list(tail)
        while lines and sum(len(item["text"].encode("utf-8")) for item in lines) > MAX_READ_BYTES:
            lines.pop(0)
        return {
            "path": self.relative(target),
            "n": n,
            "total_lines": total_lines,
            "returned_lines": len(lines),
            "lines": lines,
            "truncated": len(lines) < min(n, total_lines),
        }

    def file_info(self, path: str) -> dict:
        target = self.resolve(path)
        try:
            info = target.lstat()
        except FileNotFoundError as exc:
            raise FileToolError("FILE_NOT_FOUND", "path does not exist") from exc
        except OSError as exc:
            raise FileToolError("FILE_INFO_FAILED", str(exc)) from exc
        kind = (
            "symlink" if stat.S_ISLNK(info.st_mode)
            else "directory" if stat.S_ISDIR(info.st_mode)
            else "file" if stat.S_ISREG(info.st_mode)
            else "other"
        )
        return {
            "path": self.relative(target),
            "type": kind,
            "size": info.st_size,
            "modified_at": datetime.fromtimestamp(info.st_mtime, timezone.utc).isoformat(),
            "permissions": oct(stat.S_IMODE(info.st_mode)),
            "symlink_target": os.readlink(target) if kind == "symlink" else None,
        }

    @staticmethod
    def _children(path: Path) -> list[Path]:
        try:
            return sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.casefold()))
        except OSError as exc:
            raise FileToolError("DIRECTORY_READ_FAILED", str(exc)) from exc

    def _entry(self, path: Path, base: Path) -> dict:
        kind = "symlink" if path.is_symlink() else "directory" if path.is_dir() else "file" if path.is_file() else "other"
        entry = {
            "name": path.name,
            "path": self.relative(path),
            "relative_path": path.relative_to(base).as_posix(),
            "type": kind,
        }
        if kind == "file":
            try:
                entry["size"] = path.stat().st_size
            except OSError:
                entry["size"] = None
        return entry

    def list_directory(
        self, path: str = ".", recursive: bool = False,
        pattern: str = "*", max_entries: int = MAX_ENTRIES,
    ) -> dict:
        base = self._directory(path)
        max_entries = self._positive(max_entries, "max_entries", MAX_ENTRIES)
        if not pattern:
            pattern = "*"
        entries = []
        stack = [base]
        truncated = False
        while stack:
            folder = stack.pop()
            children = self._children(folder)
            for child in children:
                relative = child.relative_to(base).as_posix()
                if fnmatch.fnmatch(child.name, pattern) or fnmatch.fnmatch(relative, pattern):
                    if len(entries) >= max_entries:
                        truncated = True
                        break
                    entries.append(self._entry(child, base))
                if recursive and child.is_dir() and not child.is_symlink():
                    stack.append(child)
            if truncated:
                break
        return {
            "path": self.relative(base),
            "recursive": recursive,
            "pattern": pattern,
            "entries": entries,
            "count": len(entries),
            "truncated": truncated,
        }

    def tree(self, path: str = ".", depth: int = 2, max_entries: int = MAX_ENTRIES) -> dict:
        base = self._directory(path)
        try:
            depth = int(depth)
        except (TypeError, ValueError) as exc:
            raise FileToolError("INVALID_INPUT", "depth must be an integer") from exc
        if not 0 <= depth <= 20:
            raise FileToolError("INVALID_INPUT", "depth must be between 0 and 20")
        max_entries = self._positive(max_entries, "max_entries", MAX_ENTRIES)
        count = 0
        truncated = False

        def build(item: Path, level: int) -> dict | None:
            nonlocal count, truncated
            if count >= max_entries:
                truncated = True
                return None
            count += 1
            node = self._entry(item, base) if item != base else {
                "name": base.name, "path": self.relative(base),
                "relative_path": ".", "type": "directory",
            }
            if item.is_dir() and not item.is_symlink():
                if level < depth:
                    node["children"] = []
                    for child in self._children(item):
                        child_node = build(child, level + 1)
                        if child_node is not None:
                            node["children"].append(child_node)
                        if truncated:
                            break
                else:
                    node["children"] = []
                    node["depth_limited"] = bool(self._children(item))
            return node

        root_node = build(base, 0)
        return {
            "path": self.relative(base),
            "depth": depth,
            "tree": root_node,
            "total_entries": count,
            "truncated": truncated,
        }

    def search_files(
        self, pattern: str, path: str = ".", regex: bool = False,
        context: int = 0, case_sensitive: bool = False,
        file_globs: list[str] | None = None,
        max_matches: int = 200, timeout_seconds: int = 10,
    ) -> dict:
        target = self.resolve(path)
        if not target.exists() or not (target.is_file() or target.is_dir()):
            raise FileToolError("PATH_NOT_FOUND", "search path does not exist")
        if not str(pattern or ""):
            raise FileToolError("INVALID_INPUT", "pattern is required")
        try:
            context = int(context)
        except (TypeError, ValueError) as exc:
            raise FileToolError("INVALID_INPUT", "context must be an integer") from exc
        if not 0 <= context <= 5:
            raise FileToolError("INVALID_INPUT", "context must be between 0 and 5")
        max_matches = self._positive(max_matches, "max_matches", 1000)
        timeout_seconds = self._positive(timeout_seconds, "timeout_seconds", 30)
        globs = file_globs or []
        if not isinstance(globs, list) or not all(isinstance(item, str) and item for item in globs):
            raise FileToolError("INVALID_INPUT", "file_globs must be a list of nonempty strings")
        executable = shutil.which("rg")
        if executable:
            command = [executable, "--json", "--color", "never", "-n", "--max-count", str(max_matches)]
            if not regex:
                command.append("-F")
            if not case_sensitive:
                command.append("-i")
            if context:
                command.extend(["-C", str(context)])
            for glob in globs:
                command.extend(["-g", glob])
            command.extend(["-e", pattern, "--", str(target)])
            parser = "rg"
        else:
            executable = shutil.which("grep")
            if not executable:
                raise FileToolError("COMMAND_NOT_FOUND", "rg or grep is required")
            command = [executable, "-H", "-n", "-I", "-m", str(max_matches)]
            if target.is_dir():
                command.append("-r")
            if not regex:
                command.append("-F")
            if not case_sensitive:
                command.append("-i")
            if context:
                command.extend(["-C", str(context)])
            for glob in globs:
                command.append(f"--include={glob}")
            command.extend(["-e", pattern, "--", str(target)])
            parser = "grep"
        try:
            completed = subprocess.run(
                command, cwd=str(target if target.is_dir() else target.parent),
                capture_output=True, timeout=timeout_seconds, check=False,
            )
        except subprocess.TimeoutExpired as exc:
            raise FileToolError("TIMEOUT", f"search timed out after {timeout_seconds}s") from exc
        except OSError as exc:
            raise FileToolError("SEARCH_FAILED", str(exc)) from exc
        stdout = completed.stdout[:MAX_SEARCH_OUTPUT_BYTES]
        output_truncated = len(completed.stdout) > MAX_SEARCH_OUTPUT_BYTES
        text = stdout.decode("utf-8", errors="replace")
        if parser == "rg":
            matches = self._parse_rg(text, context, max_matches)
        else:
            matches = self._parse_grep(text, context, max_matches)
        if completed.returncode not in (0, 1):
            raise FileToolError("SEARCH_FAILED", completed.stderr[:4000].decode("utf-8", errors="replace"))
        return {
            "path": self.relative(target),
            "pattern": pattern,
            "regex": regex,
            "context": context,
            "count": len(matches),
            "matches": matches,
            "truncated": output_truncated or len(matches) >= max_matches,
            "returncode": completed.returncode,
        }

    def _parse_rg(self, text: str, context: int, max_matches: int) -> list[dict]:
        matches = []
        lines_by_file = {}
        for raw in text.splitlines():
            try:
                event = json.loads(raw)
            except json.JSONDecodeError:
                continue
            if event.get("type") not in {"match", "context"}:
                continue
            data = event.get("data") or {}
            filename = (data.get("path") or {}).get("text")
            number = data.get("line_number")
            if not filename or not isinstance(number, int):
                continue
            file_lines = lines_by_file.setdefault(filename, {})
            line_text = (data.get("lines") or {}).get("text", "").rstrip("\r\n")
            file_lines[number] = line_text
            if event["type"] == "match" and len(matches) < max_matches:
                matches.append({
                    "file_path": self.relative(Path(filename)),
                    "line_number": number,
                    "line": line_text,
                    "submatches": [
                        {"start": part.get("start"), "end": part.get("end")}
                        for part in data.get("submatches") or []
                    ],
                    "_filename": filename,
                })
        for match in matches:
            filename = match.pop("_filename")
            number = match["line_number"]
            nearby = lines_by_file.get(filename, {})
            match["context_before"] = [
                {"line_number": line, "text": nearby[line]}
                for line in range(number - context, number) if line in nearby
            ]
            match["context_after"] = [
                {"line_number": line, "text": nearby[line]}
                for line in range(number + 1, number + context + 1) if line in nearby
            ]
        return matches

    def _parse_grep(self, text: str, context: int, max_matches: int) -> list[dict]:
        matches = []
        lines_by_file = {}
        for raw in text.splitlines():
            found = re.match(r"^(.*)([:-])(\d+)\2(.*)$", raw)
            if not found:
                continue
            filename, separator, number_text, line = found.groups()
            number = int(number_text)
            lines_by_file.setdefault(filename, {})[number] = line
            if separator == ":" and len(matches) < max_matches:
                matches.append({
                    "file_path": self.relative(Path(filename)),
                    "line_number": number,
                    "line": line,
                    "submatches": [],
                    "_filename": filename,
                })
        for match in matches:
            filename = match.pop("_filename")
            number = match["line_number"]
            nearby = lines_by_file.get(filename, {})
            match["context_before"] = [
                {"line_number": line, "text": nearby[line]}
                for line in range(number - context, number) if line in nearby
            ]
            match["context_after"] = [
                {"line_number": line, "text": nearby[line]}
                for line in range(number + 1, number + context + 1) if line in nearby
            ]
        return matches
