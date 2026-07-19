import json
import re
import shutil
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional, TypedDict

from AIOralExamSystem.Agent.base_Agent import BaseAgent
from AIOralExamSystem.Tool.git.git_tool import (
    GitHistoryTool,
    GitHistoryToolInput,
)
from AIOralExamSystem.Tool.files.documentoutput import RewriteTool, TextReplacement
from AIOralExamSystem.Tool.files.folder_tool import FolderStatsTool, FolderTreeTool
from AIOralExamSystem.Tool.files.info_search_tool import (
    FileReadTool,
    FileReadToolInput,
    InfoSearchTool,
    InfoSearchToolInput,
)
from langchain_core.tools import tool
from pydantic import BaseModel, Field


FilePlanAction = Literal["read_indexed_chunks", "info_search", "file_read", "folder_tree"]
TEMPLATE_DIR = Path(__file__).resolve().parents[1] / "template"
REPORT_TEMPLATE_NAME = "report_demo.md"
DEFAULT_REPORT_NAME = "report.md"
TOOL_CALL_INPUT_LOG_PATH = Path("/root/AI-Oral-exam/tool_call_inputs.jsonl")


def read_template_text(template_name: str) -> str:
    template_path = TEMPLATE_DIR / template_name
    try:
        return template_path.read_text(encoding="utf-8").strip()
    except OSError:
        return ""


class FileReadGraphState(TypedDict, total=False):
    user_requirement: str
    file_path: str
    user_name: str
    course_id: str | None
    exam_id: str | None
    folder_path: str
    template_name: str
    max_entries: int
    target_tokens: int
    report_path: str
    max_iterations: int
    iteration: int
    template_source_dir: str
    template_work_dir: str
    source_template_files: list[str]
    template_files: list[str]
    template_index: int
    current_template_file: str
    current_source_template_file: str
    current_template_name: str
    current_template_content: str
    chapter_history: list[dict[str, Any]]
    chapter_done_plan_start: int
    merged_report_path: str
    finish_reason: str
    plan: list[dict[str, Any]]
    done_plan: list[dict[str, Any]]
    final_answer: dict[str, Any] | None
    status: str
    error: dict[str, Any] | None
    review_result: dict[str, Any] | None
    needs_core_question_tool: bool
    core_question_sets: list[dict[str, Any]]
    file_not_found_replanned: bool
    file_not_found_followup_pending: bool





class RunnerRewriteDocumentInput(BaseModel):
    replacements: list[TextReplacement] = Field(
        min_length=1,
        description="包含 old_text 和 new_text 的替换条目",
    )


RunnerRewriteDocumentDescription = "使用 old_text 和 new_text 替换内容并改写文档。"

class FolderTreeToolInput(BaseModel):
    folder_path: str = Field(description="项目目录路径")
    max_entries: int = Field(default=3000, description="最多返回的目录条目数量")
    include_files: bool = Field(default=True, description="是否包含文件")
    include_dirs: bool = Field(default=True, description="是否包含目录")


class FolderStatsToolInput(BaseModel):
    folder_path: str = Field(description="项目目录路径")
    file_type: Optional[list[str]] = Field(default=None, description="Optional file type filters. Empty means all files; examples: ['py'], ['.py', '.md'], ['*.json', 'Makefile']")


FolderTreeToolDescription = "读取项目目录树。"
FolderStatsToolDescription = "统计项目文件和目录数量。"

RunnerInfoSearchDescription = "搜索与当前步骤相关的项目材料。"
RunnerFileReadDescription = "读取允许范围内的项目文件或指定行区间。"
RunnerReadDocumentDescription = "无需参数，读取当前目标文档的完整内容。"
RunnerGitHistoryDescription = "读取相关的 Git 历史记录。"


class ReviewerAgent(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        thinking: bool = False,
        response_format: bool = True,
        temperature: float = 0,
        outerprompt: str | None = None,
        show_tool_io: bool = False,
    ):
        self.outerprompt = outerprompt or ""
        super().__init__(
            "ReviewerAgent",
            model_settings,
            thinking,
            response_format,
            temperature,
            show_tool_io=show_tool_io,
        )
        self.system_prompt = self.build_system_prompt()

    def build_system_prompt(self) -> str:
        return """
你是 ReviewerAgent，只负责判断 AI 生成文档 B 是否符合原始文档模板 A。
A 是唯一权威模板，B 是需要检查的 AI 生成文档。
请检查以下内容：
1. 模板要求的模块和字段是否全部存在。
2. 是否存在空白字段；“未找到”不算空白。
3. B 是否擅自增加了 A 中不存在的模块。
4. 文档内容是否仍然遵循 A 的结构。

你不得修改、补写、删除或重写文档内容。
你只能返回以下 JSON 字段：
{
  "passed": true 或 false,
  "reason": "如果 passed 为 false，说明不通过的具体原因；通过时填写空字符串"
}
"""
    async def execute(self, original_template: str, ai_document: str) -> dict:
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()
        original_template = str(original_template or "")
        ai_document = str(ai_document or "")
        try:
            response = await self.agent.ainvoke(
                {
                    "messages": [
                        {"role": "system", "content": self.system_prompt},
                        {"role": "user", "content": self.build_user_prompt(
                            original_template, ai_document
                        )},
                    ]
                },
            )
            data = self.extract_json_object(self.message_to_text(response))
            passed = data.get("passed")
            if isinstance(passed, str):
                passed = passed.strip().lower() in {"true", "yes", "通过", "行"}
            if not isinstance(passed, bool):
                raise ValueError("ReviewerAgent 返回结果缺少 passed 字段")
            reason = str(data.get("reason") or "").strip()
            return {"passed": passed, "reason": reason}
        except Exception as exc:
            return {
                "passed": False,
                "reason": f"Reviewer 执行失败：{exc.__class__.__name__}: {exc}",
            }
        finally:
            await self.stop_heartbeat()
    def build_user_prompt(self, original_template: str, ai_document: str) -> str:
        return (
            "A 原始文档模板：\n<<<A>>>\n"
            + original_template
            + "\n<<<END A>>>\n\nB AI 生成文档：\n<<<B>>>\n"
            + ai_document
            + "\n<<<END B>>>\n"
            "请只判断 B 是否符合 A，不要修改或重写任何文档内容。\n"
            "如果不通过，请在 reason 中说明具体缺失、空白或新增模块。\n"
        )
    def message_to_text(self, response) -> str:
        if isinstance(response, dict):
            messages = response.get("messages")
            if messages:
                return self.message_to_text(messages[-1])
            if response.get("content") is not None:
                return self.message_to_text(response["content"])
        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content or "")

    def extract_json_object(self, text: str) -> dict:
        cleaned = str(text or "").strip()
        fence = re.search(
            r"```(?:json)?\s*(.*?)\s*```",
            cleaned,
            re.DOTALL | re.IGNORECASE,
        )
        if fence:
            cleaned = fence.group(1).strip()
        try:
            value = json.loads(cleaned)
        except json.JSONDecodeError:
            match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not match:
                raise
            value = json.loads(match.group(0))
        if not isinstance(value, dict):
            raise ValueError("ReviewerAgent 返回结果必须是 JSON 对象")
        return value

    def get_response_format(self):
        class ResponseFormat(BaseModel):
            passed: bool = False
            reason: str = ""

        return ResponseFormat

class FileRunnerAgent(BaseAgent):
    """执行当前计划步骤并使用工具返回简洁结果。"""

    def __init__(
        self,
        model_settings: dict,
        thinking: bool = False,
        response_format: bool = True,
        temperature: float = 0,
        mineru_api_key: str | None = None,
        chunk_ai_model_settings: dict | None = None,
        extra_tools: list | None = None,
        outerprompt: str | None = None,
        allowed_scope_root: str | None = None,
        extra_allowed_roots: list[str] | None = None,
        template_path: str | None = None,
        show_tool_io: bool = False,
    ):
        self.model_settings = dict(model_settings or {})
        self.thinking = thinking
        self.response_format = False
        self.temperature = temperature
        self.mineru_api_key = mineru_api_key
        self.chunk_ai_model_settings = chunk_ai_model_settings or self.model_settings
        self.extra_tools = list(extra_tools or [])
        self.outerprompt = outerprompt.strip() or ""
        self.allowed_scope_root = self.resolve_runner_scope_root(allowed_scope_root)
        self.template_path = template_path
        self.extra_allowed_roots = [
            self.resolve_runner_scope_root(root)
            for root in (extra_allowed_roots or [])
            if str(root or "").strip()
        ]
        super().__init__(
            "FileRunnerAgent",
            model_settings,
            thinking,
            False,
            temperature,
            show_tool_io=show_tool_io,
        )
        self.system_prompt = self.build_system_prompt()

    def resolve_runner_scope_root(self, scope_root: str | None = None) -> Path:
        raw_root = str(scope_root or "").strip()
        if raw_root:
            root_path = Path(raw_root).expanduser()
            if not root_path.is_absolute():
                root_path = Path("/root/AI-Oral-exam/OSjuger") / root_path
        else:
            root_path = Path("/root/AI-Oral-exam/OSjuger")
        return root_path.resolve(strict=False)

    def resolve_runner_tool_path(self, path: str = "", default_path: str = ".") -> dict:
        root_path = self.allowed_scope_root or self.resolve_runner_scope_root(None)
        raw_path = str(path or default_path or ".").strip() or "."
        requested_path = Path(raw_path).expanduser()
        if requested_path.is_absolute():
            resolved_path = requested_path.resolve(strict=False)
        else:
            resolved_path = (root_path / requested_path).resolve(strict=False)

        allowed = False
        matched_root = root_path
        for candidate_root in [root_path, *self.extra_allowed_roots]:
            try:
                resolved_root = candidate_root.resolve(strict=False)
                if resolved_path == resolved_root or resolved_root in resolved_path.parents:
                    allowed = True
                    matched_root = resolved_root
                    break
            except (OSError, ValueError):
                continue
        if not allowed:
            return {
                "ok": False,
                "flag": "RUNNER_TOOL_PATH_DENIED",
                "requested_path": raw_path,
                "reason": "请求路径超出当前项目范围和报告模板工作区。",
                "recoverable": True,
                "next_actions": ["请使用当前项目材料的相对路径，或先调用 folderTree 检查目录。"],
            }
        return {
            "ok": True,
            "flag": "RUNNER_TOOL_PATH_RESOLVED",
            "requested_path": raw_path,
            "resolved_path": str(resolved_path),
            "scope_root": str(matched_root),
        }

    def short_runner_text(self, value, limit: int = 50) -> str:
        if isinstance(value, dict):
            for key in ("flag", "answer", "result_summary", "error_message", "reason", "message"):
                if value.get(key):
                    value = value.get(key)
                    break
            else:
                value = json.dumps(value, ensure_ascii=False)
        elif isinstance(value, list):
            value = json.dumps(value, ensure_ascii=False)
        text = str(value or "").replace("\r", " ").replace("\n", " ").strip()
        text = " ".join(text.split())
        if len(text) > limit:
            return text[:limit] + "..."
        return text


    async def stream_runner_response(self, messages: list[dict]):
        latest_response = None

        async for chunk in self.agent.astream({"messages": messages}):
            latest_response = chunk

        return latest_response["model"] or {"messages": []}


    def build_graph_context_summary(self, state: FileReadGraphState) -> dict:
        return {
            "current_step": state.get("current_step"),
            "previous_execution_count": len(state.get("done_plan") or []),
            "folder_path": state.get("folder_path"),
            "file_path": state.get("file_path"),
            "target_tokens": state.get("target_tokens"),
        }

    def build_system_prompt(self) -> str:
        return '''
        ## 角色
        你是一名优秀的评委助理，现在有一个仓库和一个模板，你需要根据模板对仓库进行评审，可以调用其中的工具，。

        ## 可用工具
        - folderTree：查看项目目录结构。
        - folderStats：统计文件数量。
        - infoSearch：搜索项目材料，定位文件和匹配位置。
        - readFile：读取指定文件或行区间的完整内容。
        - ReadDocument：无需参数，直接读取当前目标文档的完整内容。
        - gitHistoryReader：读取必要的 Git 历史证据。
        - rewriteDocument：根据 file_path 和多个 old_text/new_text 修改文件。

        ## 写入规则
        - 所有文件修改必须使用 rewriteDocument。
        - 必须传入 file_path，目标文件由系统校验。
        - replacements 中每项只能包含 old_text 和 new_text。
        - 一次调用可以包含多个 replacements。
        - 每个 old_text 的所有匹配项都会被替换。
        - 修改模板占位符时，将 [FIELD:...] 作为 old_text。
        - 如果不能找到对应的答案，必须填写“未找到”。
        - 任何写入失败都不得忽略。

        ## 首先需要
        - 执行当前步骤前先调用 ReadDocument() 读取当前目标文档。
        - 修改文档后再次调用 ReadDocument() 检查最终内容。
        ''' + self.outerprompt
    
    def get_tools(self):
        @tool(args_schema=FolderTreeToolInput, description=FolderTreeToolDescription)
        async def folderTree(
            folder_path: str = '',
            max_entries: int = 3000,
            include_files: bool = True,
            include_dirs: bool = True,
        ) -> str:
            path_resolution = self.resolve_runner_tool_path(folder_path, '.')
            if not path_resolution.get('ok'):
                return json.dumps(path_resolution, ensure_ascii=False)
            folder_tool = FolderTreeTool('runner_folder_tree_tool')
            return await folder_tool.execute(
                folder_path=path_resolution['resolved_path'],
                max_entries=max_entries or 3000,
                include_files=include_files,
                include_dirs=include_dirs,
            )

        @tool(args_schema=FolderStatsToolInput, description=FolderStatsToolDescription)
        async def folderStats(
            folder_path: str = '',
            file_type: Optional[list[str]] = None,
        ) -> str:
            path_resolution = self.resolve_runner_tool_path(folder_path, '.')
            if not path_resolution.get('ok'):
                return json.dumps(path_resolution, ensure_ascii=False)
            folder_tool = FolderStatsTool('runner_folder_stats_tool')
            return await folder_tool.execute(
                folder_path=path_resolution['resolved_path'],
                file_type=file_type,
            )

        @tool(args_schema=InfoSearchToolInput, description=RunnerInfoSearchDescription)
        async def infoSearch(
            scope_path: str = ".",
            query: str = "",
            shell_command: Optional[str] = None,
            file_globs: list[str] | None = None,
            case_sensitive: bool = False,
            regex: bool = False,
            context_lines: int = 0,
            max_matches: int = 200,
            timeout_seconds: int = 10,
        ) -> str:
            scope_resolution = self.resolve_runner_tool_path(scope_path, ".")
            if not scope_resolution.get("ok"):
                return json.dumps(scope_resolution, ensure_ascii=False)
            info_search_tool = InfoSearchTool("runner_info_search_tool")
            return await info_search_tool.execute(
                scope_path=scope_resolution["resolved_path"],
                query=query,
                shell_command=shell_command,
                file_globs=file_globs or [],
                case_sensitive=case_sensitive,
                regex=regex,
                context_lines=context_lines,
                max_matches=max_matches,
                timeout_seconds=timeout_seconds,
            )

        @tool(args_schema=FileReadToolInput, description=RunnerFileReadDescription)
        async def readFile(
            scope_path: str = ".",
            file_path: str = "",
            start_line: Optional[int] = None,
            end_line: Optional[int] = None,
            max_bytes: int = 200_000,
        ) -> str:
            scope_resolution = self.resolve_runner_tool_path(scope_path, ".")
            if not scope_resolution.get("ok"):
                return json.dumps(scope_resolution, ensure_ascii=False)
            file_resolution = self.resolve_runner_tool_path(file_path, "")
            if not file_resolution.get("ok"):
                return json.dumps(file_resolution, ensure_ascii=False)
            file_read_tool = FileReadTool("runner_file_read_tool")
            return await file_read_tool.execute(
                scope_path=scope_resolution["resolved_path"],
                file_path=file_resolution["resolved_path"],
                start_line=start_line,
                end_line=end_line,
                max_bytes=max_bytes,
            )
        
        @tool("ReadDocument", description=RunnerReadDocumentDescription)
        async def read_document() -> str:
            target_path = str(self.template_path or "").strip()
            if not target_path:
                return json.dumps({
                    "ok": False,
                    "flag": "RUNNER_TARGET_DOCUMENT_NOT_BOUND",
                    "message": "当前步骤没有绑定目标文档。",
                }, ensure_ascii=False)
            file_resolution = self.resolve_runner_tool_path(target_path, "")
            if not file_resolution.get("ok"):
                return json.dumps(file_resolution, ensure_ascii=False)
            file_read_tool = FileReadTool("runner_document_read_tool")
            return await file_read_tool.execute(
                scope_path=str(self.allowed_scope_root),
                file_path=file_resolution["resolved_path"],
                start_line=None,
                end_line=None,
                max_bytes=200_000,
            )
        
        @tool(args_schema=GitHistoryToolInput, description=RunnerGitHistoryDescription)
        async def gitHistoryReader(
            repo_path: str,
            mode: str = "history",
            commit_hash: Optional[str] = None,
        ) -> str:
            path_resolution = self.resolve_runner_tool_path(repo_path, ".")
            if not path_resolution.get("ok"):
                return json.dumps(path_resolution, ensure_ascii=False)
            git_history_tool = GitHistoryTool("runner_git_history_tool")
            return await git_history_tool.execute(
                repo_path=path_resolution["resolved_path"],
                mode=mode,
                commit_hash=commit_hash,
            )

        @tool(args_schema=RunnerRewriteDocumentInput, description=RunnerRewriteDocumentDescription)
        async def rewriteDocument(
            replacements: list[TextReplacement],
        ) -> str:
            rewrite_tool = RewriteTool('runner_rewrite_tool')
            result = await rewrite_tool.execute(
                file_path=self.template_path,
                replacements=replacements,
            )
            return result if isinstance(result, str) else json.dumps(result, ensure_ascii=False)
        return [folderTree, folderStats, infoSearch, readFile, read_document, gitHistoryReader, rewriteDocument] + self.extra_tools

    async def execute(self, step: dict) -> str:
        if not isinstance(step, dict):
            return ""
        self.template_path = str(
            step.get("current_template_file")
            or step.get("target_file")
            or step.get("file_path")
            or self.template_path
            or ""
        ).strip()
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()
        try:
            user_prompt = self.build_user_prompt(step)
            messages = [
                {"role": "system", "content": self.system_prompt},
                {"role": "user", "content": user_prompt},
            ]
            response = await self.agent.ainvoke({"messages": messages})
            
            response_messages = response.get("messages") or [] if isinstance(response, dict) else getattr(response, "messages", []) or []
            if not response_messages:
                return ""
            summary = self.message_to_text(response_messages[-1]).strip()
            return summary
        except Exception as exc:
            return f"Runner 执行失败：{exc.__class__.__name__}: {exc}"
        finally:
            await self.stop_heartbeat()

    def build_user_prompt(self, step: dict) -> str:
        report_template = Path(
            step.get("current_template_file", "")
        ).read_text(encoding="utf-8") or ""
        if report_template == "":
            raise ValueError("模板文件为空")
        direction = str(step.get("direction") or "").strip()
        return f'''
            当前的目标文档模板为：
            {report_template}

            当前步骤要求：
            {direction}

            执行要求：
            1. 先调用 ReadDocument() 查看当前目标文档。
            2. 如果模板中有 --ps--，按照其中的内容进行解析；如果没有，则按照模板和当前步骤要求执行。
            3. 只能在 {step.get("scope")} 中调用工具读取项目材料。
            4. 每个 replacement 条目必须包含 old_text 和 new_text。
            5. 一次调用可以修改多处文字，每个 old_text 的所有匹配项都会被替换。
            6. 文字不存在或替换范围重叠时不要强行写入。
            7. 修改完成后再次调用 ReadDocument() 检查当前目标文档。
            8. 最后只返回自然语言执行摘要。
        '''
    def normalize_dict_list(self, value) -> list[dict]:
        if isinstance(value, dict):
            return [value]
        if isinstance(value, list):
            return [item for item in value if isinstance(item, dict)]
        return []

    def collect_used_tools(self, response) -> list[str]:
        names = []
        if isinstance(response, dict):
            messages = response.get("messages") or []
        else:
            messages = getattr(response, "messages", []) or []
        for message in messages:
            name = getattr(message, "name", None)
            if name:
                names.append(str(name))
            tool_calls = getattr(message, "tool_calls", None) or []
            for call in tool_calls:
                if isinstance(call, dict) and call.get("name"):
                    names.append(str(call["name"]))
        seen = set()
        result = []
        for name in names:
            if name in seen:
                continue
            seen.add(name)
            result.append(name)
        return result

    def plan_summary(self, plan: dict) -> dict:
        return {
            "ok": plan.get("ok"),
            "flag": plan.get("flag"),
            "goal": plan.get("goal"),
            "done": plan.get("done"),
            "plan_count": len(plan.get("plan") or plan.get("read_plan") or []),
            "notes": plan.get("notes") or [],
        }

    def normalize_sub_plan(self, value) -> list:
        if not isinstance(value, list):
            return []
        result = []
        for index, item in enumerate(value, start=1):
            if not isinstance(item, dict):
                text = str(item).strip()
                if text:
                    result.append(
                        {
                            "step": index,
                            "tool": "",
                            "purpose": text,
                            "status": "done",
                            "result_summary": "",
                        }
                    )
                continue
            result.append(
                {
                    "step": int(item.get("step") or index),
                    "tool": str(item.get("tool") or item.get("action") or "").strip(),
                    "purpose": str(item.get("purpose") or item.get("reason") or "").strip(),
                    "status": str(item.get("status") or "done").strip(),
                    "result_summary": str(item.get("result_summary") or item.get("summary") or "").strip(),
                }
            )
        return result

    def normalize_step_conclusions(self, value) -> list:
        if not isinstance(value, list):
            return []
        result = []
        for item in value:
            if isinstance(item, dict):
                conclusion = str(item.get("conclusion") or "").strip()
                if not conclusion:
                    continue
                result.append(
                    {
                        "step": item.get("step"),
                        "conclusion": conclusion,
                    }
                )
            else:
                text = str(item).strip()
                if text:
                    result.append({"step": None, "conclusion": text})
        return result

    def normalize_list(self, value) -> list:
        if isinstance(value, list):
            return [str(item).strip() for item in value if str(item).strip()]
        if value is None:
            return []
        text = str(value).strip()
        return [text] if text else []

    def message_to_text(self, response) -> str:
        if isinstance(response, dict):
            messages = response.get("messages")
            if messages:
                return self.message_to_text(messages[-1])
            content = response.get("content")
            if content is not None:
                return self.message_to_text(content)

        content = getattr(response, "content", response)
        if isinstance(content, list):
            return "".join(
                str(item.get("text", item)) if isinstance(item, dict) else str(item)
                for item in content
            )
        return str(content or "")

    def extract_json_object(self, text: str) -> dict:
        cleaned = str(text or "").strip()
        fence_match = re.search(r"```(?:json)?\s*(.*?)\s*```", cleaned, re.DOTALL | re.IGNORECASE)
        if fence_match:
            cleaned = fence_match.group(1).strip()
        decoder = json.JSONDecoder(strict=False)
        try:
            data, _ = decoder.raw_decode(cleaned)
        except json.JSONDecodeError:
            json_match = re.search(r"\{.*\}", cleaned, re.DOTALL)
            if not json_match:
                raise
            data, _ = decoder.raw_decode(json_match.group(0).strip())
        if not isinstance(data, dict):
            raise ValueError("FileRunnerAgent 返回结果必须是 JSON 对象")
        return data

    def extract_named_tool_results(self, response, tool_name: str) -> list[dict]:
        messages = response.get('messages') if isinstance(response, dict) else getattr(response, 'messages', None)
        results = []
        for message in messages or []:
            if isinstance(message, dict):
                name = message.get('name')
                content = message.get('content', '')
            else:
                name = getattr(message, 'name', None)
                content = getattr(message, 'content', '')
            if name != tool_name:
                continue
            data = self.loads_tool_json(content)
            if isinstance(data, dict):
                results.append(data)
        return results

    def loads_tool_json(self, content) -> dict | None:
        text = str(content or '').strip()
        if not text:
            return None
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            try:
                data = self.extract_json_object(text)
            except Exception:
                return None
        return data if isinstance(data, dict) else None

    def collect_file_not_found_incidents(self, results: list[dict]) -> list[dict]:
        incidents = []
        for result in results or []:
            if not isinstance(result, dict) or result.get('flag') != 'FILE_NOT_FOUND':
                continue
            incidents.append(self.normalize_file_not_found_incident(result))
        return incidents

    def normalize_file_not_found_incidents(self, value) -> list[dict]:
        if not isinstance(value, list):
            return []
        incidents = []
        for item in value:
            if isinstance(item, dict):
                incidents.append(self.normalize_file_not_found_incident(item))
        return incidents

    def normalize_file_not_found_incident(self, result: dict) -> dict:
        diagnostics = result.get('diagnostics') if isinstance(result.get('diagnostics'), dict) else {}
        reason = str(diagnostics.get('reason') or result.get('reason') or '请求的文件不存在')
        return {
            'flag': 'FILE_NOT_FOUND',
            'file_path': str(result.get('file_path') or diagnostics.get('file_path') or ''),
            'resolved_path': str(result.get('resolved_path') or diagnostics.get('resolved_path') or ''),
            'parent_path': str(diagnostics.get('parent_path') or ''),
            'parent_exists': bool(diagnostics.get('parent_exists', False)),
            'reason': reason,
            'candidate_files': diagnostics.get('candidate_files') or result.get('candidate_files') or [],
        }

    def file_not_found_missing_information(self, incidents: list[dict]) -> list[str]:
        messages = []
        for incident in incidents or []:
            file_path = str(incident.get('file_path') or '')
            reason = str(incident.get('reason') or '')
            messages.append('文件未找到，必须由 Planner 进一步调查：' + file_path)
            if reason:
                messages.append('文件未找到调查原因：' + reason)
        return messages

    def report_file_exists(self, report_path: str) -> bool:
        if not report_path:
            return False
        root_path = Path(str(report_path or '/root/AI-Oral-exam/OSjuger')).expanduser().resolve(strict=False)
        raw_path = Path(str(report_path)).expanduser()
        if not raw_path.is_absolute():
            raw_path = root_path / raw_path
        try:
            resolved_path = raw_path.resolve(strict=False)
            return (resolved_path == root_path or root_path in resolved_path.parents) and resolved_path.is_file()
        except OSError:
            return False

    def get_response_format(self):
        return None













