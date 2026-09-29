import re
from pathlib import Path


REPORT_WORK_ROOT = Path("/root/AI-Oral-exam/.report_work").resolve(strict=False)
REPORT_FILE_NAME = "report.md"


def safe_report_component(value: str | None, fallback: str) -> str:
    component = re.sub(
        r"[^A-Za-z0-9._-]+",
        "_",
        str(value or "").strip(),
    ).strip(" .")
    if component and component not in {".", ".."}:
        return component
    return fallback


def resolve_report_work_dir(
    course_id: str | None,
    exam_id: str | None,
) -> Path:
    work_dir = (
        REPORT_WORK_ROOT
        / safe_report_component(course_id, "unknown_course")
        / safe_report_component(exam_id, "unknown_exam")
    ).resolve(strict=False)
    _require_within(work_dir, REPORT_WORK_ROOT)
    return work_dir


def resolve_exam_report_path(
    course_id: str | None,
    exam_id: str | None,
) -> Path:
    work_dir = resolve_report_work_dir(course_id, exam_id)
    report_path = (work_dir / REPORT_FILE_NAME).resolve(strict=False)
    _require_within(report_path, work_dir)
    return report_path


def _require_within(path: Path, parent: Path) -> None:
    try:
        path.relative_to(parent)
    except ValueError as exc:
        raise ValueError("REPORT_PATH_INVALID") from exc
