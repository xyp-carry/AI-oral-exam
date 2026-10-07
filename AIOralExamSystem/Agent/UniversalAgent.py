"""General-purpose, scoped file and Git agent.

The caller binds identity and repository URL when constructing the agent. The
model can select a branch, but cannot choose another user's cache directory.
"""

from __future__ import annotations

import asyncio
import fnmatch
import json
import os
from pathlib import Path
from typing import Any, Literal

from langchain_core.tools import tool
from pydantic import BaseModel, Field

from AIOralExamSystem.Agent.base_Agent import BaseAgent
from AIOralExamSystem.Agent.scope_binding import ScopeBinding
from AIOralExamSystem.Agent.universal_prompt_builder import UniversalPromptBuilder
from AIOralExamSystem.Graph.dag_task import DagTaskInput, DagTaskRunner, SharedDagContext
from AIOralExamSystem.Tool.files.filesystem_tools import (
    FILESYSTEM_TOOL_DESCRIPTIONS,
    FileInfoInput,
    ListDirectoryInput,
    ProjectFileTool,
    ReadFileInput,
    ReadHeadInput,
    ReadLinesInput,
    ReadTailInput,
    SearchFilesInput,
    TreeInput,
)
from AIOralExamSystem.Tool.files.info_search_tool import (
    FileReadTool,
    FileReadToolInput,
    FileReadDescription,
    InfoSearchTool,
    InfoSearchToolInput,
    InfoSearchDescription,
)
from AIOralExamSystem.Tool.files.documentoutput import RewriteTool, TextReplacement
from AIOralExamSystem.report_template import ReportTemplate
from AIOralExamSystem.Tool.files.project_fs import MAX_ENTRIES, FileToolError, ProjectFiles
from AIOralExamSystem.Tool.git.git_tool import (
    GitHistoryTool,
    GitRemoteBranchesTool,
    GitRepositoryTool,
)


class _BoundReportRewriteTool(RewriteTool):
    """Allow the existing replacement logic to edit exactly one trusted report."""

    def __init__(self, path: Path):
        super().__init__("universal_report_rewrite")
        self.bound_path = path

    def _resolve_file_path(self, file_path: str) -> Path | None:
        requested = Path(file_path)
        if requested != self.bound_path or requested.is_symlink():
            return None
        try:
            resolved = requested.resolve(strict=True)
        except OSError:
            return None
        return resolved if resolved == self.bound_path and resolved.is_file() else None


class CloneRepositoryInput(BaseModel):
    branch: str | None = Field(
        default=None,
        description="要克隆的远程分支；不填时使用查询到的默认分支。",
    )


class GitHistoryInput(BaseModel):
    mode: str = Field(default="history", description="history 或 commit_detail")
    commit_hash: str | None = Field(default=None, description="commit_detail 模式的提交 ID")
    since: str | None = Field(default=None, description="history 模式的起始时间，使用 ISO 8601 格式")


class RecordOralQuestionsInput(BaseModel):
    questions: list[dict] = Field(
        description="口试问题列表。A 类问题包含 dimension、Question、standard_answer；"
                    "C 类问题包含 module_name、question、Answer、aspect、source。"
    )


class FolderStatsInput(BaseModel):
    path: str = Field(default=".", description="当前选中分支仓库内要统计的目录；默认为仓库根目录。")
    operation: Literal["count", "full"] = Field(
        default="count",
        description="count 按可选类型过滤且受 max_entries 限制；full 完整统计代码、文档和其他普通文件。",
    )
    file_type: list[str] | None = Field(
        default=None,
        description="count 操作可用的文件名或扩展名过滤规则；不填时统计所有普通文件。",
    )
    max_entries: int = Field(
        default=MAX_ENTRIES, ge=1, le=MAX_ENTRIES,
        description="count 操作最多遍历的目录条目数；达到上限时结果标记 truncated。",
    )


_CODE_FILE_SUFFIXES = frozenset({
    "c", "cc", "cpp", "cxx", "h", "hpp", "py", "rs", "go", "java",
    "js", "ts", "tsx", "sh", "bat", "ps1", "cmake", "sql",
})
_CODE_FILE_NAMES = frozenset({"makefile", "kconfig", "cmakelists.txt", "dockerfile"})
_DOC_FILE_SUFFIXES = frozenset({"md", "markdown", "txt", "rst", "doc", "docx", "pdf"})


def _full_file_counts(folder: Path) -> dict[str, int]:
    counts = {"total_files": 0, "code_files": 0, "doc_files": 0, "other_files": 0}
    pending = [folder]
    while pending:
        with os.scandir(pending.pop()) as children:
            for entry in children:
                if entry.is_dir(follow_symlinks=False):
                    pending.append(Path(entry.path))
                elif entry.is_file(follow_symlinks=False):
                    name = entry.name.casefold()
                    suffix = Path(name).suffix.lstrip(".")
                    if name in _CODE_FILE_NAMES or suffix in _CODE_FILE_SUFFIXES:
                        category = "code_files"
                    elif suffix in _DOC_FILE_SUFFIXES:
                        category = "doc_files"
                    else:
                        category = "other_files"
                    counts["total_files"] += 1
                    counts[category] += 1
    return counts


class UniversalAgent(BaseAgent):
    """Use file and Git tools within one user/course/exam repository cache."""

    def __init__(
        self,
        model_settings: dict,
        user_id: str,
        course_id: str,
        exam_id: str,
        repository_url: str | None = None,
        branch: str | None = None,
        thinking: bool = False,
        temperature: float = 0,
        show_tool_io: bool = False,
        tool_event_callback=None,
        dag_model_settings: dict | None = None,
        report_path: str | Path | None = None,
        model_round_callback=None,
    ):
        for key, value in (
            ("user_id", user_id),
            ("course_id", course_id),
            ("exam_id", exam_id),
        ):
            if not str(value or "").strip():
                raise ValueError(f"{key} is required")

        # BaseAgent calls get_tools() in its constructor, so bind scope first.
        self.user_id = str(user_id).strip()
        self.course_id = str(course_id).strip()
        self.exam_id = str(exam_id).strip()
        self.repository_url = str(repository_url or "").strip() or None
        self.requested_branch = str(branch or "").strip() or None
        self.branch_info: dict | None = None
        self.active_branch: str | None = None
        self.active_root: Path | None = None
        self.report_path: Path | None = None
        self.report_write_count = 0
        self.oral_questions: list[dict] = []
        if report_path is not None:
            candidate = Path(report_path).expanduser()
            if candidate.is_symlink():
                raise ValueError("报告文件不能是符号链接")
            candidate = candidate.resolve(strict=True)
            if not candidate.is_file():
                raise ValueError("报告文件不存在或不是普通文件")
            if candidate.name != "report.md" or candidate.parent.name != "AIreport":
                raise ValueError("报告文件必须是 AIreport/report.md")
            self.report_path = candidate
        self._execution_lock = asyncio.Lock()
        # The DAG model uses the main configuration by default, but owns its copy.
        self.dag_model_settings = dict(
            model_settings if dag_model_settings is None else dag_model_settings
        )
        self._dag_context: SharedDagContext | None = None

        cache_tool = GitRepositoryTool("universal_repository_cache")
        project_root = cache_tool._default_storage_root().resolve()
        self.exam_root = (
            project_root
            / "Gitrepositorys"
            / cache_tool._safe_path_part(self.user_id)
            / cache_tool._safe_path_part(self.course_id)
            / cache_tool._safe_path_part(self.exam_id)
        ).resolve()
        try:
            self.exam_root.relative_to(project_root)
        except ValueError as exc:
            raise ValueError("repository cache path escapes the project root") from exc
        self.scope_binding = ScopeBinding(
            user_id=self.user_id,
            course_id=self.course_id,
            exam_id=self.exam_id,
            exam_root=self.exam_root,
            report_path=self.report_path,
        )

        if self.requested_branch and not self.repository_url:
            candidate = (
                self.exam_root / cache_tool._safe_path_part(self.requested_branch)
            ).resolve()
            if candidate.is_dir():
                self.active_root = candidate
                self.active_branch = self.requested_branch

        self.prompt_builder = UniversalPromptBuilder.create()
        universal_model_settings = dict(model_settings)
        universal_model_settings["max_agent_model_calls"] = 100
        super().__init__(
            "UniversalAgent",
            universal_model_settings,
            thinking=thinking,
            response_format=False,
            temperature=temperature,
            show_tool_io=show_tool_io,
            tool_event_callback=tool_event_callback,
            model_round_callback=model_round_callback,
        )
        self.system_prompt = self.prompt_builder.build_system_prompt()

    @staticmethod
    def _json(payload: dict) -> str:
        return json.dumps(payload, ensure_ascii=False)

    @staticmethod
    def _text(value: Any) -> str:
        if isinstance(value, dict):
            messages = value.get("messages") or []
            if messages:
                return UniversalAgent._text(messages[-1])
            if "content" in value:
                return UniversalAgent._text(value["content"])
        content = getattr(value, "content", value)
        if isinstance(content, list):
            return "".join(
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content or "")

    def _bound_report_file(self) -> tuple[Path | None, str | None]:
        if self.scope_binding.report_path is None:
            return None, self._json({"ok": False, "flag": "REPORT_NOT_BOUND"})
        path = self.scope_binding.report_file()
        if path is None:
            return None, self._json({
                "ok": False, "flag": "REPORT_PATH_CHANGED",
                "message": "报告路径不存在或已发生重定向。",
            })
        return path, None

    def _scope(self) -> Path | None:
        return self.scope_binding.repository_directory(self.active_root)

    def _exam_scope(self) -> Path | None:
        return self.scope_binding.exam_directory()

    def _resolve_file_path(self, path: str, root: Path | None) -> tuple[str | None, str | None]:
        if root is None:
            return None, self._json({
                "ok": False,
                "flag": "REPOSITORY_NOT_READY",
                "message": "先调用 git_repository 获取考试目录中的仓库。",
            })
        try:
            resolved = ProjectFiles(scope=root).resolve(path)
        except FileToolError as exc:
            return None, self._json({
                "ok": False,
                "flag": exc.code,
                "message": str(exc),
            })
        return str(resolved), None

    async def _file(self, operation: str, **kwargs) -> str:
        root = self._scope()
        if root is None:
            return self._json({
                "ok": False,
                "flag": "REPOSITORY_NOT_READY",
                "message": "先调用 git_repository 获取或选定仓库分支。",
            })
        result = await ProjectFileTool(
            f"universal_{operation}",
            operation=operation,
            scope_root=root,
        ).execute(**kwargs)
        return result if isinstance(result, str) else self._json(result)

    async def _remote_branches(self) -> dict:
        if not self.repository_url:
            return {
                "ok": False,
                "flag": "REPOSITORY_URL_REQUIRED",
                "message": "构造 Agent 时需要绑定 repository_url。",
            }
        if self.branch_info is not None:
            return self.branch_info
        branch_tool = GitRemoteBranchesTool("universal_git_remote_branches")
        branch_tool.timeout_seconds = 120
        result = await branch_tool.execute(repo_url=self.repository_url)
        data = json.loads(result) if isinstance(result, str) else result
        if isinstance(data, dict) and data.get("ok"):
            self.branch_info = data
            self.update_prompt_context(
                "repository",
                {
                    "branch_count": data.get("branch_count"),
                    "default_branch": data.get("default_branch"),
                },
            )
        return data

    def get_tools(self):
        @tool(args_schema=ReadFileInput, description=FILESYSTEM_TOOL_DESCRIPTIONS["read_file"])
        async def read_file(
            path: str, offset: int = 0, length: int | None = None,
            mode: str = "text",
        ) -> str:
            return await self._file(
                "read_file", path=path, offset=offset, length=length, mode=mode,
            )

        @tool(args_schema=ReadLinesInput, description=FILESYSTEM_TOOL_DESCRIPTIONS["read_lines"])
        async def read_lines(
            path: str, start_line: int = 1, end_line: int | None = None,
        ) -> str:
            return await self._file(
                "read_lines", path=path, start_line=start_line, end_line=end_line,
            )

        @tool(args_schema=SearchFilesInput, description=FILESYSTEM_TOOL_DESCRIPTIONS["search_files"])
        async def search_files(
            pattern: str, path: str = ".", regex: bool = False,
            context: int = 0, case_sensitive: bool = False,
            file_globs: list[str] | None = None, max_matches: int = 200,
            timeout_seconds: int = 10,
        ) -> str:
            return await self._file(
                "search_files", pattern=pattern, path=path, regex=regex,
                context=context, case_sensitive=case_sensitive,
                file_globs=file_globs or [], max_matches=max_matches,
                timeout_seconds=timeout_seconds,
            )

        @tool(args_schema=ListDirectoryInput, description=FILESYSTEM_TOOL_DESCRIPTIONS["list_directory"])
        async def list_directory(
            path: str = ".", recursive: bool = False, pattern: str = "*",
            max_entries: int = 5000,
        ) -> str:
            return await self._file(
                "list_directory", path=path, recursive=recursive,
                pattern=pattern, max_entries=max_entries,
            )

        @tool(args_schema=FileInfoInput, description=FILESYSTEM_TOOL_DESCRIPTIONS["file_info"])
        async def file_info(path: str) -> str:
            return await self._file("file_info", path=path)

        @tool(args_schema=ReadHeadInput, description=FILESYSTEM_TOOL_DESCRIPTIONS["read_head"])
        async def read_head(path: str, n: int = 10) -> str:
            return await self._file("read_head", path=path, n=n)

        @tool(args_schema=ReadTailInput, description=FILESYSTEM_TOOL_DESCRIPTIONS["read_tail"])
        async def read_tail(path: str, n: int = 10) -> str:
            return await self._file("read_tail", path=path, n=n)

        @tool(args_schema=TreeInput, description=FILESYSTEM_TOOL_DESCRIPTIONS["tree"])
        async def tree(
            path: str = ".", depth: int = 2, max_entries: int = 5000,
        ) -> str:
            return await self._file(
                "tree", path=path, depth=depth, max_entries=max_entries,
            )

        @tool(
            args_schema=InfoSearchToolInput,
            description=InfoSearchDescription.format(
                allowed_scope="当前 user_id/course_id/exam_id 对应的考试目录",
            ),
        )
        async def infoSearch(
            query: str, file_globs: list[str] | None = None,
            case_sensitive: bool = False, regex: bool = False,
            context_lines: int = 0, max_matches: int = 200,
        ) -> str:
            root = self._exam_scope()
            if root is None:
                return self._json({
                    "ok": False, "flag": "REPOSITORY_NOT_READY",
                    "message": "先调用 git_repository 获取考试目录中的仓库。",
                })
            result = await InfoSearchTool("universal_info_search", allowed_scope=root).execute(
                scope_path=str(root), query=query, file_globs=file_globs or [],
                case_sensitive=case_sensitive, regex=regex,
                context_lines=context_lines, max_matches=max_matches,
            )
            return result if isinstance(result, str) else self._json(result)

        @tool(
            args_schema=FileReadToolInput,
            description=FileReadDescription.format(
                allowed_scope="当前 user_id/course_id/exam_id 对应的考试目录",
            ),
        )
        async def readFile(
            file_path: str, start_line: int | None = None,
            end_line: int | None = None, max_bytes: int = 200_000,
        ) -> str:
            root = self._exam_scope()
            resolved, error = self._resolve_file_path(file_path, root)
            if error:
                return error
            result = await FileReadTool("universal_legacy_file_read", allowed_scope=root).execute(
                scope_path=str(root), file_path=resolved,
                start_line=start_line, end_line=end_line, max_bytes=max_bytes,
            )
            return result if isinstance(result, str) else self._json(result)

        @tool(description=(
            "查看当前选中分支仓库中指定目录的层级树；path 默认为仓库根目录。"
            "最多展开 20 层，max_entries 限制遍历节点数，truncated 表示节点数达到上限。"
            "include_files/include_dirs 控制普通文件和目录节点是否显示；"
            "符号链接作为 symlink 节点显示，但不会沿链接进入目标目录。"
        ))
        async def folderTree(
            path: str = ".", max_entries: int = 5000,
            include_files: bool = True, include_dirs: bool = True,
        ) -> str:
            result = json.loads(await self._file(
                "tree", path=path, depth=20, max_entries=max_entries,
            ))
            if not result.get("ok"):
                return self._json(result)
            root_node = result.get("data", {}).get("tree")
            if isinstance(root_node, dict):
                def filter_node(node: dict, is_root: bool = False) -> dict | None:
                    kind = node.get("type")
                    if not is_root and (
                        (kind == "file" and not include_files)
                        or (kind == "directory" and not include_dirs)
                    ):
                        return None
                    filtered = dict(node)
                    if isinstance(node.get("children"), list):
                        filtered["children"] = [
                            item for child in node["children"]
                            if (item := filter_node(child)) is not None
                        ]
                    return filtered
                result["data"]["tree"] = filter_node(root_node, is_root=True)
            return self._json(result)

        @tool(
            args_schema=FolderStatsInput,
            description="统计当前选中分支仓库内的普通文件，不计目录和符号链接；operation=count 可按类型过滤，operation=full 完整统计代码、文档和其他文件。",
        )
        async def folderStats(
            path: str = ".", operation: Literal["count", "full"] = "count",
            file_type: list[str] | None = None, max_entries: int = MAX_ENTRIES,
        ) -> str:
            if operation == "full":
                if file_type:
                    return self._json({
                        "ok": False, "error_type": "INVALID_INPUT",
                        "error_message": "full 操作不接受 file_type 过滤。",
                    })
                root = self._scope()
                if root is None:
                    return self._json({
                        "ok": False, "flag": "REPOSITORY_NOT_READY",
                        "message": "先调用 git_repository 获取或选定仓库分支。",
                    })
                try:
                    folder = ProjectFiles(scope=root).resolve(path)
                    if not folder.exists():
                        raise FileToolError("DIRECTORY_NOT_FOUND", "directory does not exist")
                    if not folder.is_dir():
                        raise FileToolError("PATH_IS_NOT_DIRECTORY", "path is not a directory")
                    counts = await asyncio.to_thread(_full_file_counts, folder)
                except FileToolError as exc:
                    return self._json({
                        "ok": False, "error_type": exc.code,
                        "error_message": str(exc), "folder_path": path,
                    })
                except OSError as exc:
                    return self._json({
                        "ok": False, "error_type": "FILESYSTEM_ERROR",
                        "error_message": str(exc), "folder_path": path,
                    })
                return self._json({
                    "ok": True, "mode": "stats", "operation": "full",
                    "folder_path": path, "file_type": [], "file_count": counts["total_files"],
                    **counts, "truncated": False,
                })
            result = json.loads(await self._file(
                "list_directory", path=path, recursive=True,
                max_entries=max_entries,
            ))
            if not result.get("ok"):
                return self._json(result)
            filters = [str(item).strip() for item in file_type or [] if str(item).strip()]
            entries = result.get("data", {}).get("entries") or []
            def matches(name: str) -> bool:
                if not filters:
                    return True
                return any(
                    fnmatch.fnmatch(name, item)
                    or (item.startswith(".") and name.endswith(item))
                    or (not any(char in item for char in "*?.")
                        and name.endswith("." + item))
                    for item in filters
                )
            count = sum(
                1 for entry in entries
                if entry.get("type") == "file" and matches(str(entry.get("name") or ""))
            )
            return self._json({
                "ok": True,
                "mode": "stats",
                "folder_path": path,
                "file_type": filters,
                "file_count": count,
                "truncated": bool(result.get("truncated")),
            })

        @tool(description="查询已绑定仓库的远程分支名称、数量和默认分支，不克隆。")
        async def git_remote_branches() -> str:
            return self._json(await self._remote_branches())

        @tool(
            args_schema=CloneRepositoryInput,
            description="从已绑定仓库 URL 获取指定分支；分支必须来自远程分支列表。",
        )
        async def git_repository(branch: str | None = None) -> str:
            info = await self._remote_branches()
            if not info.get("ok"):
                return self._json(info)
            selected = str(
                self.requested_branch or branch or info.get("default_branch") or ""
            ).strip()
            if not selected:
                return self._json({
                    "ok": False, "flag": "BRANCH_REQUIRED",
                    "message": "远程默认分支不可用，请指定一个远程分支。",
                    "branches": info.get("branches", []),
                })
            if selected not in info.get("branches", []):
                return self._json({
                    "ok": False, "flag": "BRANCH_NOT_FOUND",
                    "message": "指定分支不在远程分支列表中。",
                    "branches": info.get("branches", []),
                })
            cache_tool = GitRepositoryTool("universal_git_repository")
            cache_tool.timeout_seconds = 300
            result = await cache_tool.execute(
                repo_url=self.repository_url,
                user_uuid=self.user_id,
                course_id=self.course_id,
                exam_id=self.exam_id,
                git_branch=selected,
                branch=selected,
                reload=False,
            )
            data = json.loads(result) if isinstance(result, str) else result
            if not isinstance(data, dict) or data.get("mode") != "stored_repository":
                message = (
                    data.get("error") or data.get("error_message") or data.get("message")
                    if isinstance(data, dict) else None
                )
                return self._json({
                    "ok": False, "flag": "GIT_REPOSITORY_FAILED",
                    "message": message or "获取仓库失败",
                })
            expected = cache_tool._repo_cache_root(
                None, self.user_id, self.repository_url,
                self.course_id, self.exam_id, selected,
            ).resolve()
            actual = Path(str(data.get("repository_root") or "")).resolve()
            if actual != expected or not actual.is_dir():
                return self._json({
                    "ok": False, "flag": "REPOSITORY_SCOPE_MISMATCH",
                    "message": "仓库目录与当前用户、课程、考试范围不匹配。",
                })
            self.active_root = actual
            self.active_branch = str(data.get("branch") or selected)
            self.update_prompt_context(
                "repository",
                {
                    "active_branch": self.active_branch,
                },
            )
            return self._json({
                "ok": True,
                "flag": "GIT_REPOSITORY_READY",
                "repository_root": str(actual),
                "branch": self.active_branch,
                "branch_count": info.get("branch_count"),
                "default_branch": info.get("default_branch"),
                "cached": bool(data.get("cached")),
            })

        @tool(
            args_schema=GitHistoryInput,
            description="读取当前仓库的 Git 提交历史或指定提交详情。",
        )
        async def git_history(
            mode: str = "history", commit_hash: str | None = None,
            since: str | None = None,
        ) -> str:
            root = self._scope()
            if root is None:
                return self._json({
                    "ok": False, "flag": "REPOSITORY_NOT_READY",
                    "message": "先调用 git_repository 获取仓库。",
                })
            result = await GitHistoryTool("universal_git_history").execute(
                repo_path=str(root), mode=mode, commit_hash=commit_hash,
                since=since,
            )
            return result if isinstance(result, str) else self._json(result)

        @tool(
            args_schema=DagTaskInput,
            description="规划并执行有依赖关系的任务；所有步骤共享可分层压缩的上下文。",
        )
        async def run_dag_task(task: str) -> str:
            if self._dag_context is None:
                self._dag_context = SharedDagContext(
                    task, self.dag_model_settings.get("max_input_tokens", 12000)
                )
            runner = DagTaskRunner(
                model_settings=self.dag_model_settings,
                tools=self.tools,
                context=self._dag_context,
                scope_description=lambda: self._json({
                    "active_root": bool(self._scope()),
                    "active_branch": self.active_branch,
                    "report_bound": self.scope_binding.report_path is not None,
                }),
            )
            try:
                result = await runner.run(task)
            except Exception as exc:
                result = {
                    "ok": False,
                    "flag": "DAG_TASK_FAILED",
                    "message": f"{type(exc).__name__}: {exc}",
                    "context": await self._dag_context.snapshot(),
                    "step_results": await self._dag_context.result_previews(),
                }
            return self._json(result)

        report_tools = []
        if self.report_path is not None:
            @tool(description="读取已绑定的 Markdown 报告；start_line/end_line 是从 1 开始的行号。")
            async def read_report(start_line: int = 1, end_line: int = 120) -> str:
                path, error = self._bound_report_file()
                if error:
                    return error
                if start_line < 1 or end_line < start_line or end_line - start_line >= 120:
                    return self._json({
                        "ok": False, "flag": "INVALID_LINE_RANGE",
                        "message": "一次最多读取 120 行，行号从 1 开始。",
                    })
                lines = path.read_text(encoding="utf-8").splitlines()
                excerpt = [
                    f"{index + 1}: {line}"
                    for index, line in enumerate(lines)
                    if start_line <= index + 1 <= end_line
                ]
                return self._json({
                    "ok": True, "file_path": str(path),
                    "total_lines": len(lines), "content": "\n".join(excerpt),
                })

            @tool(description="仅修改已绑定的报告文件；传入 old_text/new_text 替换列表，无需传路径。")
            async def rewriteDocument(replacements: list[TextReplacement]) -> str:
                path, error = self._bound_report_file()
                if error:
                    return error
                raw = await _BoundReportRewriteTool(path).execute(
                    file_path=str(path), replacements=replacements,
                )
                data = json.loads(raw) if isinstance(raw, str) else raw
                if not isinstance(data, dict):
                    return self._json({
                        "ok": False, "flag": "INVALID_REWRITE_RESULT",
                    })
                if data.get("ok"):
                    self.report_write_count += 1
                    return self._json({
                        "ok": True, "flag": "REPORT_UPDATED",
                        "file_path": str(path),
                        "replacement_count": data.get("replacement_count"),
                        "replaced_count": data.get("replaced_count"),
                    })
                return self._json(data)

            @tool(description="检查报告中仍未填写的 FIELD 占位符；修改后调用。")
            async def check_report() -> str:
                path, error = self._bound_report_file()
                if error:
                    return error
                remaining = ReportTemplate.remaining_fields(
                    path.read_text(encoding="utf-8")
                )
                return self._json({
                    "ok": True, "file_path": str(path),
                    "write_count": self.report_write_count,
                    "remaining_count": len(remaining),
                    "remaining_fields": remaining,
                })

            report_tools = [read_report, rewriteDocument, check_report]

        @tool(
            args_schema=RecordOralQuestionsInput,
            description="记录基于当前仓库证据生成的口试问题；A 类按评分维度，C 类按核心模块。",
        )
        async def record_oral_questions(questions: list[dict]) -> str:
            if self._scope() is None:
                return self._json({
                    "ok": False, "flag": "REPOSITORY_NOT_READY",
                    "message": "请先选择并获取仓库分支。",
                })
            valid = [
                dict(item) for item in questions
                if isinstance(item, dict)
                and str(item.get("Question") or item.get("question") or "").strip()
            ]
            if not valid:
                return self._json({
                    "ok": False, "flag": "NO_VALID_QUESTIONS",
                    "message": "至少需要一道非空问题。",
                })
            self.oral_questions = valid[:20]
            return self._json({
                "ok": True, "flag": "ORAL_QUESTIONS_RECORDED",
                "question_count": len(self.oral_questions),
            })

        return [
            read_file, read_lines, search_files, list_directory,
            file_info, read_head, read_tail, tree,
            infoSearch, readFile, folderTree, folderStats,
            git_remote_branches, git_repository, git_history,
            run_dag_task, record_oral_questions, *report_tools,
        ]

    async def execute(self, user_prompt: str) -> dict:
        async with self._execution_lock:
            current_task = UniversalPromptBuilder.build_current_task(
                user_prompt,
                repository_url=self.repository_url,
                requested_branch=self.requested_branch,
            )
            UniversalPromptBuilder.update_runtime(
                self.prompt_builder,
                active_branch=self.active_branch if self._scope() else None,
            )
            self.system_prompt = self.prompt_builder.build_system_prompt()
            self._dag_context = SharedDagContext(
                current_task, self.dag_model_settings.get("max_input_tokens", 12000)
            )
            response = await self.agent.ainvoke(
                {"messages": self.build_prompt_messages(current_task)},
                config={"recursion_limit": 256},
            )
            dag_context_snapshot = await self._dag_context.snapshot()
            dag_step_outputs = await self._dag_context.raw_results()
        return {
            "answer": self._text(response),
            "repository_root": str(self.active_root) if self.active_root else None,
            "branch": self.active_branch,
            "branch_count": (self.branch_info or {}).get("branch_count"),
            "default_branch": (self.branch_info or {}).get("default_branch"),
            "dag_context": dag_context_snapshot,
            "dag_step_outputs": dag_step_outputs,
            "report_write_count": self.report_write_count,
            "oral_questions": list(self.oral_questions),
        }
