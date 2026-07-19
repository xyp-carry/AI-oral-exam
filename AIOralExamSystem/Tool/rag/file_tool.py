from AIOralExamSystem.Tool.base_tool import BaseTool
import json
import pickle
import re
from concurrent.futures import ThreadPoolExecutor
import asyncio
from pathlib import Path
from AIOralExamSystem.Agent.Directorysearcher import Directorysearcher
from AIOralExamSystem.Tool.files.minerUTool import MinerUFileTool
from config import get_settings

class FileParserTool(BaseTool):
    """文件解析工具"""
    def __init__(self, token: str, name: str):
        super().__init__(name)
        self.description = "Parse file content"
        self.mineru_tool = MinerUFileTool(token)

    async def _run(
        self,
        file_paths: list,
        work_dir: str | None = None,
        chunk_mode: str = "traditional",
        chunk_ai_model_settings: dict | None = None,
    ) -> str:
        """
        query: 用户查询的信息
        source: 用户的id，用于指定查询的文档来源
        """
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            chunks = await loop.run_in_executor(
                executor,
                self.getfile,
                file_paths,
                work_dir,
                chunk_mode,
                chunk_ai_model_settings,
            )
        return chunks
    
    def getfile(
        self,
        file_paths: list,
        work_dir: str | None = None,
        chunk_mode: str = "traditional",
        chunk_ai_model_settings: dict | None = None,
    ):
        if isinstance(file_paths, str):
            file_paths = [file_paths]
        file_chunks = []
        mineru_file_paths = []
        for file_path in file_paths:
            path = Path(file_path)
            suffix = path.suffix.lower()
            if suffix in {".pdf", ".doc", ".docx"}:
                mineru_file_paths.append(str(path))
            elif suffix in {".md", ".markdown"}:
                file_chunks.append(self.parse_md_file(path, chunk_mode, chunk_ai_model_settings))
            elif suffix in {".json", ".jsonl", ".ndjson"}:
                file_chunks.append(self.parse_json_file(path))
            elif suffix == ".pkl":
                file_chunks.append(self.parse_pkl_file(path))
            else:
                print(f"Skip unsupported file type: {path}")

        if mineru_file_paths:
            file_chunks.extend(
                self.parse_mineru_files(
                    mineru_file_paths,
                    work_dir,
                    chunk_mode,
                    chunk_ai_model_settings,
                )
            )
        
        return file_chunks

    def parse_mineru_files(
        self,
        file_paths: list,
        work_dir: str | None = None,
        chunk_mode: str = "traditional",
        chunk_ai_model_settings: dict | None = None,
    ):
        work_root = self._resolve_work_root(work_dir)
        md_files = self.mineru_tool.parse_to_markdown_files(file_paths, work_root)
        return [
            self.parse_md_file(md_file, chunk_mode, chunk_ai_model_settings)
            for md_file in md_files
        ]

    def parse_md_file(
        self,
        file_path: Path,
        chunk_mode: str = "traditional",
        chunk_ai_model_settings: dict | None = None,
    ) -> list | dict:
        if chunk_mode == "ai_toc":
            return self.detect_md_toc_with_ai(str(file_path), chunk_ai_model_settings)
        if chunk_mode == "ai_chunk":
            return self.parse_md_to_chunks_by_ai_toc(file_path, chunk_ai_model_settings)
        content = file_path.read_text(encoding="utf-8")
        return self.parse_md_file_content(content, chunk_mode, chunk_ai_model_settings)

    def parse_md_file_content(
        self,
        content: str,
        chunk_mode: str = "traditional",
        chunk_ai_model_settings: dict | None = None,
    ) -> list | dict:
        if chunk_mode == "ai_toc":
            return self._build_toc_not_found_result(0, "DOCUMENT_PATH_REQUIRED")
        if chunk_mode == "ai_chunk":
            return self.parse_md_to_chunks(content)
        return self.parse_md_to_chunks(content)

    def detect_md_toc_with_ai(
        self,
        document_dir: str,
        ai_model_settings: dict | None = None,
    ) -> dict:
        try:
            model_settings = self._resolve_toc_model_settings(ai_model_settings)
            agent = Directorysearcher(model_settings)
            return asyncio.run(agent.execute(document_dir=str(document_dir)))
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

    def _resolve_toc_model_settings(self, ai_model_settings: dict | None = None) -> dict:
        if ai_model_settings:
            model_settings = dict(ai_model_settings)
        else:
            settings = get_settings()
            model_settings = {
                "model_name": settings.model_name,
                "model_url": settings.model_url,
                "model_api_key": settings.model_api_key,
            }
        if model_settings.get("base_url") and not model_settings.get("model_url"):
            model_settings["model_url"] = model_settings["base_url"]
        for key in ("model_name", "model_url", "model_api_key"):
            if not model_settings.get(key):
                raise ValueError(f"{key} is required for ai_toc mode")
        return model_settings

    def _build_toc_not_found_result(
        self,
        preview_line_count: int,
        reason: str,
        confidence=None,
        raw_response: str | None = None,
    ) -> dict:
        result = {
            "ok": False,
            "mode": "ai_toc",
            "flag": "TOC_NOT_FOUND",
            "reason": reason,
            "has_toc": False,
            "preview_line_count": preview_line_count,
            "confidence": confidence,
            "directory_list": [],
            "toc_items": [],
        }
        if raw_response:
            result["raw_response"] = raw_response[:1000]
        return result

    def parse_md_to_chunks_by_ai_toc(
        self,
        file_path: Path,
        ai_model_settings: dict | None = None,
    ) -> list:
        toc_result = self.detect_md_toc_with_ai(str(file_path), ai_model_settings)
        text = file_path.read_text(encoding="utf-8")
        if not toc_result.get("has_toc"):
            return self.parse_md_to_chunks(text)

        toc_items = toc_result.get("toc_items") or []
        if not toc_items:
            return self.parse_md_to_chunks(text)

        chunks = self.split_md_by_toc_items(text, toc_items)
        if not chunks:
            return self.parse_md_to_chunks(text)
        return chunks

    def split_md_by_toc_items(self, text: str, toc_items: list) -> list:
        text = self.format_document(text)
        lines = text.splitlines()
        located_items = self.locate_toc_headings(lines, toc_items)
        if len(located_items) < 2:
            return []

        chunks = []
        title_stack = []
        for index, located in enumerate(located_items):
            item = located["item"]
            start = located["line_index"]
            end = located_items[index + 1]["line_index"] if index + 1 < len(located_items) else len(lines)
            content = "\n".join(lines[start:end]).strip()
            if not content:
                continue

            level = int(item.get("level") or 1)
            title = str(item.get("title") or "").strip()
            while title_stack and title_stack[-1][0] >= level:
                title_stack.pop()
            title_stack.append((level, title))

            path_str = " > ".join([f"{'#' * lv} {t}" for lv, t in title_stack])
            chunks.append(f"{path_str}\n\n{content}")
        return chunks

    def locate_toc_headings(self, lines: list, toc_items: list) -> list:
        search_start = self.get_toc_search_start_line(toc_items)
        located_items = []
        current_line_index = search_start

        for item in toc_items:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title") or "").strip()
            normalized_title = self.normalize_toc_title(title)
            if not normalized_title:
                continue

            matched_index = self.find_toc_heading_line(
                lines,
                normalized_title,
                current_line_index,
            )
            if matched_index is None:
                continue

            located_items.append({
                "item": item,
                "line_index": matched_index,
            })
            current_line_index = matched_index + 1

        if len(toc_items) and len(located_items) / len(toc_items) < 0.5:
            return []
        return located_items

    def get_toc_search_start_line(self, toc_items: list) -> int:
        source_lines = []
        for item in toc_items:
            source_line = item.get("source_line") if isinstance(item, dict) else None
            try:
                if source_line is not None:
                    source_lines.append(int(source_line))
            except (TypeError, ValueError):
                continue
        if not source_lines:
            return 0
        return max(source_lines)

    def find_toc_heading_line(self, lines: list, normalized_title: str, start_index: int) -> int | None:
        for index in range(start_index, len(lines)):
            line = lines[index].strip()
            if not line:
                continue
            normalized_line = self.normalize_toc_title(line)
            if normalized_line == normalized_title:
                return index
        return None

    def normalize_toc_title(self, value: str) -> str:
        value = re.sub(r'^(#{1,6})\s*', '', str(value or "").strip())
        value = re.sub(r'\s+', ' ', value)
        value = re.sub(r'\s*[.·…]{2,}\s*\d+\s*$', '', value)
        value = re.sub(r'\s+\d+\s*$', '', value)
        return value.strip()

    def parse_json_file(self, file_path: Path) -> list:
        suffix = file_path.suffix.lower()
        if suffix in {".jsonl", ".ndjson"}:
            return self.parse_json_lines(file_path)

        raw_text = file_path.read_text(encoding="utf-8")
        try:
            data = json.loads(raw_text)
        except json.JSONDecodeError as exc:
            raise ValueError(f"Invalid JSON file: {file_path}") from exc

        if isinstance(data, list):
            chunks = []
            for item in data:
                text = self.json_to_search_text(item)
                if text.strip():
                    chunks.append(text)
            return chunks

        text = self.json_to_search_text(data)
        return [text] if text.strip() else []

    def parse_json_lines(self, file_path: Path) -> list:
        chunks = []
        for line_number, line in enumerate(file_path.read_text(encoding="utf-8").splitlines(), start=1):
            line = line.strip()
            if not line:
                continue
            try:
                data = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ValueError(f"Invalid JSONL line {line_number} in {file_path}") from exc
            text = self.json_to_search_text(data)
            if text.strip():
                chunks.append(text)
        return chunks

    def parse_pkl_file(self, file_path: Path) -> list:
        with file_path.open("rb") as f:
            data = pickle.load(f)

        if isinstance(data, list):
            chunks = []
            for item in data:
                text = self.json_to_search_text(item)
                if text.strip():
                    chunks.append(text)
            return chunks

        text = self.json_to_search_text(data)
        return [text] if text.strip() else []

    def json_to_search_text(self, data) -> str:
        if isinstance(data, dict):
            lines = []
            self.flatten_json_lines(data, lines)
            return "\n".join(lines)
        if isinstance(data, list):
            parts = []
            for item in data:
                text = self.json_to_search_text(item)
                if text.strip():
                    parts.append(text)
            return "\n".join(parts)
        if data is None:
            return ""
        return str(data)

    def flatten_json_lines(self, data: dict, lines: list, prefix: str = "") -> None:
        for key, value in data.items():
            field_name = f"{prefix}.{key}" if prefix else str(key)
            if isinstance(value, dict):
                self.flatten_json_lines(value, lines, field_name)
            elif isinstance(value, list):
                if all(not isinstance(item, (dict, list)) for item in value):
                    values = [str(item) for item in value if item is not None]
                    if values:
                        lines.append(f"{field_name}: {', '.join(values)}")
                else:
                    for index, item in enumerate(value, start=1):
                        item_field_name = f"{field_name}[{index}]"
                        if isinstance(item, dict):
                            self.flatten_json_lines(item, lines, item_field_name)
                        elif isinstance(item, list):
                            nested_text = self.json_to_search_text(item)
                            if nested_text.strip():
                                lines.append(f"{item_field_name}: {nested_text}")
                        elif item is not None:
                            lines.append(f"{item_field_name}: {item}")
            elif value is not None:
                lines.append(f"{field_name}: {value}")

    def _resolve_work_root(self, work_dir: str | None) -> Path:
        if work_dir and str(work_dir).strip():
            root = Path(str(work_dir).strip()).expanduser()
        else:
            root = self._project_root() / "FILE"
        root.mkdir(parents=True, exist_ok=True)
        return root

    def _project_root(self) -> Path:
        current_file = Path(__file__).resolve()
        for parent in current_file.parents:
            if parent.name == "AIOralExamSystem":
                return parent.parent
        return Path.cwd()

    def get_description(self) -> str:
        return self.description
    
    def format_document(self, text: str) -> str:
        """保留整个文档结构，仅将里面的 <table> 替换为标准 MD 表格"""
        def convert_table(match):
            html_str = match.group(0)
            tr_pattern = re.compile(r'<tr[^>]*>(.*?)</tr>', re.DOTALL | re.IGNORECASE)
            cell_pattern = re.compile(r'<(?:th|td)[^>]*>(.*?)</(?:th|td)>', re.DOTALL | re.IGNORECASE)
            
            md_lines = []
            is_first_row = True
            
            for row_match in tr_pattern.finditer(html_str):
                cells = [re.sub(r'<[^>]+>', '', cell.strip()) for cell in cell_pattern.findall(row_match.group(1))]
                if not cells: continue
                    
                md_lines.append("| " + " | ".join(cells) + " |")
                
                if is_first_row:
                    md_lines.append("| " + " | ".join(["---"] * len(cells)) + " |")
                    is_first_row = False
                    
            return "\n".join(md_lines)

        # 匹配全文所有 <table> 并调用内部函数替换
        return re.sub(r'<table.*?>.*?</table>', convert_table, text, flags=re.DOTALL | re.IGNORECASE)

    def parse_md_to_chunks(self, text: str) -> list:
        """
        1. 接收 str 类型的 md 文本
        2. 将所有 <table> 转变成 | --- | 格式
        3. 以任意 # 标题为边界切分，每个块必须附带其上方所有的层级标题路径
        """
        text = self.format_document(text)
        lines = [line.strip() for line in text.splitlines()]
        
        chunks = []
        
        # 【核心改动】使用栈来维护当前的层级路径
        # 栈的结构: [(level: int, title: str), ...]
        title_stack = []
        current_content_lines = [] 
        i = 0
        
        def save_chunk():
            
            nonlocal current_content_lines
            nonlocal title_stack
            chunk_str = "\n".join(current_content_lines).strip()
            if chunk_str:
                # 拼接完整的层级路径，例如: "# 总览 > ## 方法 > ### 算法"
                path_str = " > ".join([f"{'#' * lv} {t}" for lv, t in title_stack])
                chunks.append(f"{path_str}\n\n{chunk_str}")
            current_content_lines = []
            
        while i < len(lines):
            
            line = lines[i]
            
            # 匹配任意级别的标题 (#, ##, ###, #### 等)
            title_match = re.match(r'^(#{1,6})\s*(.*)', line)
            
            if title_match:
                save_chunk()  # 遇到新标题，前面的内容先存起来
                
                level = len(title_match.group(1))
                title_text = title_match.group(2).strip()
                
                # 【栈操作】维护层级关系
                # 1. 弹出栈中等于或低于当前层级的旧标题 (比如遇到##，就把栈里的##和###都弹出去)
                while title_stack and title_stack[-1][0] >= level:
                    title_stack.pop()
                # 2. 将当前新标题压入栈中
                title_stack.append((level, title_text))
                
                i += 1
            else:
                # 如果需要保留非表格文字，取消注释下一行：
                current_content_lines.append(line)
                i += 1
                
        save_chunk() # 处理文档末尾的残留内容
        return chunks
