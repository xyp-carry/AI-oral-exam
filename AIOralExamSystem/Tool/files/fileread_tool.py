import json
from pathlib import Path

from AIOralExamSystem.Tool.base_tool import BaseTool


PROJECT_ROOT = Path("/root/AI-Oral-exam").resolve()


class FileReadTool(BaseTool):
    """Read a project-scoped file and split it into conservative text chunks."""

    def __init__(self, name: str):
        super().__init__(name)
        self.description = "读取项目主目录下的明确文件路径，并按保守 token 阈值切分为文本块"

    def _run(
        self,
        file_path: str,
        task_prompt: str = "",
        max_context_tokens: int = 12000,
        reserved_output_tokens: int = 3000,
        target_tokens: int | None = None,
    ) -> str:
        path = self._resolve_project_file(file_path)
        if path is None:
            return self._json_error("PATH_OUTSIDE_PROJECT", file_path)
        if not path.exists():
            return self._json_error("DOCUMENT_NOT_FOUND", str(path))
        if not path.is_file():
            return self._json_error("PATH_IS_NOT_FILE", str(path))

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError as exc:
            return self._json_error("FILE_DECODE_FAILED", str(path), str(exc))
        except OSError as exc:
            return self._json_error("FILE_READ_FAILED", str(path), str(exc))

        if not text.strip():
            return self._json_error("EMPTY_DOCUMENT", str(path))

        document_tokens = self.estimate_tokens(text)
        safe_input_tokens = target_tokens or self.estimate_safe_input_tokens(
            task_prompt,
            max_context_tokens,
            reserved_output_tokens,
        )
        chunks = self.build_document_chunks(text, safe_input_tokens)
        return json.dumps(
            {
                "ok": True,
                "flag": "FILE_CHUNKS_READY",
                "file_path": str(path),
                "file_name": path.name,
                "document_tokens": document_tokens,
                "safe_input_tokens": safe_input_tokens,
                "chunk_count": len(chunks),
                "chunks": [
                    {
                        "chunk_index": index,
                        "token_count": self.estimate_tokens(chunk),
                        "content": chunk,
                    }
                    for index, chunk in enumerate(chunks, start=1)
                ],
            },
            ensure_ascii=False,
        )

    def _resolve_project_file(self, file_path: str) -> Path | None:
        if not file_path:
            return None
        raw_path = Path(str(file_path)).expanduser()
        if not raw_path.is_absolute():
            raw_path = PROJECT_ROOT / raw_path
        path = raw_path.resolve()
        try:
            path.relative_to(PROJECT_ROOT)
        except ValueError:
            return None
        return path

    def estimate_tokens(self, text: str) -> int:
        text = str(text or "")
        return max(len(text) // 2, len(text.split()))

    def estimate_safe_input_tokens(
        self,
        task_prompt: str,
        max_context_tokens: int,
        reserved_output_tokens: int,
    ) -> int:
        try:
            context_window = int(max_context_tokens or 12000)
        except (TypeError, ValueError):
            context_window = 12000
        try:
            reserved_tokens = int(reserved_output_tokens or 3000)
        except (TypeError, ValueError):
            reserved_tokens = 3000

        task_tokens = self.estimate_tokens(task_prompt)
        available = context_window - reserved_tokens - task_tokens
        if available <= 0:
            return 3000
        return max(2000, int(available * 0.7))

    def build_document_chunks(self, text: str, target_tokens: int) -> list:
        lines = text.splitlines()
        chunks = []
        current_lines = []
        current_tokens = 0

        for line in lines:
            line_tokens = self.estimate_tokens(line) + 1
            if current_lines and current_tokens + line_tokens > target_tokens:
                chunks.append("\n".join(current_lines).strip())
                current_lines = []
                current_tokens = 0

            if line_tokens > target_tokens:
                chunks.extend(self.split_long_text(line, target_tokens))
                continue

            current_lines.append(line)
            current_tokens += line_tokens

        if current_lines:
            chunks.append("\n".join(current_lines).strip())

        return [chunk for chunk in chunks if chunk.strip()] or [text]

    def split_long_text(self, text: str, target_tokens: int) -> list:
        chunk_chars = max(target_tokens * 2, 1000)
        overlap_chars = min(500, chunk_chars // 5)
        chunks = []
        start = 0
        while start < len(text):
            end = min(start + chunk_chars, len(text))
            chunks.append(text[start:end].strip())
            if end >= len(text):
                break
            start = max(end - overlap_chars, start + 1)
        return [chunk for chunk in chunks if chunk]

    def _json_error(self, flag: str, file_path: str, error_message: str = "") -> str:
        return json.dumps(
            {
                "ok": False,
                "flag": flag,
                "project_root": str(PROJECT_ROOT),
                "file_path": str(file_path or ""),
                "error_message": error_message,
                "chunks": [],
            },
            ensure_ascii=False,
        )