"""Validate and render the standalone Git analysis report template."""

from __future__ import annotations

import json
import os
import re
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any


FIELD_PATTERN = re.compile(r"\[FIELD:([^\]]+)\]")
FIELD_NAME_PATTERN = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


@dataclass(frozen=True)
class FieldSpec:
    name: str
    kind: str = "text"
    choices: tuple[str, ...] = ()
    columns: int = 0


class ReportTemplate:
    def __init__(self, data: dict[str, Any]):
        modules = data.get("modules")
        if not isinstance(modules, list) or not modules:
            raise ValueError("报告模板缺少 modules")
        self.modules = sorted(modules, key=lambda item: item["sort_order"])
        self.fields: dict[str, FieldSpec] = {}
        for module in self.modules:
            body = module.get("template_body")
            if not isinstance(body, str) or not body.strip():
                raise ValueError(f"模板模块缺少正文：{module.get('module_key')}")
            lines = body.splitlines()
            for index, line in enumerate(lines):
                for match in FIELD_PATTERN.finditer(line):
                    parts = [part.strip() for part in match.group(1).split("|")]
                    name = parts[0]
                    if not FIELD_NAME_PATTERN.fullmatch(name):
                        raise ValueError(f"无效的模板字段名：{name}")
                    if name in self.fields:
                        raise ValueError(f"重复的模板字段名：{name}")
                    directives = parts[1:]
                    kind = "table" if any(part == "type:table" for part in directives) else "text"
                    choices: tuple[str, ...] = ()
                    columns = 0
                    if kind == "table":
                        header = next(
                            (
                                previous
                                for previous in reversed(lines[:index])
                                if previous.startswith("|") and "---" not in previous
                            ),
                            None,
                        )
                        if header is None:
                            raise ValueError(f"表格字段缺少表头：{name}")
                        columns = len(header.strip().strip("|").split("|"))
                    else:
                        enum = next(
                            (
                                re.fullmatch(r"type:enum\((.*)\)", part)
                                for part in directives
                                if part.startswith("type:enum(")
                            ),
                            None,
                        )
                        if enum is not None:
                            raw_choices = enum.group(1)
                            # A free-form example containing Chinese commas is guidance, not a finite enum.
                            if "," in raw_choices:
                                choices = tuple(choice.strip() for choice in raw_choices.split(","))
                    self.fields[name] = FieldSpec(name, kind, choices, columns)

    @classmethod
    def from_file(cls, path: Path) -> "ReportTemplate":
        return cls(json.loads(path.read_text(encoding="utf-8")))

    def initial_report(self) -> str:
        """Create one Markdown document with every template placeholder intact."""
        return "\n\n".join(
            module["template_body"].strip() for module in self.modules
        ) + "\n"

    @staticmethod
    def remaining_fields(report: str) -> list[str]:
        return sorted({
            match.group(1).split("|", 1)[0].strip()
            for match in FIELD_PATTERN.finditer(report)
        })

    def document_instruction(self, task: str, report_path: Path) -> str:
        rules = [
            {
                "module_key": module["module_key"],
                "template_prompt": module.get("template_prompt", ""),
            }
            for module in self.modules
        ]
        return "\n\n".join([
            task,
            f"报告模板已创建在 {report_path}。请直接修改这份 Markdown 文档。",
            "先查询远程分支并选择合适分支，调用 git_repository 获取仓库，再用文件和 Git 工具取得证据。",
            "调用 read_report 阅读现有模板；使用 rewriteDocument 将每个完整的 [FIELD:...] 占位符"
            "替换为实际分析内容。可分批修改，不要改动报告标题、章节结构和表头。",
            "表格占位符请替换为 Markdown 表格数据行；缺少证据时填写“未发现证据”，"
            "工具结果显示 truncated 时不得当作完整统计，不要编造事实。",
            "完成后调用 check_report 查看未填写字段；如仍有占位符，继续修改。"
            "最后调用 read_report 回读关键段落并自行检查内容和格式。",
            "最终回复简述已写入的报告路径、选用分支与未能查明的事项；不要输出 fields JSON。",
            "各模块分析规则：" + json.dumps(rules, ensure_ascii=False),
        ])

    @staticmethod
    def write_report(report: str, current_directory: Path) -> Path:
        cwd = current_directory.resolve()
        report_dir = cwd / "AIreport"
        if report_dir.exists() and report_dir.resolve() != report_dir:
            raise ValueError("AIreport 不能是指向其他目录的符号链接")
        report_dir.mkdir(exist_ok=True)
        target = report_dir / "report.md"
        temporary: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w", encoding="utf-8", newline="\n",
                prefix=".report.", suffix=".tmp", dir=report_dir, delete=False,
            ) as stream:
                temporary = stream.name
                stream.write(report)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, target)
            temporary = None
        finally:
            if temporary is not None:
                os.unlink(temporary)
        return target
