import ast
import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool
from AIOralExamSystem.Tool.codeanalysis.python import query_pyright


class CodeAnalysisToolInput(BaseModel):
    mode: str = Field(description="Analysis mode: ast or lsp.")
    file_path: str = Field(description="AST mode uses a file path. LSP mode uses a project directory path.")
    language: Optional[str] = Field(default=None, description="Optional language name. Defaults to path detection.")
    timeout: int = Field(default=20, description="LSP server timeout seconds.")


CodeAnalysisDescription = (
    "Analyze code in two modes. ast parses one Python file only. lsp treats file_path as the project root and "
    "calls the language server provider under codeanalysis/{language}; currently Python uses pyright-langserver."
)


class CodeAnalysisTool(BaseTool):
    """Code analysis tool with single-file AST and project-level Python LSP support."""

    def __init__(self, name: str):
        super().__init__(name)
        self.description = CodeAnalysisDescription

    async def _run(
        self,
        mode: str,
        file_path: str,
        language: Optional[str] = None,
        timeout: int = 20,
    ) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.analyze_code,
                mode,
                file_path,
                language,
                timeout,
            )

    def analyze_code(
        self,
        mode: str,
        file_path: str,
        language: Optional[str] = None,
        timeout: int = 20,
    ) -> str:
        # 根据路径类型和模式分发：AST 只接受文件，LSP 只接受项目目录。
        logs = []
        try:
            target_path = Path(file_path).expanduser().resolve()
            language = language or self.detect_language(target_path)
            logs.append(
                {
                    "step": "start",
                    "status": "success",
                    "mode": mode,
                    "file_path": str(target_path),
                    "language": language,
                }
            )

            if mode == "ast":
                if not target_path.is_file():
                    raise ValueError("AST mode requires file_path to be a file.")
                result = self.parse_ast(target_path, language)
            elif mode == "lsp":
                if not target_path.is_dir():
                    raise ValueError("LSP mode requires file_path to be a project directory.")
                result = self.query_lsp(target_path, language, timeout)
            else:
                raise ValueError("mode must be ast or lsp.")

            result["logs"] = logs + result.get("logs", [])
            return json.dumps(result, ensure_ascii=False)
        except Exception as exc:
            logs.append({"step": "error", "status": "error", "message": str(exc)})
            return json.dumps({"mode": mode, "error": str(exc), "logs": logs}, ensure_ascii=False)

    def parse_ast(self, file_path: Path, language: str) -> dict:
        # 只解析单个 Python 文件，提取类和函数符号，不做跨文件语义分析。
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
            "file_path": str(file_path),
            "symbols": sorted(symbols, key=lambda item: (item["line"], item["name"])),
            "tree": ast.dump(tree, include_attributes=True),
        }

    def query_lsp(self, project_root: Path, language: str, timeout: int) -> dict:
        # 调用对应语言目录下的 LSP provider；当前只支持 Python 的 pyright-langserver。
        if language != "python":
            raise ValueError("LSP mode currently only supports Python via pyright-langserver.")
        return query_pyright(project_root=project_root, timeout=timeout)

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

    def get_description(self) -> str:
        return self.description
