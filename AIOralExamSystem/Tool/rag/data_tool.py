import asyncio
import json
import math
import re
import uuid
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

from AIOralExamSystem.Tool.base_tool import BaseTool
from AIOralExamSystem.Tool.rag.faiss_store import FaissDocumentStore, embed_texts
from AIOralExamSystem.Tool.rag.file_tool import FileParserTool
from LLM.model_repository import get_user_model
from pydantic import BaseModel, Field

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
        self.store = FaissDocumentStore()

    async def _embedding_settings_for_scope(
        self, course_id: str, source: str, exam_id: str | None,
    ) -> dict | None:
        scope = await asyncio.to_thread(
            self.store.get_scope_info, course_id, source, exam_id,
        )
        if not scope:
            return None
        model_id = str(scope.get("model_id") or "").strip()
        if not model_id:
            raise ValueError("EMBEDDING_MODEL_ID_NOT_FOUND")
        model = await get_user_model(model_id, include_api_key=True)
        if not model or not str(model.get("model_api_key") or "").strip():
            raise ValueError("EMBEDDING_MODEL_NOT_AVAILABLE")
        return {
            "model_id": model_id,
            "model_name": scope["model_name"],
            "model_url": scope["model_url"],
            "model_api_key": model["model_api_key"],
            "dimensions": scope["dimensions"],
            "embedding_max_bytes": scope["embedding_max_bytes"],
        }

    async def search_top_documents(
        self, query: str, sources: list[str], course_id: str,
        top_n: int = 10,
    ) -> list[dict]:
        if not str(query or "").strip():
            raise ValueError("QUERY_REQUIRED")
        if not 1 <= top_n <= 100:
            raise ValueError("TOP_N_OUT_OF_RANGE")

        rank_limit = max(60, top_n)
        vector_cache = {}
        semantic_hits = []
        documents = []
        for source in dict.fromkeys(sources):
            settings = await self._embedding_settings_for_scope(
                course_id, source, None,
            )
            if settings is None:
                continue
            model_key = (
                settings["model_id"],
                settings["model_name"],
                settings["model_url"],
                settings["dimensions"],
                settings["embedding_max_bytes"],
            )
            if model_key not in vector_cache:
                vector_cache[model_key] = (
                    await asyncio.to_thread(embed_texts, [query], settings)
                )[0]
            source_hits, source_documents = await asyncio.to_thread(
                self.store.search,
                course_id, source, None, vector_cache[model_key],
                settings, rank_limit,
            )
            semantic_hits.extend(source_hits)
            documents.extend(source_documents)

        if not documents:
            return []
        semantic_hits.sort(
            key=lambda row: row["_semantic_score"], reverse=True,
        )
        return await asyncio.to_thread(
            self._rank_documents, query, semantic_hits[:rank_limit],
            documents, top_n,
        )

    async def _run(
        self,
        query: str,
        source: str,
        course_id: str,
        exam_id: str | None = None,
        batch_index: int = 0,
        target_tokens: int = 6000,
    ) -> str:
        embedding_settings = None
        if str(query or "").strip():
            embedding_settings = await self._embedding_settings_for_scope(
                course_id, source, exam_id,
            )
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
                embedding_settings,
            )

    def search(
        self,
        query: str,
        source: str,
        course_id: str,
        exam_id: str | None = None,
        batch_index: int = 0,
        target_tokens: int = 6000,
        embedding_settings: dict | None = None,
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

        results = self._search_documents_hybrid(query, source, course_id, exam_id, embedding_settings)
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

    def _search_documents_sequential(
        self, source: str, course_id: str, exam_id: str | None = None,
    ) -> dict:
        return {"hits": self.store.list_documents(course_id, source, exam_id)}

    def _search_documents_hybrid(
        self, query: str, source: str, course_id: str,
        exam_id: str | None = None, embedding_settings: dict | None = None,
        limit: int = 30,
    ) -> dict:
        if not embedding_settings:
            return {"hits": []}
        query_vector = embed_texts([query], embedding_settings)[0]
        rank_limit = max(60, limit)
        semantic, documents = self.store.search(
            course_id, source, exam_id, query_vector, embedding_settings, limit=rank_limit,
        )
        if not documents:
            return {"hits": []}
        return {
            "hits": self._rank_documents(query, semantic, documents, limit)
        }

    def _rank_documents(
        self, query: str, semantic: list[dict],
        documents: list[dict], limit: int,
    ) -> list[dict]:
        lexical = self._lexical_rank(
            query, documents, limit=max(60, limit),
        )
        fused = {}
        by_id = {row["id"]: row for row in documents}
        for weight, ranked in ((0.7, semantic), (0.3, lexical)):
            for rank, row in enumerate(ranked, start=1):
                document_id = row["id"]
                fused[document_id] = fused.get(document_id, 0.0) + weight / (60 + rank)
        ordered = sorted(fused, key=lambda document_id: fused[document_id], reverse=True)
        semantic_scores = {row["id"]: row["_semantic_score"] for row in semantic}
        return [
            {
                **by_id[document_id],
                "rank_score": fused[document_id],
                "semantic_score": semantic_scores.get(document_id),
            }
            for document_id in ordered[:limit]
        ]

    @staticmethod
    def _terms(text: str) -> list[str]:
        import jieba

        return [
            term.lower() for term in jieba.lcut(text)
            if term.strip() and any(char.isalnum() for char in term)
        ]

    def _lexical_rank(self, query: str, documents: list[dict], limit: int) -> list[dict]:
        terms = set(self._terms(query))
        if not terms:
            return []
        tokenized = [Counter(self._terms(str(row.get("content") or ""))) for row in documents]
        doc_count = len(documents)
        avg_length = sum(sum(counts.values()) for counts in tokenized) / max(doc_count, 1)
        frequency = {
            term: sum(term in counts for counts in tokenized) for term in terms
        }
        ranked = []
        for row, counts in zip(documents, tokenized):
            length = sum(counts.values())
            score = 0.0
            for term in terms:
                tf = counts.get(term, 0)
                if not tf:
                    continue
                idf = math.log(1 + (doc_count - frequency[term] + 0.5) / (frequency[term] + 0.5))
                score += idf * (tf * 2.2) / (
                    tf + 1.2 * (0.25 + 0.75 * length / max(avg_length, 1))
                )
            if score > 0:
                ranked.append((score, row))
        ranked.sort(key=lambda item: item[0], reverse=True)
        return [row for _, row in ranked[:limit]]

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
    """Insert parsed document chunks into a local FAISS store."""

    def __init__(
        self,
        name: str,
        mineru_settings: dict | None = None,
        embedding_settings: dict | None = None,
        embedding_model_id: str | None = None,
    ):
        super().__init__(name)
        self.description = "Insert parsed document chunks into FAISS"
        self.store = FaissDocumentStore()
        self.timeout_seconds = 300
        self.mineru_settings = dict(mineru_settings or {})
        self.embedding_settings = dict(embedding_settings or {})
        self.embedding_model_id = str(embedding_model_id or "").strip()
        self.fileParser = FileParserTool(self.mineru_settings, "file_parser")

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
            if isinstance(chunksList, dict) and chunksList.get("ok") is False:
                raise RuntimeError(
                    str(chunksList.get("error_message") or "file parsing failed")
                )
        else:
            chunksList = data

        if chunk_mode == "ai_toc":
            return chunksList

        if not chunksList:
            return "没有可插入的文档。"

        embedding = self._resolve_embedding_settings()
        if not self.embedding_model_id:
            raise ValueError("EMBEDDING_MODEL_ID_REQUIRED")
        documents = []
        for chunks in chunksList:
            for chunk in chunks:
                if not self.is_meaningful_text(chunk):
                    continue
                documents.append(
                    {
                        "id": str(uuid.uuid4()),
                        "source": source,
                        "course_id": course_id,
                        "exam_id": exam_id,
                        "upload_batch_id": upload_batch_id,
                        "chunk_order": len(documents) + 1,
                        "content": chunk,
                    }
                )
        if not documents:
            raise ValueError("NO_DOCUMENTS_INSERTED")
        vectors = await asyncio.to_thread(
            embed_texts, [row["content"] for row in documents], embedding,
        )
        model = {
            "model_id": self.embedding_model_id,
            "model_name": embedding["model_name"],
            "model_url": embedding["model_url"],
            "dimensions": embedding["dimensions"],
            "embedding_max_bytes": embedding["embedding_max_bytes"],
        }
        await asyncio.to_thread(
            self.store.insert, course_id, source, exam_id, documents, vectors, model, reload,
        )
        inserted_count = len(documents)
        return f"\u6210\u529f\u63d2\u5165 {inserted_count} \u6761\u6587\u6863"

    def _resolve_embedding_settings(self) -> dict:
        settings = dict(self.embedding_settings)
        for key in ("model_name", "model_url", "model_api_key"):
            if not str(settings.get(key) or "").strip():
                raise ValueError(f"EMBEDDING_{key.upper()}_NOT_CONFIGURED")
            settings[key] = str(settings[key]).strip()
        try:
            default_dimensions = 1024 if settings["model_name"] == "embedding-2" else 2048
            dimensions = int(settings.get("dimensions") or default_dimensions)
        except (TypeError, ValueError) as exc:
            raise ValueError("EMBEDDING_DIMENSIONS_INVALID") from exc
        if dimensions <= 0:
            raise ValueError("EMBEDDING_DIMENSIONS_INVALID")
        settings["dimensions"] = dimensions
        try:
            max_bytes = int(settings.get("embedding_max_bytes") or 400)
        except (TypeError, ValueError) as exc:
            raise ValueError("EMBEDDING_MAX_BYTES_INVALID") from exc
        if max_bytes <= 0:
            raise ValueError("EMBEDDING_MAX_BYTES_INVALID")
        settings["embedding_max_bytes"] = max_bytes
        return settings

    def get_description(self) -> str:
        return self.description

    def delete_documents_by_batch(
        self,
        course_id: str | None,
        upload_batch_id: str | None,
    ) -> None:
        if upload_batch_id and str(upload_batch_id).strip():
            self.store.delete_by_batch(course_id, str(upload_batch_id).strip())

    def delete_course_documents_by_source(
        self,
        course_id: str | None,
        source: str,
    ) -> None:
        source = str(source or "").strip()
        if not source:
            raise ValueError("source is required")
        self.store.delete_by_source(course_id, source)

    def delete_existing_documents_except_batch(
        self,
        course_id: str | None,
        source: str,
        exam_id: str | None,
        upload_batch_id: str | None,
    ) -> None:
        if not str(exam_id or "").strip():
            raise ValueError("exam_id is required")
        self.store.delete_existing_except_batch(
            course_id, source, str(exam_id).strip(),
            str(upload_batch_id or "").strip() or None,
        )

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
