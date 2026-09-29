"""Persistent FAISS indexes and JSON metadata for RAG documents."""
from __future__ import annotations

import hashlib
import json
import os
import uuid
from contextlib import contextmanager
from pathlib import Path

import faiss
import numpy as np
import requests


def embed_texts(texts: list[str], settings: dict, batch_size: int = 10) -> np.ndarray:
    """Call the configured embedding API directly and validate every returned vector."""
    dimension = int(settings["dimensions"])
    max_bytes = int(settings.get("embedding_max_bytes") or 400)
    if max_bytes <= 0:
        raise ValueError("EMBEDDING_MAX_BYTES_INVALID")
    vectors = []
    for start in range(0, len(texts), batch_size):
        batch = [
            text.strip().encode("utf-8")[:max_bytes].decode("utf-8", "ignore")
            for text in texts[start:start + batch_size]
        ]
        if any(not text for text in batch):
            raise ValueError(f"EMBEDDING_INPUT_EMPTY batch_start={start}")
        request_body = {"model": settings["model_name"], "input": batch}
        if settings["model_name"] == "embedding-3":
            request_body["dimensions"] = dimension
        try:
            response = requests.post(
                settings["model_url"],
                headers={"Authorization": f"Bearer {settings['model_api_key']}"},
                json=request_body,
                timeout=(5, 30),
            )
            response.raise_for_status()
            payload = response.json()
        except requests.RequestException as exc:
            detail = ""
            if getattr(exc, "response", None) is not None:
                detail = str(exc.response.text)[:500]
            raise RuntimeError(
                f"EMBEDDING_REQUEST_FAILED batch_start={start} detail={detail}"
            ) from exc
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list) or len(data) != len(batch):
            raise ValueError(f"EMBEDDING_RESPONSE_COUNT_MISMATCH batch_start={start}")
        ordered = sorted(data, key=lambda item: item.get("index", 0))
        for item in ordered:
            vector = item.get("embedding") if isinstance(item, dict) else None
            if not isinstance(vector, list) or len(vector) != dimension:
                raise ValueError(f"EMBEDDING_DIMENSIONS_MISMATCH batch_start={start}")
            values = np.asarray(vector, dtype=np.float32)
            if not np.all(np.isfinite(values)) or not np.any(values):
                raise ValueError(f"EMBEDDING_VECTOR_INVALID batch_start={start}")
            vectors.append(values)
    if not vectors:
        return np.empty((0, dimension), dtype=np.float32)
    return np.ascontiguousarray(np.stack(vectors), dtype=np.float32)


class FaissDocumentStore:
    """One physical index per (course, source, exam) scope.

    JSON metadata and NumPy vectors are authoritative. The FAISS file is a
    persisted search index rebuilt on mutation. A manifest is swapped last,
    so interrupted writes leave the previous generation readable.
    """

    def __init__(self, root: str | Path | None = None):
        default_root = Path(__file__).resolve().parents[3] / "faiss_data"
        self.root = Path(root or os.environ.get("RAG_FAISS_DIR") or default_root).resolve()

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()

    def _course_dir(self, course_id: str) -> Path:
        course_id = str(course_id or "").strip()
        if not course_id:
            raise ValueError("course_id is required")
        return self.root / self._digest(course_id)

    def _scope_dir(self, course_id: str, source: str, exam_id: str | None) -> Path:
        source = str(source or "").strip()
        if not source:
            raise ValueError("source is required")
        key = json.dumps([source, str(exam_id or "").strip()], ensure_ascii=False)
        return self._course_dir(course_id) / self._digest(key)

    @contextmanager
    def _course_lock(self, course_id: str):
        import fcntl

        course_dir = self._course_dir(course_id)
        course_dir.mkdir(parents=True, exist_ok=True)
        with (course_dir / ".lock").open("a+b") as lock_file:
            fcntl.flock(lock_file.fileno(), fcntl.LOCK_EX)
            try:
                yield course_dir
            finally:
                fcntl.flock(lock_file.fileno(), fcntl.LOCK_UN)

    @staticmethod
    def _read_manifest(scope_dir: Path) -> dict | None:
        path = scope_dir / "manifest.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _load_locked(self, scope_dir: Path) -> tuple[dict | None, list[dict], np.ndarray]:
        manifest = self._read_manifest(scope_dir)
        if manifest is None:
            return None, [], np.empty((0, 0), dtype=np.float32)
        generation = manifest["generation"]
        documents = json.loads(
            (scope_dir / f"documents-{generation}.json").read_text(encoding="utf-8")
        )
        vectors = np.load(scope_dir / f"vectors-{generation}.npy", allow_pickle=False)
        if vectors.ndim != 2 or len(documents) != len(vectors):
            raise RuntimeError("FAISS_METADATA_INCONSISTENT")
        if vectors.shape[1] != int(manifest["dimensions"]):
            raise RuntimeError("FAISS_DIMENSIONS_INCONSISTENT")
        return manifest, documents, vectors

    def _write_locked(
        self,
        scope_dir: Path,
        manifest: dict,
        documents: list[dict],
        vectors: np.ndarray,
    ) -> None:
        scope_dir.mkdir(parents=True, exist_ok=True)
        generation = uuid.uuid4().hex
        dimensions = int(manifest["dimensions"])
        vectors = np.ascontiguousarray(vectors, dtype=np.float32).reshape(-1, dimensions)
        index = faiss.IndexFlatIP(dimensions)
        if len(vectors):
            normalized = vectors.copy()
            faiss.normalize_L2(normalized)
            index.add(normalized)

        doc_path = scope_dir / f"documents-{generation}.json"
        vector_path = scope_dir / f"vectors-{generation}.npy"
        index_path = scope_dir / f"index-{generation}.faiss"
        manifest_path = scope_dir / f"manifest-{generation}.tmp"
        with doc_path.open("w", encoding="utf-8") as stream:
            json.dump(documents, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        with vector_path.open("wb") as stream:
            np.save(stream, vectors, allow_pickle=False)
            stream.flush()
            os.fsync(stream.fileno())
        faiss.write_index(index, str(index_path))
        with index_path.open("rb") as stream:
            os.fsync(stream.fileno())
        next_manifest = {**manifest, "generation": generation}
        with manifest_path.open("w", encoding="utf-8") as stream:
            json.dump(next_manifest, stream, ensure_ascii=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(manifest_path, scope_dir / "manifest.json")
        for pattern in ("documents-*.json", "vectors-*.npy", "index-*.faiss"):
            for candidate in scope_dir.glob(pattern):
                if generation not in candidate.name:
                    candidate.unlink()

    def get_scope_info(self, course_id: str, source: str, exam_id: str | None) -> dict | None:
        with self._course_lock(course_id):
            manifest = self._read_manifest(self._scope_dir(course_id, source, exam_id))
            return manifest if manifest and manifest.get("count", 0) else None

    def list_documents(self, course_id: str, source: str, exam_id: str | None) -> list[dict]:
        with self._course_lock(course_id):
            _, documents, _ = self._load_locked(self._scope_dir(course_id, source, exam_id))
            return sorted(documents, key=lambda row: row["chunk_order"])

    def search(
        self, course_id: str, source: str, exam_id: str | None,
        vector: np.ndarray, expected_model: dict, limit: int = 60,
    ) -> tuple[list[dict], list[dict]]:
        with self._course_lock(course_id):
            scope_dir = self._scope_dir(course_id, source, exam_id)
            manifest, documents, _ = self._load_locked(scope_dir)
            if not manifest or not documents:
                return [], []
            for key in ("model_id", "model_name", "model_url", "dimensions", "embedding_max_bytes"):
                if manifest.get(key) != expected_model.get(key):
                    raise ValueError("EMBEDDING_MODEL_CHANGED_RETRY")
            query = np.ascontiguousarray(vector, dtype=np.float32).reshape(1, -1)
            if query.shape[1] != int(manifest["dimensions"]):
                raise ValueError("EMBEDDING_DIMENSIONS_MISMATCH")
            faiss.normalize_L2(query)
            index = faiss.read_index(str(scope_dir / f"index-{manifest['generation']}.faiss"))
            if index.ntotal != len(documents):
                raise RuntimeError("FAISS_INDEX_INCONSISTENT")
            scores, positions = index.search(query, min(limit, len(documents)))
            ranked = [
                {**documents[int(pos)], "_semantic_score": float(score)}
                for score, pos in zip(scores[0], positions[0])
                if pos >= 0
            ]
            return ranked, documents

    def insert(
        self, course_id: str, source: str, exam_id: str | None,
        documents: list[dict], vectors: np.ndarray, model: dict, reload: bool = False,
    ) -> None:
        if len(documents) != len(vectors):
            raise ValueError("FAISS_DOCUMENT_VECTOR_COUNT_MISMATCH")
        with self._course_lock(course_id):
            scope_dir = self._scope_dir(course_id, source, exam_id)
            previous, old_documents, old_vectors = self._load_locked(scope_dir)
            model_info = {
                "model_id": model["model_id"],
                "model_name": model["model_name"],
                "model_url": model["model_url"],
                "dimensions": int(model["dimensions"]),
                "embedding_max_bytes": int(model.get("embedding_max_bytes") or 400),
            }
            if old_documents and not reload:
                for key, value in model_info.items():
                    if previous.get(key) != value:
                        raise ValueError("EMBEDDING_MODEL_CHANGED_REINDEX_REQUIRED")
            else:
                old_documents = []
                old_vectors = np.empty((0, model_info["dimensions"]), dtype=np.float32)
            next_documents = old_documents + documents
            next_vectors = np.concatenate((old_vectors, vectors), axis=0)
            manifest = {
                "course_id": str(course_id),
                "source": str(source),
                "exam_id": str(exam_id or ""),
                "count": len(next_documents),
                **model_info,
            }
            self._write_locked(
                scope_dir, manifest, next_documents, next_vectors,
            )

    def _matching_scopes(self, course_dir: Path, predicate) -> list[tuple[Path, dict]]:
        scopes = []
        for path in course_dir.iterdir():
            if not path.is_dir():
                continue
            manifest = self._read_manifest(path)
            if manifest and predicate(manifest):
                scopes.append((path, manifest))
        return scopes

    def _delete_matching(self, course_id: str, scope_predicate, document_predicate) -> None:
        with self._course_lock(course_id) as course_dir:
            for scope_dir, _ in self._matching_scopes(course_dir, scope_predicate):
                previous, documents, vectors = self._load_locked(scope_dir)
                keep = [i for i, row in enumerate(documents) if not document_predicate(row)]
                if len(keep) == len(documents):
                    continue
                kept_documents = [documents[i] for i in keep]
                kept_vectors = vectors[keep]
                manifest = {**previous, "count": len(kept_documents)}
                self._write_locked(
                    scope_dir, manifest, kept_documents, kept_vectors,
                )

    def delete_by_batch(self, course_id: str, batch_id: str) -> None:
        self._delete_matching(
            course_id, lambda manifest: True,
            lambda row: row.get("upload_batch_id") == batch_id,
        )

    def delete_by_source(self, course_id: str, source: str) -> None:
        self._delete_matching(
            course_id, lambda manifest: manifest.get("source") == source,
            lambda row: True,
        )

    def delete_existing_except_batch(
        self, course_id: str, source: str, exam_id: str, batch_id: str | None,
    ) -> None:
        self._delete_matching(
            course_id,
            lambda manifest: manifest.get("source") == source
            and manifest.get("exam_id") == str(exam_id),
            lambda row: row.get("upload_batch_id") != batch_id,
        )
