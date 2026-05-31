import ast
import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool


class CodeAstToolInput(BaseModel):
    git_local_address: Optional[str] = Field(
        default=None,
        description=(
            "Git repository address or local repository cache identifier. It is normalized before "
            "locating Gitrepositorys/{user_uuid}/{normalized_git_local_address}/code. "
            "May be omitted only when the tool was registered with a bound git_local_address."
        ),
    )
    file_path: str = Field(
        description="Python file path relative to the selected repository code root.",
    )
    language: Optional[str] = Field(
        default=None,
        description="Optional language name. Only python is currently supported.",
    )


CodeAstDescription = (
    "Parse one Python file from a specific current-user repository cache into compact AST layers. "
    "`file_path` must be relative to Gitrepositorys/{user_uuid}/{normalized_git_local_address}/code. "
    "`git_local_address` is normalized the same way as GitRepositoryTool cache names and may be omitted "
    "only when the tool was registered with a bound git_local_address. Use this tool to understand a "
    "file's classes, functions, module organization, and call skeleton; use codeReader for exact source snippets."
)


class CodeAstTool(BaseTool):
    """Single-file, user-scoped Python AST analysis tool."""

    def __init__(self, name: str, user_uuid: str, git_local_address: Optional[str] = None):
        super().__init__(name)
        self.user_uuid = self._require_text(user_uuid, "user_uuid")
        self.git_local_address = self._normalize_optional_text(git_local_address)
        self.description = CodeAstDescription

    async def _run(
        self,
        file_path: str,
        git_local_address: Optional[str] = None,
        language: Optional[str] = None,
    ) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.analyze_ast,
                file_path,
                git_local_address,
                language,
            )

    def analyze_ast(
        self,
        file_path: str,
        git_local_address: Optional[str] = None,
        language: Optional[str] = None,
    ) -> str:
        logs = []
        active_git_address = self._normalize_optional_text(git_local_address) or self.git_local_address
        try:
            target_path = self.resolve_code_path(active_git_address, file_path)
            language = language or self.detect_language(target_path)
            logs.append(
                {
                    "step": "start",
                    "status": "success",
                    "mode": "ast",
                    "file_path": str(target_path),
                    "relative_file_path": self._relative_file_path(target_path),
                    "user_uuid": self.user_uuid,
                    "git_local_address": active_git_address,
                    "safe_git_local_address": self._safe_path_part(active_git_address),
                    "code_root": str(self.code_root(active_git_address)),
                    "language": language,
                }
            )
            result = self.parse_ast(target_path, language, active_git_address)
            result["logs"] = logs + result.get("logs", [])
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            logs.append({"step": "error", "status": "error", "message": str(exc)})
            return json.dumps({"mode": "ast", "error": str(exc), "logs": logs}, ensure_ascii=False)

    def parse_ast(self, file_path: Path, language: str, git_local_address: str) -> dict:
        if language != "python":
            raise ValueError("AST mode currently only supports Python files.")
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
        symbols = []
        for node in ast.walk(tree):
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                symbols.append(
                    {
                        "name": node.name,
                        "type": node.__class__.__name__,
                        "line": node.lineno,
                        "end_line": getattr(node, "end_lineno", None),
                    }
                )
        return {
            "mode": "ast",
            "language": language,
            "file_path": self._relative_file_path(file_path),
            "absolute_file_path": str(file_path),
            "git_local_address": git_local_address,
            "safe_git_local_address": self._safe_path_part(git_local_address),
            "code_root": str(self._owning_code_root(file_path)),
            "symbols": sorted(symbols, key=lambda item: (item["line"], item["name"])),
            "tree_format": "s_expression_compact",
            "summary_sexpr": self.build_ast_summary_sexpr(tree),
            "ast_sexpr": self.ast_to_sexpr(tree),
        }

    def ast_to_sexpr(self, value) -> str:
        if isinstance(value, ast.AST):
            fields = [value.__class__.__name__]
            if isinstance(value, ast.stmt) and hasattr(value, "lineno"):
                end_line = getattr(value, "end_lineno", value.lineno)
                end_col = getattr(value, "end_col_offset", value.col_offset)
                location = f"{value.lineno}:{value.col_offset}-{end_line}:{end_col}"
                fields.append(f":loc {json.dumps(location, ensure_ascii=False)}")
            for field_name, field_value in ast.iter_fields(value):
                if field_value is None or field_value == []:
                    continue
                fields.append(f":{field_name} {self.ast_to_sexpr(field_value)}")
            return f"({' '.join(fields)})"
        if isinstance(value, list):
            return f"({' '.join(self.ast_to_sexpr(item) for item in value)})"
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return json.dumps(repr(value), ensure_ascii=False)

    def build_ast_summary_sexpr(self, tree: ast.Module) -> str:
        def function_summary(node) -> str:
            parameters = [item.arg for item in node.args.posonlyargs + node.args.args]
            if node.args.vararg:
                parameters.append(f"*{node.args.vararg.arg}")
            parameters.extend(item.arg for item in node.args.kwonlyargs)
            if node.args.kwarg:
                parameters.append(f"**{node.args.kwarg.arg}")
            calls = []
            for item in ast.walk(node):
                if not isinstance(item, ast.Call):
                    continue
                call_name = ast.unparse(item.func)
                if call_name not in calls:
                    calls.append(call_name)
            parts = [
                node.__class__.__name__,
                f":name {json.dumps(node.name, ensure_ascii=False)}",
                f":line {node.lineno}",
                f":params {self.ast_to_sexpr(parameters)}",
            ]
            if calls:
                parts.append(f":calls {self.ast_to_sexpr(calls)}")
            return f"({' '.join(parts)})"

        sections = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                sections.append(f"(Import :names {self.ast_to_sexpr([item.name for item in node.names])})")
            elif isinstance(node, ast.ImportFrom):
                sections.append(
                    "(ImportFrom "
                    f":module {self.ast_to_sexpr(node.module or '')} "
                    f":names {self.ast_to_sexpr([item.name for item in node.names])})"
                )
            elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                sections.append(function_summary(node))
            elif isinstance(node, ast.ClassDef):
                methods = [
                    function_summary(item)
                    for item in node.body
                    if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                ]
                class_parts = [
                    "ClassDef",
                    f":name {json.dumps(node.name, ensure_ascii=False)}",
                    f":line {node.lineno}",
                ]
                if methods:
                    class_parts.append(f":methods ({' '.join(methods)})")
                sections.append(f"({' '.join(class_parts)})")
        return f"(ModuleSummary {' '.join(sections)})"

    def detect_language(self, target_path: Path) -> str:
        mapping = {
            ".py": "python",
            ".pyi": "python",
            ".rs": "rust",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
        }
        suffix = target_path.suffix.lower()
        return mapping.get(suffix, suffix.lstrip(".") or "unknown")

    def resolve_code_path(self, git_local_address: Optional[str], file_path: Optional[str]) -> Path:
        active_git_address = self._require_text(git_local_address, "git_local_address")
        relative_path = self._require_text(file_path, "file_path")
        code_root = self.code_root(active_git_address)
        if not code_root.is_dir():
            raise ValueError(f"Code root does not exist: {code_root}")
        return self._resolve_relative_path(code_root, relative_path)

    def code_root(self, git_local_address: str) -> Path:
        return (
            self._default_storage_root()
            / "Gitrepositorys"
            / self._safe_path_part(self.user_uuid)
            / self._safe_repository_path(git_local_address)
            / "code"
        )

    def _resolve_relative_path(self, code_root: Path, relative_path: str) -> Path:
        clean_path = relative_path.replace("\\", "/").strip()
        if clean_path.startswith("/") or re.match(r"^[A-Za-z]:/", clean_path):
            raise ValueError(f"file_path must be relative to the repository code root: {relative_path}")
        candidate = (code_root / clean_path).resolve()
        code_root_real = code_root.resolve()
        if candidate != code_root_real and code_root_real not in candidate.parents:
            raise ValueError(f"Path escapes repository code root: {relative_path}")
        if not candidate.is_file():
            raise ValueError(f"AST mode requires file_path to be a file: {relative_path}")
        return candidate

    def _owning_code_root(self, file_path: Path) -> Path:
        resolved = file_path.resolve()
        for parent in resolved.parents:
            if parent.name == "code" and parent.parent.parent.name == self._safe_path_part(self.user_uuid):
                return parent
        return resolved.parent

    def _relative_file_path(self, file_path: Path) -> str:
        code_root = self._owning_code_root(file_path)
        try:
            return file_path.resolve().relative_to(code_root.resolve()).as_posix()
        except ValueError:
            return str(file_path)

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

    def get_description(self) -> str:
        return self.description
