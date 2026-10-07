"""Agent-facing wrappers for the project-scoped filesystem operations."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from AIOralExamSystem.Tool.base_tool import BaseTool
from AIOralExamSystem.Tool.files.project_fs import (
    FileToolError, ProjectFiles, MAX_ENTRIES, MAX_LINES, MAX_READ_BYTES,
)


FILESYSTEM_TOOL_DESCRIPTIONS = {
    "read_file": (
        "按字节范围读取授权目录内的文件。path 指定文件；offset 从 0 开始，"
        f"length 为正数、单次最多 {MAX_READ_BYTES} 字节；"
        "mode=text 返回文本，mode=binary 返回 Base64 编码内容。"
    ),
    "read_lines": (
        "按行号读取授权目录内的文本文件并返回行号。start_line 从 1 开始，"
        f"end_line 包含末行；单次最多 {MAX_LINES} 行、{MAX_READ_BYTES} 字节。"
    ),
    "search_files": (
        "在授权目录内按文本或正则搜索文件内容，返回匹配行、行号和上下文。"
        "pattern 是查询内容；path 指定文件或目录；regex 控制正则，"
        "file_globs 过滤文件；context 为 0–5，max_matches 为 1–1000，"
        "timeout_seconds 为 1–30 秒。留意结果中的 truncated。"
    ),
    "list_directory": (
        "列出授权目录内的文件和子目录。path 指定目录；recursive 控制递归，"
        "pattern 是 glob 过滤条件；"
        f"max_entries 最多 {MAX_ENTRIES} 个条目。留意结果中的 truncated。"
    ),
    "file_info": (
        "读取授权目录内指定 path 的元信息：类型、大小、修改时间、权限，"
        "若为符号链接还返回链接目标。"
    ),
    "read_head": (
        "读取授权目录内文本文件的前 n 行并返回行号；"
        f"n 为 1–{MAX_LINES}，输出最多 {MAX_READ_BYTES} 字节。"
    ),
    "read_tail": (
        "读取授权目录内文本文件的后 n 行并返回行号；"
        f"n 为 1–{MAX_LINES}，输出最多 {MAX_READ_BYTES} 字节。"
    ),
    "tree": (
        "展示授权目录内指定 path 的目录树。depth 为 0–20，"
        f"max_entries 最多 {MAX_ENTRIES} 个节点；"
        "不递归进入符号链接。留意 truncated 和 depth_limited。"
    ),
}


class ReadFileInput(BaseModel):
    path: str = Field(description="项目内的文件路径")
    offset: int = Field(default=0, description="起始字节偏移，从 0 开始")
    length: int | None = Field(default=None, description="要读取的字节数；单次最多返回 1 MiB")
    mode: Literal["text", "binary"] = Field(default="text", description="文本或 Base64 二进制模式")


class ReadLinesInput(BaseModel):
    path: str = Field(description="项目内的文件路径")
    start_line: int = Field(default=1, description="起始行号，从 1 开始")
    end_line: int | None = Field(default=None, description="结束行号，包含该行")


class SearchFilesInput(BaseModel):
    pattern: str = Field(description="要搜索的文本或正则表达式")
    path: str = Field(default=".", description="项目内的文件或目录")
    regex: bool = Field(default=False, description="是否按正则表达式搜索")
    context: int = Field(default=0, description="匹配行前后的上下文行数，0 到 5")
    case_sensitive: bool = Field(default=False, description="是否区分大小写")
    file_globs: list[str] = Field(default_factory=list, description="可选的文件 glob 过滤")
    max_matches: int = Field(default=200, description="最多返回 1000 条匹配")
    timeout_seconds: int = Field(default=10, description="搜索命令超时，最多 30 秒")


class ListDirectoryInput(BaseModel):
    path: str = Field(default=".", description="项目内的目录")
    recursive: bool = Field(default=False, description="是否递归列出子目录")
    pattern: str = Field(default="*", description="文件名或相对路径的 glob 模式")
    max_entries: int = Field(default=5000, description="最多返回 5000 个条目")


class FileInfoInput(BaseModel):
    path: str = Field(description="要查看元信息的项目内路径")


class ReadHeadInput(BaseModel):
    path: str = Field(description="项目内的文本文件")
    n: int = Field(default=10, description="读取前 N 行，最多 1000 行")


class ReadTailInput(BaseModel):
    path: str = Field(description="项目内的文本文件")
    n: int = Field(default=10, description="读取后 N 行，最多 1000 行")


class TreeInput(BaseModel):
    path: str = Field(default=".", description="项目内的目录")
    depth: int = Field(default=2, description="最大目录深度，0 到 20")
    max_entries: int = Field(default=5000, description="最多返回 5000 个节点")


class ProjectFileTool(BaseTool):
    """A thin BaseTool adapter; the operation name is fixed by the caller."""

    OPERATIONS = frozenset(FILESYSTEM_TOOL_DESCRIPTIONS)

    def __init__(self, name: str, operation: str, scope_root: str | Path | None = None):
        super().__init__(name)
        if operation not in self.OPERATIONS:
            raise ValueError(f"unsupported filesystem operation: {operation}")
        self.operation = operation
        self.scope_root = scope_root
        self.description = FILESYSTEM_TOOL_DESCRIPTIONS[operation]

    def _run(self, **kwargs) -> str:
        try:
            files = ProjectFiles(scope=self.scope_root)
            data = getattr(files, self.operation)(**kwargs)
            payload = {
                "ok": True,
                "tool": self.operation,
                "path": data.get("path"),
                "data": data,
                "truncated": bool(data.get("truncated", False)),
            }
        except FileToolError as exc:
            payload = {
                "ok": False,
                "tool": self.operation,
                "error_type": exc.code,
                "error_message": str(exc),
                "data": {},
                "truncated": False,
            }
        except OSError as exc:
            payload = {
                "ok": False,
                "tool": self.operation,
                "error_type": "FILESYSTEM_ERROR",
                "error_message": str(exc),
                "data": {},
                "truncated": False,
            }
        return json.dumps(payload, ensure_ascii=False)

