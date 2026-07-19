import json
import re
from pathlib import Path
from typing import Literal, Optional

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from AIOralExamSystem.Agent.base_Agent import BaseAgent
from AIOralExamSystem.Tool.codeanalysis import (
    CodeAstDescription,
    CodeAstTool,
    CodeLspDescription,
    CodeLspTool,
    CodeReaderDescription,
    CodeReaderTool,
)


class CodeAstToolInput(BaseModel):
    file_path: str = Field(description="Source file path relative to the bound repository code root.")
    language: Optional[str] = Field(default=None, description="Optional language name: python, rust, or c.")


class CodeReaderToolInput(BaseModel):
    file_path: str = Field(description="Source file path relative to the bound repository code root.")
    start_line: int = Field(description="Inclusive 1-based start line.")
    end_line: int = Field(description="Inclusive 1-based end line.")


class CodeLspToolInput(BaseModel):
    action: Literal["project_map", "symbols"] = Field(
        default="symbols",
        description="Python-only LSP action. Use symbols to locate Python symbols or project_map for a Python project overview.",
    )
    query: Optional[str] = Field(default=None, description="Python symbol search query. Only used when action is symbols.")
    limit: int = Field(default=20, description="Maximum Python symbol matches, clamped by CodeLspTool.")
    timeout: int = Field(default=20, description="LSP timeout seconds.")


class CodeEvidence(BaseModel):
    file_path: str = ""
    start_line: Optional[int] = None
    end_line: Optional[int] = None
    reason: str = ""


class Coderreader(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        user_uuid: str,
        git_local_address: str,
        thinking: bool = False,
        response_format: bool = True,
        temperature: float = 0,
        show_tool_io: bool = False,
    ):
        self.user_uuid = self._require_text(user_uuid, "user_uuid")
        self.git_local_address = self._require_text(git_local_address, "git_local_address")
        self._active_language = None
        self._active_file_path = None
        super().__init__(
            "Coderreader",
            model_settings,
            thinking,
            response_format,
            temperature,
            show_tool_io=show_tool_io,
        )
        self.system_prompt = self.build_system_prompt()

    def build_system_prompt(self) -> str:
        return """
        ## Role
        You are a code-reading agent. You answer the user's code question by using the code analysis tools only.

        ## Tools
        - codeAst: works for Python, Rust, and C. Use it for file structure, symbols, and call skeletons.
        - codeReader: works for Python, Rust, and C. Use it for exact source-code evidence by line range.
        - codeLsp: Python-only. Use it only when target_language is python. For Rust and C, do not use LSP.

        ## Rules
        - The repository address is already bound by the system. Tool inputs must use repository-relative file paths.
        - Never invent implementation details. If exact behavior matters, call codeReader and cite lines.
        - AST summaries are enough for high-level structure, but not enough for exact implementation claims.
        - For Rust and C, rely on codeAst and codeReader. If codeLsp returns LSP_ONLY_SUPPORTS_PYTHON, continue without it.
        - If the provided file or tool evidence is insufficient, say what is missing in missing_information.

        ## Output
        Strictly output JSON:
        {
          "answer": "complete answer to the user's request",
          "evidence": [
            {
              "file_path": "relative/path/to/file",
              "start_line": 1,
              "end_line": 20,
              "reason": "why this range supports the answer"
            }
          ],
          "missing_information": []
        }
        """

    def get_tools(self):
        @tool(args_schema=CodeAstToolInput, description=CodeAstDescription)
        async def codeAst(file_path: str, language: Optional[str] = None) -> str:
            code_ast = CodeAstTool("code_ast", self.user_uuid, git_local_address=self.git_local_address)
            return await code_ast.execute(
                file_path=self.normalize_file_path(file_path),
                language=language or self._active_language,
            )

        @tool(args_schema=CodeReaderToolInput, description=CodeReaderDescription)
        async def codeReader(file_path: str, start_line: int, end_line: int) -> str:
            code_reader = CodeReaderTool("code_reader", self.user_uuid, git_local_address=self.git_local_address)
            return await code_reader.execute(
                file_path=self.normalize_file_path(file_path),
                start_line=start_line,
                end_line=end_line,
            )

        @tool(args_schema=CodeLspToolInput, description=CodeLspDescription + " This wrapper rejects non-Python tasks.")
        async def codeLsp(
            action: Literal["project_map", "symbols"] = "symbols",
            query: Optional[str] = None,
            limit: int = 20,
            timeout: int = 20,
        ) -> str:
            if self._active_language != "python":
                return json.dumps(
                    {
                        "ok": False,
                        "mode": "lsp",
                        "error_type": "LSP_ONLY_SUPPORTS_PYTHON",
                        "language": self._active_language,
                        "message": "codeLsp is only available for Python files. Use codeAst and codeReader for Rust/C.",
                    },
                    ensure_ascii=False,
                )
            code_lsp = CodeLspTool("code_lsp", self.user_uuid, git_local_address=self.git_local_address)
            return await code_lsp.execute(
                action=action,
                query=query,
                limit=limit,
                timeout=timeout,
            )

        return [codeAst, codeReader, codeLsp]

    async def execute(
        self,
        file_path: str,
        prompt: str,
        language: Optional[str] = None,
        start_line: Optional[int] = None,
        end_line: Optional[int] = None,
    ) -> dict:
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        normalized_file_path = self.normalize_file_path(file_path)
        self._active_file_path = normalized_file_path
        self._active_language = self.detect_language(normalized_file_path, language)

        try:
            ast_result = await self.load_ast_context(normalized_file_path, self._active_language)
            read_result = None
            if start_line is not None or end_line is not None:
                if start_line is None or end_line is None:
                    raise ValueError("start_line and end_line must be provided together.")
                read_result = await self.load_source_context(normalized_file_path, start_line, end_line)

            messages = [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": self.build_user_prompt(
                        file_path=normalized_file_path,
                        prompt=prompt,
                        language=self._active_language,
                        ast_result=ast_result,
                        read_result=read_result,
                    ),
                },
            ]
            response = await self.agent.ainvoke({"messages": messages})
            data = self.extract_json_object(self.message_to_text(response))
            return self.normalize_reader_result(
                data,
                file_path=normalized_file_path,
                language=self._active_language,
                ast_result=ast_result,
                read_result=read_result,
            )
        except Exception as exc:
            return {
                "ok": False,
                "flag": "CODE_READER_FAILED",
                "file_path": normalized_file_path,
                "language": self._active_language,
                "answer": "",
                "evidence": [],
                "missing_information": [],
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
            }
        finally:
            await self.stop_heartbeat()

    async def load_ast_context(self, file_path: str, language: str) -> dict:
        code_ast = CodeAstTool("code_ast", self.user_uuid, git_local_address=self.git_local_address)
        raw_result = await code_ast.execute(file_path=file_path, language=language)
        return self.loads_json_result(raw_result)

    async def load_source_context(self, file_path: str, start_line: int, end_line: int) -> dict:
        code_reader = CodeReaderTool("code_reader", self.user_uuid, git_local_address=self.git_local_address)
        raw_result = await code_reader.execute(file_path=file_path, start_line=start_line, end_line=end_line)
        return self.loads_json_result(raw_result)

    def build_user_prompt(
        self,
        file_path: str,
        prompt: str,
        language: str,
        ast_result: dict,
        read_result: Optional[dict] = None,
    ) -> str:
        python_lsp_note = "codeLsp is available because target_language is python."
        if language != "python":
            python_lsp_note = "codeLsp is not available for this language; use codeAst and codeReader only."
        return f"""
        target_file: {file_path}
        target_language: {language}
        lsp_policy: {python_lsp_note}

        ## User request
        {prompt}

        ## Initial AST context
        {json.dumps(self.compact_tool_result(ast_result), ensure_ascii=False)}

        ## Initial source context
        {json.dumps(self.compact_tool_result(read_result), ensure_ascii=False) if read_result is not None else "null"}

        Use tools if the initial context is not enough. Answer strictly as JSON.
        """

    def compact_tool_result(self, value):
        if value is None:
            return None
        if not isinstance(value, dict):
            return value
        result = dict(value)
        ast_sexpr = result.get("ast_sexpr")
        if isinstance(ast_sexpr, str) and len(ast_sexpr) > 12000:
            result["ast_sexpr"] = ast_sexpr[:12000] + "..."
            result["ast_sexpr_truncated_for_prompt"] = True
        return result

    def normalize_reader_result(
        self,
        data: dict,
        file_path: str,
        language: str,
        ast_result: dict,
        read_result: Optional[dict],
    ) -> dict:
        return {
            "ok": True,
            "flag": "CODE_ANSWER_GENERATED",
            "file_path": file_path,
            "language": language,
            "answer": str(data.get("answer") or "").strip(),
            "evidence": self.normalize_evidence(data.get("evidence")),
            "missing_information": self.normalize_list(data.get("missing_information")),
            "initial_ast_error": ast_result.get("error") if isinstance(ast_result, dict) else None,
            "initial_read_error": self.read_error(read_result),
        }

    def normalize_evidence(self, value) -> list:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    result.append({"file_path": self._active_file_path or "", "start_line": None, "end_line": None, "reason": text})
                continue
            if not isinstance(item, dict):
                continue
            result.append(
                {
                    "file_path": str(item.get("file_path") or self._active_file_path or "").strip(),
                    "start_line": self.optional_int(item.get("start_line")),
                    "end_line": self.optional_int(item.get("end_line")),
                    "reason": str(item.get("reason") or "").strip(),
                }
            )
        return result

    def normalize_list(self, value) -> list:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    def read_error(self, value: Optional[dict]):
        if not isinstance(value, dict):
            return None
        errors = value.get("errors") or []
        if errors:
            return errors
        return value.get("error")

    def message_to_text(self, response) -> str:
        if isinstance(response, dict):
            messages = response.get("messages")
            if messages:
                return self.message_to_text(messages[-1])
            content = response.get("content")
            if content is not None:
                return self.message_to_text(content)

        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content or "")

    def extract_json_object(self, text: str) -> dict:
        cleaned = text.strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        try:
            data = json.loads(cleaned)
        except json.JSONDecodeError:
            json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not json_match:
                raise
            data = json.loads(json_match.group(0))
        if not isinstance(data, dict):
            raise ValueError("Coderreader response must be a JSON object")
        return data

    def loads_json_result(self, value) -> dict:
        if isinstance(value, dict):
            return value
        return json.loads(str(value or "{}"))

    def normalize_file_path(self, file_path: str) -> str:
        raw_path = self._require_text(file_path, "file_path").replace("\\", "/").strip()
        if not raw_path.startswith("/") and not re.match(r"^[A-Za-z]:/", raw_path):
            return raw_path

        absolute_path = Path(raw_path).resolve()
        code_root = self.code_root().resolve()
        try:
            return absolute_path.relative_to(code_root).as_posix()
        except ValueError as exc:
            raise ValueError(f"file_path must be relative to the bound repository code root: {file_path}") from exc

    def detect_language(self, file_path: str, language: Optional[str] = None) -> str:
        if language:
            return self.normalize_language(language)
        suffix = Path(file_path).suffix.lower()
        mapping = {
            ".py": "python",
            ".pyi": "python",
            ".rs": "rust",
            ".c": "c",
            ".h": "c",
        }
        return mapping.get(suffix, suffix.lstrip(".") or "unknown")

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

    def optional_int(self, value) -> Optional[int]:
        if value is None:
            return None
        try:
            return int(value)
        except (TypeError, ValueError):
            return None

    def code_root(self) -> Path:
        return (
            self._default_storage_root()
            / "Gitrepositorys"
            / self._safe_path_part(self.user_uuid)
            / self._safe_repository_path(self.git_local_address)
        )

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

    def get_response_format(self):
        class ResponseFormat(BaseModel):
            answer: str = ""
            evidence: list[CodeEvidence] = Field(default_factory=list)
            missing_information: list[str] = Field(default_factory=list)

        return ResponseFormat


CoderReaderAgent = Coderreader
