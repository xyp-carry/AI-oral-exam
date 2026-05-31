import ast
import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool
from AIOralExamSystem.Tool.codeanalysis.python import generate_project_maps, query_user_symbols


class CodeAnalysisToolInput(BaseModel):
    mode: str = Field(description="Analysis mode: ast or lsp.")
    git_local_address: Optional[str] = Field(
        default=None,
        description=(
            "Git repository address or local repository cache identifier for AST mode. "
            "It is normalized before locating Gitrepositorys/{user_uuid}/{normalized_git_local_address}/code. "
            "May be omitted only when the tool was registered with a bound git_local_address."
        ),
    )
    file_path: Optional[str] = Field(
        default=None,
        description=(
            "AST mode requires one source file path relative to the selected repository code root. "
            "LSP mode uses the current user's stored code repositories."
        ),
    )
    lsp_action: Optional[str] = Field(default=None, description="LSP action: project_map or symbols. Defaults to project_map.")
    language: Optional[str] = Field(default=None, description="Optional language name. Defaults to path detection.")
    timeout: int = Field(default=20, description="LSP server timeout seconds.")


CodeAnalysisDescription = (
    "Analyze code in two modes. ast parses one Python file from a specific current-user repository cache "
    "into AI-oriented compact S-expression layers. In ast mode, `file_path` must be relative to "
    "Gitrepositorys/{user_uuid}/{normalized_git_local_address}/code. `git_local_address` is normalized "
    "the same way as GitRepositoryTool cache names and may be omitted only when the tool was registered "
    "with a bound git_local_address. lsp analyzes code repositories stored for the initialized user; "
    "project_map writes and returns Markdown maps, while symbols returns structured Pyright symbol results."
)


class CodeAnalysisTool(BaseTool):
    """Code analysis tool with single-file AST and project-level Python LSP support."""

    def __init__(self, name: str, user_uuid: str, git_local_address: Optional[str] = None):
        super().__init__(name)
        # 分析始终绑定创建工具时的当前用户，避免访问其他用户的仓库缓存。
        self.user_uuid = self._require_text(user_uuid, "user_uuid")
        self.git_local_address = self._normalize_optional_text(git_local_address)
        self._active_git_local_address = None
        self.description = CodeAnalysisDescription

    async def _run(
        self,
        mode: str,
        git_local_address: Optional[str] = None,
        file_path: Optional[str] = None,
        language: Optional[str] = None,
        timeout: int = 20,
        lsp_action: Optional[str] = None,
    ) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.analyze_code,
                mode,
                git_local_address,
                file_path,
                language,
                timeout,
                lsp_action,
            )

    def analyze_code(
        self,
        mode: str,
        git_local_address: Optional[str] = None,
        file_path: Optional[str] = None,
        language: Optional[str] = None,
        timeout: int = 20,
        lsp_action: Optional[str] = None,
    ) -> str:
        # AST 使用单个文件路径；LSP 仅使用当前用户在固定目录下已保存的代码仓库。
        logs = []
        try:
            active_git_address = self._normalize_optional_text(git_local_address) or self.git_local_address
            target_path = self.resolve_code_path(active_git_address, file_path) if file_path else None
            language = language or (self.detect_language(target_path) if target_path else "python")
            self._active_git_local_address = active_git_address
            logs.append(
                {
                    "step": "start",
                    "status": "success",
                    "mode": mode,
                    "file_path": str(target_path) if target_path else None,
                    "relative_file_path": self._relative_file_path(target_path) if target_path else None,
                    "user_uuid": self.user_uuid,
                    "git_local_address": active_git_address,
                    "safe_git_local_address": self._safe_path_part(active_git_address) if active_git_address else None,
                    "code_root": str(self.code_root(active_git_address)) if active_git_address else None,
                    "lsp_action": lsp_action if mode == "lsp" else None,
                    "language": language,
                }
            )

            if mode == "ast":
                if target_path is None or not target_path.is_file():
                    raise ValueError("AST mode requires file_path to be a file.")
                result = self.parse_ast(target_path, language)
            elif mode == "lsp":
                result = self.query_lsp(language, lsp_action or "project_map", timeout)
            else:
                raise ValueError("mode must be ast or lsp.")

            result["logs"] = logs + result.get("logs", [])
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            logs.append({"step": "error", "status": "error", "message": str(exc)})
            return json.dumps({"mode": mode, "error": str(exc), "logs": logs}, ensure_ascii=False)

    def parse_ast(self, file_path: Path, language: str) -> dict:
        # 只解析单个 Python 文件，返回供 AI 快速导航和核查细节的两层结构。
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
            "git_local_address": self._active_git_local_address,
            "safe_git_local_address": (
                self._safe_path_part(self._active_git_local_address)
                if self._active_git_local_address
                else None
            ),
            "code_root": str(self._owning_code_root(file_path)),
            "symbols": sorted(symbols, key=lambda item: (item["line"], item["name"])),
            "tree_format": "s_expression_compact",
            "summary_sexpr": self.build_ast_summary_sexpr(tree),
            "ast_sexpr": self.ast_to_sexpr(tree),
        }

    def ast_to_sexpr(self, value) -> str:
        # 将真实 AST 递归转为紧凑 S-表达式；语句节点保留位置，空字段不输出。
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
        # 摘要层仅输出模块入口结构和调用概览，让 AI 先定位再阅读完整 AST。
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
                sections.append(
                    f"(Import :names {self.ast_to_sexpr([item.name for item in node.names])})"
                )
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

    def query_lsp(self, language: str, lsp_action: str, timeout: int) -> dict:
        # 由 LSP 子操作选择生成地图或返回符号结果，目录始终按当前用户固定定位。
        if language != "python":
            raise ValueError("LSP mode currently only supports Python via pyright-langserver.")
        action = str(lsp_action).strip().lower()
        if action == "project_map":
            return generate_project_maps(user_uuid=self.user_uuid, timeout=timeout)
        if action == "symbols":
            return query_user_symbols(user_uuid=self.user_uuid, timeout=timeout)
        raise ValueError("lsp_action must be project_map or symbols.")

    def detect_language(self, target_path: Path) -> str:
        # 根据路径判断语言；目录模式目前默认走 Python provider，后续可扩展其他语言。
        if target_path.is_dir():
            return "python"
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
            / self._safe_path_part(git_local_address)
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
