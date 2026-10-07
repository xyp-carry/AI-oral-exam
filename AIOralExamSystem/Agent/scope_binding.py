"""Server-bound filesystem scope for UniversalAgent tools."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class ScopeBinding:
    user_id: str
    course_id: str
    exam_id: str
    exam_root: Path
    report_path: Path | None = None

    def exam_directory(self) -> Path | None:
        root = self.exam_root.resolve()
        return root if root == self.exam_root and root.is_dir() else None

    def repository_directory(self, active_root: Path | None) -> Path | None:
        if active_root is None:
            return None
        root = active_root.resolve()
        try:
            root.relative_to(self.exam_root)
        except ValueError:
            return None
        return root if root.is_dir() else None

    def report_file(self) -> Path | None:
        path = self.report_path
        if path is None or path.is_symlink() or not path.is_file():
            return None
        return path if path.resolve() == path else None
