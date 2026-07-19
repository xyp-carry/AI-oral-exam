import asyncio
import json
import re
import uuid
from concurrent.futures import ThreadPoolExecutor

from AIOralExamSystem.Tool.base_tool import BaseTool
from AIOralExamSystem.Tool.rag.file_tool import FileParserTool
from meilisearch import Client
from pydantic import BaseModel, Field
from config import get_settings

class SearchToolInput(BaseModel):
    query: str = Field(description="用于查询信息的一段话；如果传入空字符串，则读取该 source 下的全部文本块")


SearchDescription = (
    "查询当前用户 source 范围内的资料。query 为空字符串时返回全部资料，不使用 hybrid 检索；"
    "query 非空时使用 hybrid 检索。返回结果包含每个文本块的 token 估算和分批元数据。"
)


class SearchTool(BaseTool):
    """Search user-scoped document chunks without using AI inside the tool."""

    def __init__(self, name: str):
        super().__init__(name)
        self.description = SearchDescription
        self.client = Client("http://localhost:7700")

    async def _run(
        self,
        query: str,
        source: str,
        course_id: str,
        exam_id: str | None = None,
        batch_index: int = 0,
        target_tokens: int = 6000,
    ) -> str:
        with ThreadPoolExecutor(max_workers=1) as executor:
            loop = asyncio.get_event_loop()
            return await loop.run_in_executor(
                executor,
                self.search,
                query,
                source,
                course_id,
                exam_id,
                batch_index,
                target_tokens,
            )

    def search(
        self,
        query: str,
        source: str,
        course_id: str,
        exam_id: str | None = None,
        batch_index: int = 0,
        target_tokens: int = 6000,
    ) -> str:
        query = query or ""
        batch_index = max(0, int(batch_index or 0))
        target_tokens = min(12000, max(2000, int(target_tokens or 6000)))
        max_block_tokens = max(1000, target_tokens)

        if not query.strip():
            results = self._search_documents_sequential(source, course_id, exam_id)
            blocks = self._build_text_blocks(results.get("hits", []), max_block_tokens)
            return self._build_search_response(
                query=query,
                mode="sequential",
                order_by="chunk_order:asc",
                blocks=blocks,
                batch_index=batch_index,
                target_tokens=target_tokens,
                empty_instruction="No indexed chunks were found for this source. The caller should upload/insert the document before reading it.",
                next_instruction=(
                    "If has_more is true, call search again with the same empty query and next_batch_index "
                    "to continue reading the document in chunk_order order."
                ),
            )

        results = self._search_documents_hybrid(query, source, course_id, exam_id)
        blocks = self._build_text_blocks(results.get("hits", []), max_block_tokens)
        return self._build_search_response(
            query=query,
            mode="hybrid",
            order_by="relevance",
            blocks=blocks,
            batch_index=batch_index,
            target_tokens=target_tokens,
            empty_instruction="No relevant indexed chunks were found for this query.",
            next_instruction=(
                "If has_more is true, call search again with the same query and next_batch_index "
                "to continue reading relevant retrieved chunks."
            ),
        )

    def _build_search_response(
        self,
        query: str,
        mode: str,
        order_by: str,
        blocks: list,
        batch_index: int,
        target_tokens: int,
        empty_instruction: str,
        next_instruction: str,
    ) -> str:
        batches = self._build_batches(blocks, target_tokens)

        if not batches:
            return json.dumps(
                {
                    "query": query,
                    "mode": mode,
                    "order_by": order_by,
                    "batch_index": 0,
                    "total_batches": 0,
                    "has_more": False,
                    "next_batch_index": None,
                    "total_blocks": 0,
                    "total_tokens": 0,
                    "batch_tokens": 0,
                    "first_chunk_order": None,
                    "last_chunk_order": None,
                    "returned_chunk_orders": [],
                    "next_start_chunk_order": None,
                    "blocks": [],
                    "instruction": empty_instruction,
                },
                ensure_ascii=False,
            )

        if batch_index >= len(batches):
            batch_index = len(batches) - 1

        batch = batches[batch_index]
        has_more = batch_index < len(batches) - 1
        returned_chunk_orders = self._collect_chunk_orders(batch)
        first_chunk_order = returned_chunk_orders[0] if returned_chunk_orders else None
        last_chunk_order = returned_chunk_orders[-1] if returned_chunk_orders else None
        next_start_chunk_order = (
            last_chunk_order + 1
            if mode == "sequential" and has_more and isinstance(last_chunk_order, int)
            else None
        )

        return json.dumps(
            {
                "query": query,
                "mode": mode,
                "order_by": order_by,
                "batch_index": batch_index,
                "total_batches": len(batches),
                "has_more": has_more,
                "next_batch_index": batch_index + 1 if has_more else None,
                "total_blocks": len(blocks),
                "total_tokens": sum(block["token_count"] for block in blocks),
                "batch_tokens": sum(block["token_count"] for block in batch),
                "first_chunk_order": first_chunk_order,
                "last_chunk_order": last_chunk_order,
                "returned_chunk_orders": returned_chunk_orders,
                "next_start_chunk_order": next_start_chunk_order,
                "blocks": batch,
                "instruction": next_instruction,
            },
            ensure_ascii=False,
        )

    def _collect_chunk_orders(self, blocks: list) -> list:
        orders = []
        seen = set()
        for block in blocks:
            value = block.get("chunk_order")
            if value is None:
                continue
            try:
                order = int(value)
            except (TypeError, ValueError):
                continue
            if order in seen:
                continue
            seen.add(order)
            orders.append(order)
        return orders

    def _search_documents_sequential(self, source: str, course_id: str, exam_id: str | None = None) -> dict:
        index = self.client.index(self._course_index_name(course_id))
        filter_expr = self._build_filter(source, exam_id)

        hits = []
        offset = 0
        limit = 100

        while True:
            results = index.search(
                "",
                {
                    "filter": filter_expr,
                    "limit": limit,
                    "offset": offset,
                    "sort": ["chunk_order:asc"],
                },
            )
            batch_hits = results.get("hits", [])
            hits.extend(batch_hits)

            if not batch_hits or len(batch_hits) < limit:
                break

            offset += limit
            total_hits = results.get("estimatedTotalHits")
            if total_hits is not None and offset >= total_hits:
                break

        return {"hits": hits}

    def _search_documents_hybrid(self, query: str, source: str, course_id: str, exam_id: str | None = None) -> dict:
        index = self.client.index(self._course_index_name(course_id))
        filter_expr = self._build_filter(source, exam_id)

        return index.search(
            query,
            {
                "filter": filter_expr,
                "limit": 30,
                "hybrid": {
                    "embedder": "default",
                    "semanticRatio": 0.7,
                },
            },
        )

    def _escape_filter_value(self, value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    def _course_index_name(self, course_id: str) -> str:
        course_id = str(course_id or "").strip()
        if not course_id:
            raise ValueError("course_id is required")
        safe_course_id = re.sub(r"[^A-Za-z0-9_-]", "_", course_id)
        return f"course_{safe_course_id}"

    def _build_filter(self, source: str, exam_id: str | None = None) -> str:
        filters = [f'source = "{self._escape_filter_value(source)}"']
        if exam_id and str(exam_id).strip():
            filters.append(f'exam_id = "{self._escape_filter_value(str(exam_id).strip())}"')
        return " AND ".join(filters)

    def _count_tokens(self, text: str) -> int:
        if not text:
            return 0

        chinese_chars = re.findall(r"[\u4e00-\u9fff]", text)
        english_words = re.findall(r"[A-Za-z0-9_]+(?:[-'][A-Za-z0-9_]+)?", text)
        non_space_chars = re.sub(r"\s", "", text)
        counted_chars = len(chinese_chars) + sum(len(word) for word in english_words)
        other_chars = max(0, len(non_space_chars) - counted_chars)
        return max(1, int(len(chinese_chars) + len(english_words) * 1.3 + other_chars * 0.5))

    def _build_text_blocks(self, hits: list, max_block_tokens: int) -> list:
        blocks = []
        block_index = 1

        for hit in hits:
            content = str(hit.get("content", "")).strip()
            if not content:
                continue

            for part_index, part in enumerate(self._split_large_text(content, max_block_tokens), start=1):
                blocks.append(
                    {
                        "block_index": block_index,
                        "source_document_id": hit.get("id"),
                        "chunk_order": hit.get("chunk_order"),
                        "part_index": part_index,
                        "token_count": self._count_tokens(part),
                        "content": part,
                    }
                )
                block_index += 1

        return blocks

    def _split_large_text(self, text: str, max_tokens: int) -> list:
        if self._count_tokens(text) <= max_tokens:
            return [text]

        parts = []
        current_lines = []
        current_tokens = 0

        for line in text.splitlines():
            line = line.strip()
            if not line:
                continue

            line_tokens = self._count_tokens(line)
            if line_tokens > max_tokens:
                if current_lines:
                    parts.append("\n".join(current_lines))
                    current_lines = []
                    current_tokens = 0
                parts.extend(self._split_by_char_window(line, max_tokens))
                continue

            if current_lines and current_tokens + line_tokens > max_tokens:
                parts.append("\n".join(current_lines))
                current_lines = [line]
                current_tokens = line_tokens
            else:
                current_lines.append(line)
                current_tokens += line_tokens

        if current_lines:
            parts.append("\n".join(current_lines))

        return parts

    def _split_by_char_window(self, text: str, max_tokens: int) -> list:
        token_count = self._count_tokens(text)
        if token_count <= max_tokens:
            return [text]

        ratio = max_tokens / token_count
        window_size = max(200, int(len(text) * ratio))
        return [text[i:i + window_size] for i in range(0, len(text), window_size)]

    def _build_batches(self, blocks: list, target_tokens: int) -> list:
        batches = []
        current_batch = []
        current_tokens = 0

        for block in blocks:
            block_tokens = block["token_count"]
            if current_batch and current_tokens + block_tokens > target_tokens:
                batches.append(current_batch)
                current_batch = [block]
                current_tokens = block_tokens
            else:
                current_batch.append(block)
                current_tokens += block_tokens

        if current_batch:
            batches.append(current_batch)

        return batches

    def get_description(self) -> str:
        return self.description


class InsertTool(BaseTool):
    """Insert parsed document chunks into Meilisearch."""

    def __init__(self, name: str, token: str):
        super().__init__(name)
        self.description = "将文档插入到索引中"
        self.client = Client("http://localhost:7700")
        self.fileParser = FileParserTool(token, "file_parser")

    async def _run(
        self,
        data: list | str,
        source: str,
        type: str = "file",
        course_id: str | None = None,
        exam_id: str | None = None,
        work_dir: str | None = None,
        reload: bool = False,
        upload_batch_id: str | None = None,
        chunk_mode: str = "traditional",
        chunk_ai_model_settings: dict | None = None,
    ) -> str:
        if type == "file":
            chunksList = await self.fileParser.execute(
                file_paths=data,
                work_dir=work_dir,
                chunk_mode=chunk_mode,
                chunk_ai_model_settings=chunk_ai_model_settings,
            )
        else:
            chunksList = data

        if chunk_mode == "ai_toc":
            return chunksList

        if not chunksList:
            return "没有可插入的文档。"

        index = self.client.index(self._course_index_name(course_id))

        settings = {
            "embedders": {
                "default": {
                    "source": "rest",
                    "url": "https://open.bigmodel.cn/api/paas/v4/embeddings",
                    "dimensions": 2048,
                    "documentTemplate": "{{doc.content}}",
                    "request": {
                        "model": "embedding-3",
                        "input": ["{{text}}"],
                    },
                    "response": {
                        "data": [
                            {
                                "embedding": "{{embedding}}",
                            }
                        ]
                    },
                    "headers": {
                        "Authorization": f"Bearer {get_settings().model_api_key}",
                    },
                }
            },
            "filterableAttributes": ["source", "course_id", "exam_id", "upload_batch_id"],
            "sortableAttributes": ["chunk_order"],
        }

        task = index.update_settings(settings)
        self.client.wait_for_task(task.task_uid)
        if reload:
            self._delete_existing_documents(index, source, exam_id)
        print(chunksList)
        print(len(chunksList))
        if chunksList:
            print(len(chunksList[0]))
        inserted_count = 0
        chunk_order = 0
        for chunks in chunksList:
            documents = []
            for chunk in chunks:
                if not self.is_meaningful_text(chunk):
                    continue

                chunk_order += 1
                documents.append(
                    {
                        "id": str(uuid.uuid4()),
                        "source": source,
                        "course_id": course_id,
                        "exam_id": exam_id,
                        "upload_batch_id": upload_batch_id,
                        "chunk_order": chunk_order,
                        "content": chunk,
                    }
                )

            if not documents:
                continue

            task = index.add_documents(documents, primary_key="id")
            self.client.wait_for_task(task.task_uid)
            inserted_count += len(documents)
            
        print(f"成功插入 {inserted_count} 条文档")
        return f"成功插入 {inserted_count} 条文档"

    def get_description(self) -> str:
        return self.description

    def _course_index_name(self, course_id: str | None) -> str:
        course_id = str(course_id or "").strip()
        if not course_id:
            raise ValueError("course_id is required")
        safe_course_id = re.sub(r"[^A-Za-z0-9_-]", "_", course_id)
        return f"course_{safe_course_id}"

    def delete_documents_by_batch(
        self,
        course_id: str | None,
        upload_batch_id: str | None,
    ) -> None:
        if not upload_batch_id or not str(upload_batch_id).strip():
            return
        index = self.client.index(self._course_index_name(course_id))
        self._delete_documents_by_filter(
            index,
            f'upload_batch_id = "{self._escape_filter_value(str(upload_batch_id).strip())}"',
        )

    def delete_course_documents_by_source(
        self,
        course_id: str | None,
        source: str,
    ) -> None:
        source = str(source or "").strip()
        if not source:
            raise ValueError("source is required")
        course_id_value = str(course_id or "").strip()
        if not course_id_value:
            raise ValueError("course_id is required")
        index = self.client.index(self._course_index_name(course_id_value))
        self._delete_documents_by_filter(
            index,
            (
                f'source = "{self._escape_filter_value(source)}" '
                f'AND course_id = "{self._escape_filter_value(course_id_value)}"'
            ),
        )

    def delete_existing_documents_except_batch(
        self,
        course_id: str | None,
        source: str,
        exam_id: str | None,
        upload_batch_id: str | None,
    ) -> None:
        filter_expr = self._existing_documents_filter(source, exam_id)
        index = self.client.index(self._course_index_name(course_id))
        excluded_batch_id = str(upload_batch_id or "").strip()
        self._delete_documents_by_filter(
            index,
            filter_expr,
            exclude_upload_batch_id=excluded_batch_id or None,
        )

    def _delete_existing_documents(self, index, source: str, exam_id: str | None) -> None:
        self._delete_documents_by_filter(index, self._existing_documents_filter(source, exam_id))

    def _existing_documents_filter(self, source: str, exam_id: str | None) -> str:
        if not source or not str(source).strip():
            raise ValueError("source is required when reload is true")
        if not exam_id or not str(exam_id).strip():
            raise ValueError("exam_id is required when reload is true")
        return (
            f'source = "{self._escape_filter_value(source)}" '
            f'AND exam_id = "{self._escape_filter_value(str(exam_id).strip())}"'
        )

    def _delete_documents_by_filter(
        self,
        index,
        filter_expr: str,
        exclude_upload_batch_id: str | None = None,
    ) -> None:
        document_ids = []
        offset = 0
        limit = 1000

        while True:
            results = index.search(
                "",
                {
                    "filter": filter_expr,
                    "limit": limit,
                    "offset": offset,
                    "attributesToRetrieve": ["id", "upload_batch_id"],
                },
            )
            hits = results.get("hits", [])
            document_ids.extend(
                hit["id"]
                for hit in hits
                if hit.get("id")
                and (
                    exclude_upload_batch_id is None
                    or str(hit.get("upload_batch_id") or "") != exclude_upload_batch_id
                )
            )

            if not hits or len(hits) < limit:
                break

            offset += limit
            total_hits = results.get("estimatedTotalHits")
            if total_hits is not None and offset >= total_hits:
                break

        for start in range(0, len(document_ids), limit):
            task = index.delete_documents(document_ids[start:start + limit])
            self.client.wait_for_task(task.task_uid)

    def _escape_filter_value(self, value: str) -> str:
        return str(value).replace("\\", "\\\\").replace('"', '\\"')

    def is_meaningful_text(self, text: str) -> bool:
        if not text or not text.strip():
            return False

        clean_text = text.strip()

        if len(clean_text) < 5:
            return False

        if not re.sub(r'[\w\s\.,;:!?\-\'\"()（）：。，；！？、]', "", clean_text):
            return False

        if re.match(r"^[\s\W_]+$", clean_text):
            return False

        useless_patterns = [
            r"^第\s*\d+\s*页",
            r"^\d+\s*/\s*\d+$",
            r"^(目录|目录\n|TABLE OF CONTENTS)$",
            r"^(版权所有|Copyright|All rights reserved).*",
            r"^\.{3,}$",
            r"^-+$",
            r"^=+$",
        ]
        for pattern in useless_patterns:
            if re.match(pattern, clean_text, re.IGNORECASE):
                return False

        chinese_chars = re.findall(r"[\u4e00-\u9fff]", clean_text)
        if len(clean_text) > 20 and len(chinese_chars) / len(clean_text) < 0.2:
            return False

        return True
