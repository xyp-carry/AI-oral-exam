import fnmatch
import json
from pathlib import Path

from AIOralExamSystem.Tool.base_tool import BaseTool


PROJECT_ROOT = Path("/root/AI-Oral-exam").resolve()


class FolderTreeTool(BaseTool):
    """Read or count project-scoped folder content, including hidden files."""

    def __init__(self, name: str):
        super().__init__(name)
        self.description = "Read a project-scoped folder tree or count files by type."

    def _run(
        self,
        folder_path: str,
        max_entries: int = 5000,
        include_files: bool = True,
        include_dirs: bool = True,
    ) -> str:
        return self.get_folder_tree(
            folder_path,
            max_entries=max_entries,
            include_files=include_files,
            include_dirs=include_dirs,
        )

    def get_folder_tree(
        self,
        folder_path: str | Path,
        max_entries: int = 5000,
        include_files: bool = True,
        include_dirs: bool = True,
    ) -> str:
        path = folder_path if isinstance(folder_path, Path) else self._resolve_project_path(folder_path)
        if path is None:
            return self._json_error("PATH_OUTSIDE_PROJECT", str(folder_path), mode="tree")
        if not path.exists():
            return self._json_error("FOLDER_NOT_FOUND", str(path), mode="tree")
        if not path.is_dir():
            return self._json_error("PATH_IS_NOT_FOLDER", str(path), mode="tree")

        max_entries = max(1, int(max_entries or 5000))
        counter = {"files": 0, "dirs": 0, "entries": 0, "truncated": False}
        tree = self._build_tree(
            path,
            path,
            depth=0,
            max_entries=max_entries,
            include_files=include_files,
            include_dirs=include_dirs,
            counter=counter,
        )

        return json.dumps(
            {
                "ok": True,
                "mode": "tree",
                "project_root": str(PROJECT_ROOT),
                "folder_path": str(path),
                "max_entries": max_entries,
                "truncated": counter["truncated"],
                "total_files": counter["files"],
                "total_dirs": max(0, counter["dirs"] - 1),
                "total_entries": max(0, counter["entries"] - 1),
                "tree": tree.get("children", []) if tree else [],
            },
            ensure_ascii=False,
        )

    def get_file_stats(self, folder_path: str | Path, file_type: list[str] | None = None) -> str:
        path = folder_path if isinstance(folder_path, Path) else self._resolve_project_path(folder_path)
        if path is None:
            return self._json_error("PATH_OUTSIDE_PROJECT", str(folder_path), mode="stats")
        if not path.exists():
            return self._json_error("FOLDER_NOT_FOUND", str(path), mode="stats")
        if not path.is_dir():
            return self._json_error("PATH_IS_NOT_FOLDER", str(path), mode="stats")

        filters = self._normalize_file_type_filters(file_type)
        file_count = 0
        for file_path in path.rglob("*"):
            if not file_path.is_file():
                continue
            if filters and not self._matches_file_type(file_path, filters):
                continue
            file_count += 1

        return json.dumps(
            {
                "ok": True,
                "mode": "stats",
                "project_root": str(PROJECT_ROOT),
                "folder_path": str(path),
                "file_type": [file_type] if isinstance(file_type, str) else file_type or [],
                "file_filters": filters,
                "file_count": file_count,
            },
            ensure_ascii=False,
        )

    def _resolve_project_path(self, folder_path: str) -> Path | None:
        if not folder_path:
            return None
        raw_path = Path(str(folder_path)).expanduser()
        if not raw_path.is_absolute():
            raw_path = PROJECT_ROOT / raw_path
        path = raw_path.resolve()
        try:
            path.relative_to(PROJECT_ROOT)
        except ValueError:
            return None
        return path

    def _build_tree(
        self,
        path: Path,
        root: Path,
        depth: int,
        max_entries: int,
        include_files: bool,
        include_dirs: bool,
        counter: dict,
    ) -> dict | None:
        if counter["entries"] >= max_entries:
            counter["truncated"] = True
            return None

        is_dir = path.is_dir()
        node_type = "directory" if is_dir else "file"
        if node_type == "file" and not include_files:
            return None
        if node_type == "directory" and not include_dirs and depth > 0:
            return None

        counter["entries"] += 1
        if is_dir:
            counter["dirs"] += 1
        else:
            counter["files"] += 1

        node = {
            "name": path.name,
            "path": str(path),
            "relative_path": "." if path == root else str(path.relative_to(root)),
            "type": node_type,
            "is_hidden": self._is_hidden(path, root),
        }

        if is_dir:
            node["children"] = []
            for child in self._iter_children(path):
                child_node = self._build_tree(
                    child,
                    root,
                    depth + 1,
                    max_entries,
                    include_files,
                    include_dirs,
                    counter,
                )
                if child_node is not None:
                    node["children"].append(child_node)
                if counter["truncated"]:
                    break
        else:
            try:
                node["size"] = path.stat().st_size
            except OSError:
                node["size"] = None

        return node

    def _iter_children(self, path: Path) -> list[Path]:
        try:
            return sorted(path.iterdir(), key=lambda item: (not item.is_dir(), item.name.lower()))
        except PermissionError:
            return []

    def _is_hidden(self, path: Path, root: Path) -> bool:
        relative = path.relative_to(root)
        return any(part.startswith(".") for part in relative.parts if part not in {"."})

    def _normalize_file_type_filters(self, file_type: list[str] | str | None) -> list[str]:
        if not file_type:
            return []
        items = [file_type] if isinstance(file_type, str) else file_type
        filters = []
        special_filenames = {"makefile", "kconfig", "dockerfile"}
        for item in items:
            value = str(item or "").strip()
            if not value:
                continue
            lowered = value.lower()
            if "*" in lowered or "?" in lowered:
                filters.append(lowered)
            elif lowered.startswith("."):
                filters.append(f"*{lowered}")
            elif lowered in special_filenames:
                filters.append(lowered)
            elif "." in lowered:
                filters.append(lowered)
            else:
                filters.append(f"*.{lowered}")
        return filters

    def _matches_file_type(self, path: Path, filters: list[str]) -> bool:
        name = path.name.lower()
        return any(fnmatch.fnmatch(name, pattern) for pattern in filters)

    def _json_error(self, error_type: str, folder_path: str, mode: str = "tree") -> str:
        return json.dumps(
            {
                "ok": False,
                "mode": str(mode or ""),
                "error_type": error_type,
                "project_root": str(PROJECT_ROOT),
                "folder_path": str(folder_path or ""),
                "tree": [],
            },
            ensure_ascii=False,
        )


class FolderStatsTool(FolderTreeTool):
    "Count files in a project-scoped folder by optional file type."

    def __init__(self, name: str):
        super().__init__(name)
        self.description = 'Count project-scoped files by type.'

    def _run(
        self,
        folder_path: str,
        file_type: list[str] | None = None,
    ) -> str:
        return self.get_file_stats(folder_path, file_type=file_type)
