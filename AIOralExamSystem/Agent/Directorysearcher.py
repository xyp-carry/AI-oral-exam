import json
import re
from pathlib import Path
from typing import Optional

from AIOralExamSystem.Agent.base_Agent import BaseAgent
from pydantic import BaseModel, Field


class Directorysearcher(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        thinking: bool = False,
        response_format: bool = True,
        temperature: float = 0,
        show_tool_io: bool = False,
    ):
        super().__init__(
            "Directorysearcher",
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
        You are a document table-of-contents detection agent. You only inspect the first preview lines of a document and decide whether there is an explicit TOC/Contents/Table of Contents section.

        ## Rules
        - Only identify entries from an explicit TOC/Contents/Table of Contents area.
        - Only return top-level/main TOC entries, such as chapter titles or level-1 section titles.
        - Do not return subsections, minor headings, nested entries, or items such as 1.1, 1.2.3, 2.4, etc.
        - Do not infer a TOC from ordinary body headings, ordinary title lists, or inline subsection headings.
        - Do not invent missing TOC entries.
        - source_line must use the line number shown in the user input; return null if it cannot be determined.

        ## Output
        Return strict JSON:
        {
          "has_toc": true,
          "confidence": 0.0,
          "items": [
            {
              "level": 1,
              "title": "top-level TOC entry title only",
              "number": "chapter or main-section number, empty string if absent",
              "source_line": 1
            }
          ]
        }

        If there is no explicit TOC, return:
        {
          "has_toc": false,
          "confidence": 0,
          "items": []
        }
        """

    async def execute(self, document_dir: str, max_preview_lines: int = 200) -> dict:
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        try:
            document_path = self.resolve_document_path(document_dir)
            if not document_path:
                return self.build_toc_not_found_result(
                    0,
                    "DOCUMENT_NOT_FOUND",
                    document_path=None,
                )

            preview_lines = self.read_preview_lines(document_path, max_preview_lines)
            preview_text = "\n".join(
                f"{index}: {line}"
                for index, line in enumerate(preview_lines, start=1)
            )
            if not preview_text.strip():
                return self.build_toc_not_found_result(
                    len(preview_lines),
                    "EMPTY_PREVIEW",
                    document_path=str(document_path),
                )

            historys = [
                {"role": "system", "content": self.system_prompt},
                {
                    "role": "user",
                    "content": self.build_user_prompt(str(document_path), preview_text),
                },
            ]
            response = await self.agent.ainvoke({"messages": historys})
            response_text = self.message_to_text(response)
            data = self.extract_json_object(response_text)
            toc_items = self.normalize_toc_items(data.get("items") or data.get("toc_items") or [])
            confidence = data.get("confidence")
            has_toc = bool(data.get("has_toc")) and bool(toc_items)
            if not has_toc:
                return self.build_toc_not_found_result(
                    len(preview_lines),
                    "TOC_NOT_FOUND",
                    confidence=confidence,
                    document_path=str(document_path),
                    raw_response=response_text,
                )
            return {
                "ok": True,
                "mode": "ai_toc",
                "flag": "TOC_FOUND",
                "has_toc": True,
                "document_path": str(document_path),
                "preview_line_count": len(preview_lines),
                "confidence": confidence,
                "directory_list": [item["title"] for item in toc_items],
                "toc_items": toc_items,
            }
        except Exception as exc:
            return {
                "ok": False,
                "mode": "ai_toc",
                "flag": "AI_TOC_DETECTION_FAILED",
                "has_toc": False,
                "document_path": str(document_dir or ""),
                "preview_line_count": 0,
                "directory_list": [],
                "toc_items": [],
                "error_class": exc.__class__.__name__,
                "error_message": str(exc),
            }
        finally:
            await self.stop_heartbeat()

    def resolve_document_path(self, document_dir: str) -> Optional[Path]:
        path = Path(document_dir)
        if path.is_file():
            return path
        if not path.is_dir():
            return None

        full_md_files = sorted(path.rglob("full.md"))
        if full_md_files:
            return full_md_files[0]

        md_files = sorted(
            file_path
            for file_path in path.rglob("*")
            if file_path.is_file() and file_path.suffix.lower() in {".md", ".markdown"}
        )
        return md_files[0] if md_files else None

    def read_preview_lines(self, document_path: Path, max_preview_lines: int) -> list:
        preview_lines = []
        with document_path.open("r", encoding="utf-8") as file:
            for _, line in zip(range(max_preview_lines), file):
                preview_lines.append(line.rstrip("\n"))
        return preview_lines

    def build_user_prompt(self, document_path: str, preview_text: str) -> str:
        return f"""
        document_path: {document_path}

        Inspect the numbered Markdown preview below and identify explicit TOC entries.
        Return only top-level/main TOC entries. Do not return subsections, nested headings, or minor titles.
        Do not output explanations; return JSON only.

        {preview_text}
        """

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
            raise ValueError("Directorysearcher response must be a JSON object")
        return data

    def normalize_toc_items(self, items) -> list:
        if not isinstance(items, list):
            return []
        normalized_items = []
        for item in items:
            if not isinstance(item, dict):
                continue
            raw_title = str(item.get("title") or "")
            title = raw_title.strip()
            if not title:
                continue
            try:
                level = int(item.get("level") or 1)
            except (TypeError, ValueError):
                level = 1
            level = min(max(level, 1), 6)
            number = str(item.get("number") or "").strip()
            if not self.is_top_level_toc_item(level, number, raw_title):
                continue

            source_line = item.get("source_line")
            try:
                source_line = int(source_line) if source_line is not None else None
            except (TypeError, ValueError):
                source_line = None

            normalized_items.append(
                {
                    "level": 1,
                    "title": title,
                    "number": number,
                    "source_line": source_line,
                }
            )
        return normalized_items

    def is_top_level_toc_item(self, level: int, number: str, raw_title: str) -> bool:
        if level > 1:
            return False
        title = str(raw_title or "").strip()
        number = str(number or "").strip()
        if re.match(r"^#{2,}\s+", title):
            return False
        if re.match(r"^\d+(?:\.\d+)+\.?$", number):
            return False
        if re.match(r"^\d+(?:\.\d+)+(?:\b|[\s.)-])", title):
            return False
        return True

    def build_toc_not_found_result(
        self,
        preview_line_count: int,
        reason: str,
        confidence=None,
        document_path: Optional[str] = None,
        raw_response: Optional[str] = None,
    ) -> dict:
        result = {
            "ok": False,
            "mode": "ai_toc",
            "flag": "TOC_NOT_FOUND",
            "reason": reason,
            "has_toc": False,
            "document_path": document_path,
            "preview_line_count": preview_line_count,
            "confidence": confidence,
            "directory_list": [],
            "toc_items": [],
        }
        if raw_response:
            result["raw_response"] = raw_response[:1000]
        return result

    def get_response_format(self):
        class TocItem(BaseModel):
            level: int
            title: str
            number: str = ""
            source_line: Optional[int] = None

        class ResponseFormat(BaseModel):
            has_toc: bool
            confidence: float = 0
            items: list[TocItem] = Field(default_factory=list)

        return ResponseFormat
