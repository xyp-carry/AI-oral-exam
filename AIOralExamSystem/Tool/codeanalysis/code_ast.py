import ast
import asyncio
import json
import re
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any, Optional

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool


class CodeAstToolInput(BaseModel):
    file_path: str = Field(
        description="Python, Rust, or C file path relative to the selected repository code root.",
    )
    language: Optional[str] = Field(
        default=None,
        description="Optional language name. Supported values: python, rust, c.",
    )


CodeAstDescription = (
    "Parse one Python, Rust, or C file from a specific current-user repository cache into compact AST layers. "
    "`file_path` must be relative to the system-bound repository code root. "
    "The repository address is bound by the system when this tool is initialized and cannot be overridden "
    "by tool input. Use this tool to understand a "
    "file's symbols, module organization, and call skeleton; use codeReader for exact source snippets."
)


class CodeAstTool(BaseTool):
    """Single-file, user-scoped AST analysis tool."""

    def __init__(self, name: str, user_uuid: str, git_local_address: Optional[str] = None):
        super().__init__(name)
        self.user_uuid = self._require_text(user_uuid, "user_uuid")
        self.git_local_address = self._normalize_optional_text(git_local_address)
        self.description = CodeAstDescription

    async def _run(
        self,
        file_path: str,
        language: Optional[str] = None,
    ) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.analyze_ast,
                file_path,
                language,
            )

    def analyze_ast(
        self,
        file_path: str,
        language: Optional[str] = None,
    ) -> str:
        logs = []
        active_git_address = None
        try:
            active_git_address = self._require_text(self.git_local_address, "git_local_address")
            code_root = self.code_root(active_git_address)
            if not code_root.is_dir():
                raise ValueError(f"Code root does not exist: {code_root}")
            target_path = self._resolve_relative_path(code_root, file_path)
            language = self.normalize_language(language or self.detect_language(target_path))
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
                    "code_root": str(code_root),
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
        language = self.normalize_language(language)
        if language == "python":
            return self.parse_python_ast(file_path, language, git_local_address)
        if language in {"rust", "c"}:
            return self.parse_tree_sitter_ast(file_path, language, git_local_address)
        raise ValueError("AST mode supports python, rust, and c files.")

    def parse_python_ast(self, file_path: Path, language: str, git_local_address: str) -> dict:
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
            "summary_sexpr": self.build_python_ast_summary_sexpr(tree),
            "ast_sexpr": self.python_ast_to_sexpr(tree),
        }

    def python_ast_to_sexpr(self, value) -> str:
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
                fields.append(f":{field_name} {self.python_ast_to_sexpr(field_value)}")
            return f"({' '.join(fields)})"
        if isinstance(value, list):
            return f"({' '.join(self.python_ast_to_sexpr(item) for item in value)})"
        try:
            return json.dumps(value, ensure_ascii=False)
        except TypeError:
            return json.dumps(repr(value), ensure_ascii=False)

    def build_python_ast_summary_sexpr(self, tree: ast.Module) -> str:
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
                f":params {self.python_ast_to_sexpr(parameters)}",
            ]
            if calls:
                parts.append(f":calls {self.python_ast_to_sexpr(calls)}")
            return f"({' '.join(parts)})"

        sections = []
        for node in tree.body:
            if isinstance(node, ast.Import):
                sections.append(f"(Import :names {self.python_ast_to_sexpr([item.name for item in node.names])})")
            elif isinstance(node, ast.ImportFrom):
                sections.append(
                    "(ImportFrom "
                    f":module {self.python_ast_to_sexpr(node.module or '')} "
                    f":names {self.python_ast_to_sexpr([item.name for item in node.names])})"
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

    def parse_tree_sitter_ast(self, file_path: Path, language: str, git_local_address: str) -> dict:
        parser = self.build_tree_sitter_parser(language)
        source_bytes = file_path.read_bytes()
        tree = parser.parse(source_bytes)
        symbols = self.extract_tree_sitter_symbols(tree.root_node, source_bytes, language)
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
            "summary_sexpr": self.build_tree_sitter_summary_sexpr(tree.root_node, source_bytes, language),
            "ast_sexpr": self.tree_sitter_to_sexpr(tree.root_node, source_bytes),
        }

    def build_tree_sitter_parser(self, language: str):
        try:
            from tree_sitter import Language, Parser
        except ImportError as exc:
            raise ValueError("Rust/C AST parsing requires the tree-sitter package.") from exc

        grammar_module_name = {
            "rust": "tree_sitter_rust",
            "c": "tree_sitter_c",
        }[language]
        try:
            grammar_module = __import__(grammar_module_name)
        except ImportError as exc:
            raise ValueError(f"{language} AST parsing requires the {grammar_module_name} package.") from exc

        raw_language = grammar_module.language()
        tree_sitter_language = raw_language if isinstance(raw_language, Language) else Language(raw_language)
        parser = Parser()
        if hasattr(parser, "set_language"):
            parser.set_language(tree_sitter_language)
        else:
            parser.language = tree_sitter_language
        return parser

    def extract_tree_sitter_symbols(self, root_node: Any, source_bytes: bytes, language: str) -> list[dict[str, Any]]:
        symbol_types = {
            "rust": {
                "function_item",
                "struct_item",
                "enum_item",
                "trait_item",
                "impl_item",
                "mod_item",
                "macro_definition",
                "use_declaration",
            },
            "c": {
                "function_definition",
                "declaration",
                "struct_specifier",
                "enum_specifier",
                "typedef_declaration",
                "preproc_include",
            },
        }[language]
        symbols = []
        for node in self.walk_tree_sitter(root_node):
            if node.type not in symbol_types:
                continue
            if language == "c" and node.type == "declaration" and not self.find_descendant(node, {"function_declarator"}):
                continue
            name = self.tree_sitter_symbol_name(node, source_bytes, language)
            symbols.append(
                {
                    "name": name,
                    "type": node.type,
                    "line": self.node_start_line(node),
                    "end_line": self.node_end_line(node),
                }
            )
        return symbols

    def build_tree_sitter_summary_sexpr(self, root_node: Any, source_bytes: bytes, language: str) -> str:
        symbol_parts = []
        for symbol in self.extract_tree_sitter_symbols(root_node, source_bytes, language):
            symbol_parts.append(
                "("
                f"{symbol['type']} "
                f":name {json.dumps(symbol['name'], ensure_ascii=False)} "
                f":line {symbol['line']} "
                f":end_line {symbol['end_line']}"
                ")"
            )
        calls = []
        for node in self.walk_tree_sitter(root_node):
            if node.type != "call_expression":
                continue
            call_name = self.tree_sitter_call_name(node, source_bytes)
            if call_name and call_name not in calls:
                calls.append(call_name)
        call_part = ""
        if calls:
            call_part = f" :calls {self.json_list_to_sexpr(calls)}"
        return f"(ModuleSummary :language {json.dumps(language, ensure_ascii=False)} :symbols ({' '.join(symbol_parts)}){call_part})"

    def tree_sitter_to_sexpr(
        self,
        node: Any,
        source_bytes: bytes,
        depth: int = 0,
        state: Optional[dict[str, Any]] = None,
    ) -> str:
        if state is None:
            state = {"nodes": 0, "truncated": False}
        if depth > 8 or state["nodes"] >= 3000:
            state["truncated"] = True
            return "(...)"
        state["nodes"] += 1
        fields = [node.type, f":loc {json.dumps(self.node_location(node), ensure_ascii=False)}"]
        named_children = list(getattr(node, "named_children", []) or [])
        if named_children:
            children = " ".join(
                self.tree_sitter_to_sexpr(child, source_bytes, depth + 1, state)
                for child in named_children
            )
            fields.append(f":children ({children})")
        else:
            text = self.node_text(node, source_bytes).strip()
            if text:
                fields.append(f":text {json.dumps(self.truncate_text(text), ensure_ascii=False)}")
        if depth == 0 and state["truncated"]:
            fields.append(":truncated true")
        return f"({' '.join(fields)})"

    def walk_tree_sitter(self, node: Any):
        yield node
        for child in getattr(node, "named_children", []) or []:
            yield from self.walk_tree_sitter(child)

    def tree_sitter_symbol_name(self, node: Any, source_bytes: bytes, language: str) -> str:
        named = self.child_text_by_field(node, "name", source_bytes)
        if named:
            return named
        if node.type == "impl_item":
            return self.truncate_text(self.first_line_text(node, source_bytes).replace("{", "").strip())
        if node.type in {"use_declaration", "preproc_include"}:
            return self.truncate_text(self.first_line_text(node, source_bytes).strip())
        if language == "c":
            declarator = node.child_by_field_name("declarator")
            search_root = declarator or node
            identifiers = [
                self.node_text(item, source_bytes)
                for item in self.walk_tree_sitter(search_root)
                if item.type in {"identifier", "type_identifier"}
            ]
            if identifiers:
                return identifiers[-1]
        identifier = self.find_descendant(node, {"identifier", "type_identifier"})
        if identifier is not None:
            return self.node_text(identifier, source_bytes)
        return self.truncate_text(self.first_line_text(node, source_bytes).strip()) or node.type

    def tree_sitter_call_name(self, node: Any, source_bytes: bytes) -> str:
        function_node = node.child_by_field_name("function")
        if function_node is None:
            function_node = self.find_descendant(node, {"identifier", "field_identifier", "scoped_identifier"})
        if function_node is None:
            return ""
        return self.truncate_text(self.node_text(function_node, source_bytes).strip())

    def child_text_by_field(self, node: Any, field_name: str, source_bytes: bytes) -> str:
        child = node.child_by_field_name(field_name)
        return self.node_text(child, source_bytes).strip() if child is not None else ""

    def find_descendant(self, node: Any, node_types: set[str]) -> Any:
        for item in self.walk_tree_sitter(node):
            if item is not node and item.type in node_types:
                return item
        return None

    def node_text(self, node: Any, source_bytes: bytes) -> str:
        return source_bytes[node.start_byte : node.end_byte].decode("utf-8", errors="replace")

    def first_line_text(self, node: Any, source_bytes: bytes) -> str:
        text = self.node_text(node, source_bytes)
        return text.splitlines()[0] if text else ""

    def node_location(self, node: Any) -> str:
        start_line, start_col = self.point_parts(node.start_point)
        end_line, end_col = self.point_parts(node.end_point)
        return f"{start_line + 1}:{start_col}-{end_line + 1}:{end_col}"

    def node_start_line(self, node: Any) -> int:
        return self.point_parts(node.start_point)[0] + 1

    def node_end_line(self, node: Any) -> int:
        return self.point_parts(node.end_point)[0] + 1

    def point_parts(self, point: Any) -> tuple[int, int]:
        row = getattr(point, "row", None)
        column = getattr(point, "column", None)
        if row is not None and column is not None:
            return row, column
        return point[0], point[1]

    def json_list_to_sexpr(self, values: list[str]) -> str:
        return f"({' '.join(json.dumps(item, ensure_ascii=False) for item in values)})"

    def truncate_text(self, text: str, limit: int = 120) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        if len(normalized) <= limit:
            return normalized
        return normalized[: limit - 3] + "..."

    def normalize_language(self, language: str) -> str:
        aliases = {
            "py": "python",
            "python3": "python",
            "rs": "rust",
            "rustlang": "rust",
            "c99": "c",
            "c11": "c",
            "h": "c",
        }
        normalized = self._require_text(language, "language").lower().strip()
        return aliases.get(normalized, normalized)

    def detect_language(self, target_path: Path) -> str:
        mapping = {
            ".py": "python",
            ".pyi": "python",
            ".rs": "rust",
            ".c": "c",
            ".h": "c",
            ".js": "javascript",
            ".jsx": "javascript",
            ".ts": "typescript",
            ".tsx": "typescript",
        }
        suffix = target_path.suffix.lower()
        return mapping.get(suffix, suffix.lstrip(".") or "unknown")

    def resolve_code_path(self, file_path: Optional[str]) -> Path:
        active_git_address = self._require_text(self.git_local_address, "git_local_address")
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
