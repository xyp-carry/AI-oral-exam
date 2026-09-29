import re
import shutil
from pathlib import Path
from typing import Any


REPORT_PROMPT_MARKER = "--ps--:"
LOCAL_REPORT_TEMPLATE_ROOT = Path(__file__).resolve().parents[1] / "template"
LOCAL_REPORT_TEMPLATE_CONFIGS = {
    "git": {
        "directory": LOCAL_REPORT_TEMPLATE_ROOT / "neihe" / "report",
        "template_name": "默认项目分析模板",
        "question_module_keys": {"03_document_intro"},
    },
    "general": {
        "directory": LOCAL_REPORT_TEMPLATE_ROOT / "general" / "report",
        "template_name": "默认通用模板",
        "question_module_keys": set(),
    },
}


def normalize_template_prompt(value: Any) -> str:
    prompt = str(value or "").strip()
    if not prompt or prompt.startswith(REPORT_PROMPT_MARKER):
        return prompt
    return f"{REPORT_PROMPT_MARKER}{prompt}"


def load_local_report_template(template_type: str) -> dict[str, Any]:
    config = LOCAL_REPORT_TEMPLATE_CONFIGS.get(template_type)
    if config is None:
        raise ValueError(f"Unsupported local report template type: {template_type}")

    template_dir = config["directory"]
    source_files = sorted(
        (
            path
            for path in template_dir.glob("*.md")
            if path.is_file() and re.match(r"^[0-9]+", path.name)
        ),
        key=lambda path: (
            int(re.match(r"^([0-9]+)", path.name).group(1)),
            path.name,
        ),
    )
    if not source_files:
        raise FileNotFoundError(
            f"No local report templates found for type {template_type} in {template_dir}"
        )

    modules = []
    for sort_order, source_file in enumerate(source_files):
        content = source_file.read_text(encoding="utf-8").strip()
        marker_index = content.find(REPORT_PROMPT_MARKER)
        if marker_index >= 0:
            template_body = content[:marker_index].rstrip()
            template_prompt = content[marker_index:].strip()
        else:
            template_body = content
            template_prompt = ""
        module_key = source_file.stem
        modules.append(
            {
                "module_key": module_key,
                "sort_order": sort_order,
                "template_body": template_body,
                "template_prompt": template_prompt,
                "provides_questions": module_key in config["question_module_keys"],
            }
        )

    return {
        "template_type": template_type,
        "template_name": config["template_name"],
        "modules": modules,
    }


def materialize_template_modules(
    work_dir: Path,
    modules: list[dict[str, Any]],
) -> dict[str, Any]:
    source_dir = work_dir / "template_sources"
    working_dir = work_dir / "template_working"
    for target_dir in (source_dir, working_dir):
        if target_dir.exists():
            shutil.rmtree(target_dir)
        target_dir.mkdir(parents=True, exist_ok=True)

    source_files = []
    working_files = []
    metadata = {}
    ordered_modules = sorted(
        [module for module in modules if isinstance(module, dict)],
        key=lambda module: (
            int(module.get("sort_order") or 0),
            str(module.get("module_key") or ""),
        ),
    )
    for index, module in enumerate(ordered_modules, start=1):
        module_key = str(module.get("module_key") or f"module_{index}").strip()
        safe_key = re.sub(r"[^A-Za-z0-9._-]+", "_", module_key).strip("._")
        safe_key = safe_key or f"module_{index}"
        file_name = f"{index:03d}-{safe_key}.md"
        source_file = source_dir / file_name
        working_file = working_dir / file_name
        body = str(module.get("template_body") or "")
        prompt = normalize_template_prompt(module.get("template_prompt"))
        content = body.rstrip()
        if prompt:
            content += f"\n\n{prompt}"
        if content:
            content += "\n"
        source_file.write_text(content, encoding="utf-8")
        working_file.write_text(content, encoding="utf-8")
        source_files.append(str(source_file))
        working_files.append(str(working_file))
        metadata[str(working_file)] = {
            "module_key": module_key,
            "template_prompt": prompt,
            "provides_questions": bool(module.get("provides_questions")),
            "sort_order": int(module.get("sort_order") or index),
        }

    return {
        "source_dir": str(source_dir),
        "working_dir": str(working_dir),
        "source_files": source_files,
        "working_files": working_files,
        "metadata": metadata,
    }
