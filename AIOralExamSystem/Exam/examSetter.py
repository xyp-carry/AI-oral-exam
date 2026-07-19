import asyncio
import json
import time
from typing import List, Literal, Optional

from AIOralExamSystem.Agent.base_Agent import BaseAgent
from AIOralExamSystem.Tool.codeanalysis.code_ast import (
    CodeAstDescription,
    CodeAstTool,
)
from AIOralExamSystem.Tool.codeanalysis.code_lsp import (
    CodeLspDescription,
    CodeLspTool,
)
from AIOralExamSystem.Tool.codeanalysis.code_reader import (
    CodeReaderDescription,
    CodeReaderTool,
)
from AIOralExamSystem.Tool.rag.data_tool import SearchTool
from langchain_core.tools import tool
from pydantic import BaseModel, Field
from AIOralExamSystem.Prompt.examSetterPrompt import (
    UNAVAILABLE_TOOL_SEPARATOR,
    UNAVAILABLE_FILE_MATERIAL_PROMPT,
    UNAVAILABLE_CODE_MATERIAL_PROMPT,
    REGISTERED_TOOLS_PLACEHOLDER,
    REGISTERED_TOOL_SEPARATOR,
    ALL_LISTED_TOOLS_AVAILABLE_PROMPT,
    AVAILABLE_TOOLS_PROMPT_TEMPLATE,
    DEFAULT_DIFFICULTY_RULE_PROMPT,
    DIFFICULTY_RATING_PROMPT_TEMPLATE,
    DIMENSION_RULE_PROMPT_TEMPLATE,
    FOLLOWUP_CORE_TASK_BASIS,
    FOLLOWUP_SEARCH_USAGE_PROMPT,
    INITIAL_CORE_TASK_BASIS,
    INITIAL_SEARCH_USAGE_PROMPT,
    NO_AVAILABLE_TOOLS_PROMPT,
    SYSTEM_PROMPT_TEMPLATE,
    render_prompt_template,
)
from loguru import logger


class ExamSetterSearchToolInput(BaseModel):
    query: str = Field(
        default="",
        description="检索 query。需要读取当前用户全部资料时必须传空字符串，不要写“全部资料”等自然语言。",
    )
    batch_index: int = Field(
        default=0,
        description="资料批次编号，从 0 开始；如果工具返回 has_more=true，请用 next_batch_index 继续读取。",
    )
    target_tokens: int = Field(
        default=6000,
        description="每次工具返回给 Agent 的目标 token 数，建议 4000-8000。",
    )


class ExamSetterCourseSearchToolInput(ExamSetterSearchToolInput):
    document_name: str = Field(description="课程资料名称，必须来自系统给出的 course_document_sources 列表。")


class ExamSetterCodeLspToolInput(BaseModel):
    action: Literal["project_map", "symbols"] = Field(default="project_map")
    query: Optional[str] = None
    limit: int = Field(default=20)
    timeout: int = Field(default=20)


class ExamSetterCodeAstToolInput(BaseModel):
    file_path: str
    language: Optional[str] = None


class ExamSetterCodeReaderToolInput(BaseModel):
    file_path: str
    start_line: int
    end_line: int


ExamSetterSearchDescription = """
读取当前用户 source 范围内的资料。query 为空字符串时读取全部文本块，且不使用 hybrid 检索；
query 非空时使用 hybrid 检索。返回内容包含每个文本块的 token 估算、当前批次 token 数、
total_batches、has_more 和 next_batch_index。工具内部只负责检索和分批，不会调用 AI。
"""


ExamSetterCourseSearchDescription = """
检索老师提供的课程相关知识资料。document_name 必须来自 course_document_sources；
query 为空字符串时读取该课程资料的全部文本块，query 非空时使用 hybrid 检索。
返回结构与 search 相同。
"""


class ExamSetterAgent(BaseAgent):
    STREAM_CHUNK_TIMEOUT_SECONDS = 60
    STREAM_MAX_RESTARTS = 3

    def __init__(
        self,
        model_settings: dict,
        source: str,
        thinking: bool = False,
        response_format: bool = True,
        temperature: float = 0,
        difficulty_level: int = 3,
        question_count: int = 3,
        question_dimensions: Optional[List[str]] = None,
        difficulty_rule_prompt: Optional[str] = None,
        course_id: Optional[str] = None,
        exam_id: Optional[str] = None,
        file_local_address: Optional[str] = None,
        code_local_address: Optional[str] = None,
        course_document_sources: Optional[List[str]] = None,
        show_tool_io: bool = False,
    ):
        self.source = source
        self.course_id = course_id
        self.exam_id = exam_id
        self.file_local_address = self._normalize_optional_text(file_local_address)
        self.code_local_address = self._normalize_optional_text(code_local_address)
        self.course_document_sources = self._normalize_source_list(course_document_sources)
        self.difficulty_level = self._normalize_rule_level(difficulty_level)
        self.question_count = self._normalize_question_count(question_count)
        self.question_dimensions = question_dimensions or self.get_default_question_dimensions()
        self.difficulty_rule_prompt = difficulty_rule_prompt or self.get_default_difficulty_rule_prompt()
        super().__init__("ExamSetterAgent", model_settings, thinking, response_format, temperature, show_tool_io=show_tool_io)
        self.system_prompt = self.build_system_prompt()

    def _normalize_rule_level(self, level: Optional[int]) -> int:
        if level is None:
            return 3
        return min(5, max(0, int(level)))

    def _normalize_question_count(self, question_count: Optional[int]) -> int:
        if question_count is None:
            return 3
        return max(1, int(question_count))

    def _normalize_optional_text(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _normalize_source_list(self, values: Optional[List[str]]) -> List[str]:
        sources = []
        seen = set()
        for value in values or []:
            source = str(value or "").strip()
            if not source or source in seen:
                continue
            sources.append(source)
            seen.add(source)
        return sources

    def _resolve_question_dimensions(
        self,
        question_count: int,
        question_dimensions: Optional[List[str]] = None,
    ) -> List[str]:
        dimensions = question_dimensions or self.question_dimensions
        if len(dimensions) != question_count:
            raise ValueError(
                "question_dimensions length must equal question_count: "
                f"{len(dimensions)} != {question_count}"
            )
        return dimensions

    def get_default_difficulty_rule_prompt(self) -> str:
        return DEFAULT_DIFFICULTY_RULE_PROMPT

    def get_difficulty_rating_prompt(self, level: Optional[int] = None) -> str:
        current_level = self._normalize_rule_level(level if level is not None else self.difficulty_level)
        return render_prompt_template(
            DIFFICULTY_RATING_PROMPT_TEMPLATE,
            {"current_level": current_level},
        )

    def get_difficulty_rule_prompt(
        self,
        level: Optional[int] = None,
        difficulty_rule_prompt: Optional[str] = None,
    ) -> str:
        return (
            self.get_difficulty_rating_prompt(level)
            + "\n"
            + (difficulty_rule_prompt or self.difficulty_rule_prompt)
        )

    def get_default_question_dimensions(self) -> List[str]:
        return [
            "项目目标与整体架构",
            "核心技术原理或关键机制",
            "工程实现、边界条件或异常路径",
        ]

    def get_dimension_rule_prompt(self, question_count: int, dimensions: Optional[List[str]] = None) -> str:
        active_dimensions = self._resolve_question_dimensions(question_count, dimensions)
        dimension_lines = "\n".join(
            f"          第 {index} 题：{dimension}"
            for index, dimension in enumerate(active_dimensions, start=1)
        )
        return render_prompt_template(
            DIMENSION_RULE_PROMPT_TEMPLATE,
            {"dimension_lines": dimension_lines},
        )

    def get_question_standard_prompt(
        self,
        question_count: int,
        difficulty_level: Optional[int] = None,
        difficulty_rule_prompt: Optional[str] = None,
        question_dimensions: Optional[List[str]] = None,
    ) -> str:
        return (
            self.get_difficulty_rule_prompt(difficulty_level, difficulty_rule_prompt)
            + "\n"
            + self.get_dimension_rule_prompt(question_count, question_dimensions)
        )

    def build_system_prompt(
        self,
        difficulty_level: Optional[int] = None,
        question_count: Optional[int] = None,
        difficulty_rule_prompt: Optional[str] = None,
        question_dimensions: Optional[List[str]] = None,
        is_initial_generation: bool = False,
    ) -> str:
        active_question_count = self._normalize_question_count(
            question_count if question_count is not None else self.question_count
        )
        active_difficulty_level = self._normalize_rule_level(
            difficulty_level if difficulty_level is not None else self.difficulty_level
        )
        active_dimensions = self._resolve_question_dimensions(active_question_count, question_dimensions)
        question_standard_prompt = self.get_question_standard_prompt(
            question_count=active_question_count,
            difficulty_level=active_difficulty_level,
            difficulty_rule_prompt=difficulty_rule_prompt,
            question_dimensions=active_dimensions,
        )
        has_file_material = bool(self.file_local_address)
        has_code_material = bool(self.code_local_address)
        has_course_material = bool(self.course_document_sources)
        available_tools_prompt = self._build_available_tools_prompt(
            has_file_material,
            has_code_material,
            has_course_material,
        )
        search_usage_prompt = self._build_search_usage_prompt(
            is_initial_generation,
            has_file_material,
            has_course_material,
        )
        core_task_basis = INITIAL_CORE_TASK_BASIS if is_initial_generation else FOLLOWUP_CORE_TASK_BASIS
        return render_prompt_template(
            SYSTEM_PROMPT_TEMPLATE,
            {
                "available_tools_prompt": available_tools_prompt,
                "search_usage_prompt": search_usage_prompt,
                "question_standard_prompt": question_standard_prompt,
                "core_task_basis": core_task_basis,
                "active_question_count": active_question_count,
                "active_difficulty_level": active_difficulty_level,
            },
        )

    def _build_available_tools_prompt(
        self,
        has_file_material: bool,
        has_code_material: bool,
        has_course_material: bool,
    ) -> str:
        tools = []
        if has_file_material:
            tools.append("search")
        if has_course_material:
            tools.append("courseSearch")
        if has_code_material:
            tools.extend(["codeLsp", "codeAst", "codeReader"])
        if not tools:
            return NO_AVAILABLE_TOOLS_PROMPT
        unavailable = []
        if not has_file_material:
            unavailable.append(UNAVAILABLE_FILE_MATERIAL_PROMPT)
        if not has_code_material:
            unavailable.append(UNAVAILABLE_CODE_MATERIAL_PROMPT)
        if has_course_material:
            unavailable.append(
                "courseSearch 可用课程资料名称："
                + "、".join(self.course_document_sources)
            )
        unavailable_text = UNAVAILABLE_TOOL_SEPARATOR.join(unavailable) if unavailable else ALL_LISTED_TOOLS_AVAILABLE_PROMPT
        return render_prompt_template(
            AVAILABLE_TOOLS_PROMPT_TEMPLATE,
            {
                REGISTERED_TOOLS_PLACEHOLDER: REGISTERED_TOOL_SEPARATOR.join(tools),
                "unavailable_text": unavailable_text,
            },
        )

    def _build_search_usage_prompt(
        self,
        is_initial_generation: bool,
        has_file_material: bool,
        has_course_material: bool,
    ) -> str:
        if has_file_material and has_course_material:
            base_prompt = INITIAL_SEARCH_USAGE_PROMPT if is_initial_generation else FOLLOWUP_SEARCH_USAGE_PROMPT
            return (
                base_prompt
                + "\n"
                + "        课程资料不足时，可调用 `courseSearch(document_name=课程资料名称, query=\"\")` 读取老师提供的课程资料；"
                + "document_name 必须来自系统列出的课程资料名称。"
            )
        if has_file_material:
            return INITIAL_SEARCH_USAGE_PROMPT if is_initial_generation else FOLLOWUP_SEARCH_USAGE_PROMPT
        if has_course_material:
            if is_initial_generation:
                return (
                    "        1. 首次生成题目前，必须从 `courseSearch(document_name=课程资料名称, query=\"\", batch_index=0)` "
                    "开始读取老师提供的课程资料。\n"
                    "        2. document_name 必须来自系统列出的课程资料名称；如有多个课程资料，可按需分别读取。\n"
                    "        3. 若 `has_more=true`，保持相同 document_name 和 query，并用 `next_batch_index` 继续读取。"
                )
            return (
                "        1. 非首次生成可基于已有题目、评分和上下文直接命题；课程知识不足时才调用 `courseSearch`。\n"
                "        2. 需全面重读课程资料用 `courseSearch(document_name=课程资料名称, query=\"\")`；"
                "只查主题用非空 query 定向检索。\n"
                "        3. 需续读时保持相同 document_name 和 query，并使用 `next_batch_index`。"
            )
        return "        未注册资料检索工具时，不要调用 `search` 或 `courseSearch`。"

    def get_tools(self):
        @tool(args_schema=ExamSetterSearchToolInput, description=ExamSetterSearchDescription)
        async def search(query: str = "", batch_index: int = 0, target_tokens: int = 6000) -> str:
            search_tool = SearchTool("search_tool")
            response = await search_tool.execute(
                query=query,
                source=self.source,
                course_id=self.course_id,
                exam_id=self.exam_id,
                batch_index=batch_index,
                target_tokens=target_tokens,
            )
            return response

        @tool(args_schema=ExamSetterCourseSearchToolInput, description=ExamSetterCourseSearchDescription)
        async def courseSearch(
            document_name: str,
            query: str = "",
            batch_index: int = 0,
            target_tokens: int = 6000,
        ) -> str:
            document_name = str(document_name or "").strip()
            if document_name not in self.course_document_sources:
                return json.dumps(
                    {
                        "error": "COURSE_DOCUMENT_SOURCE_NOT_ALLOWED",
                        "document_name": document_name,
                        "course_document_sources": self.course_document_sources,
                    },
                    ensure_ascii=False,
                )
            search_tool = SearchTool("course_search_tool")
            return await search_tool.execute(
                query=query,
                source=document_name,
                course_id=self.course_id,
                exam_id=None,
                batch_index=batch_index,
                target_tokens=target_tokens,
            )

        @tool(args_schema=ExamSetterCodeLspToolInput, description=CodeLspDescription)
        async def codeLsp(
            action: Literal["project_map", "symbols"] = "project_map",
            query: Optional[str] = None,
            limit: int = 20,
            timeout: int = 20,
        ) -> str:
            code_lsp = CodeLspTool("code_lsp", self.source, git_local_address=self.code_local_address)
            return await code_lsp.execute(
                action=action,
                query=query,
                limit=limit,
                timeout=timeout,
            )

        @tool(args_schema=ExamSetterCodeAstToolInput, description=CodeAstDescription)
        async def codeAst(
            file_path: str,
            language: Optional[str] = None,
        ) -> str:
            code_ast = CodeAstTool("code_ast", self.source, git_local_address=self.code_local_address)
            return await code_ast.execute(
                file_path=file_path,
                language=language,
            )

        @tool(args_schema=ExamSetterCodeReaderToolInput, description=CodeReaderDescription)
        async def codeReader(file_path: str, start_line: int, end_line: int) -> str:
            code_reader = CodeReaderTool("code_reader", self.source, git_local_address=self.code_local_address)
            return await code_reader.execute(
                file_path=file_path,
                start_line=start_line,
                end_line=end_line,
            )

        tools = []
        
        if self.file_local_address:
            tools.append(search)
        if self.course_document_sources:
            tools.append(courseSearch)
        if self.code_local_address:
            tools.extend([codeLsp, codeAst, codeReader])
        return tools

    async def execute(
        self,
        history: str = None,
        difficulty_level: Optional[int] = None,
        question_count: Optional[int] = None,
        difficulty_rule_prompt: Optional[str] = None,
        question_dimensions: Optional[List[str]] = None,
        is_initial_generation: Optional[bool] = None,
    ):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        active_question_count = self._normalize_question_count(
            question_count if question_count is not None else self.question_count
        )
        active_is_initial_generation = (
            not bool(history)
            if is_initial_generation is None
            else bool(is_initial_generation)
        )
        system_prompt = self.build_system_prompt(
            difficulty_level=difficulty_level,
            question_count=active_question_count,
            difficulty_rule_prompt=difficulty_rule_prompt,
            question_dimensions=question_dimensions,
            is_initial_generation=active_is_initial_generation,
        )
        historys = [{"role": "system", "content": system_prompt}]
        if history:
            historys.extend(
                self._augment_initial_history(history, active_question_count)
                if active_is_initial_generation
                else history
            )
        else:
            initial_content = self._build_initial_user_prompt(active_question_count)
            historys.append({
                "role": "user",
                "content": initial_content,
            })
        try:
            return await self._run_agent_stream_with_restart(historys)
        finally:
            await self.stop_heartbeat()

    async def _run_agent_stream_with_restart(self, historys):
        restart_count = 0
        while True:
            try:
                return await self._run_agent_stream_once(historys, restart_count)
            except asyncio.TimeoutError as exc:
                if restart_count >= self.STREAM_MAX_RESTARTS:
                    raise RuntimeError("examSetter stream timeout after 3 restarts") from exc
                restart_count += 1
                logger.warning(
                    "examSetter stream timeout; restarting generation "
                    f"({restart_count}/{self.STREAM_MAX_RESTARTS})"
                )

    async def _run_agent_stream_once(self, historys, restart_count: int = 0):
        stream = self.agent.astream({"messages": historys})
        latest_response = None
        chunk_index = 0
        try:
            while True:
                start_time = time.monotonic()
                try:
                    chunk = await asyncio.wait_for(
                        stream.__anext__(),
                        timeout=self.STREAM_CHUNK_TIMEOUT_SECONDS,
                    )
                except StopAsyncIteration:
                    break
                elapsed = time.monotonic() - start_time
                chunk_index += 1
                logger.info(
                    "examSetter stream chunk returned",
                    restart_count=restart_count,
                    chunk_index=chunk_index,
                    elapsed_seconds=round(elapsed, 3),
                )
                response = self._extract_stream_response(chunk)
                if response is not None:
                    latest_response = response
        finally:
            aclose = getattr(stream, "aclose", None)
            if aclose is not None:
                await aclose()

        if latest_response is None:
            raise RuntimeError("examSetter stream finished without messages")
        return latest_response

    def _extract_stream_response(self, chunk):
        if isinstance(chunk, dict):
            if "messages" in chunk:
                return chunk
            for value in chunk.values():
                if isinstance(value, dict) and "messages" in value:
                    return value
        return None

    def _build_initial_user_prompt(self, active_question_count: int) -> str:
        if self.file_local_address and self.course_document_sources:
            return (
                "这是首次生成题目。请先调用 search(query=\"\") 读取当前用户全部资料；"
                "如需课程背景知识，再使用 courseSearch(document_name=课程资料名称, query=\"\") "
                f"读取课程资料（可用名称：{'、'.join(self.course_document_sources)}）。"
                f"读完所需批次后生成 {active_question_count} 个高质量口试问题。"
            )
        if self.file_local_address:
            return (
                f"这是首次生成题目。请先调用 search(query=\"\") 读取当前用户全部资料，"
                f"读完所有批次后生成 {active_question_count} 个高质量口试问题。"
            )
        if self.course_document_sources:
            return (
                "这是首次生成题目。未注册当前用户资料 search；"
                "请使用 courseSearch(document_name=课程资料名称, query=\"\") 读取老师提供的课程资料"
                f"（可用名称：{'、'.join(self.course_document_sources)}），"
                f"再生成 {active_question_count} 个高质量口试问题。"
            )
        return (
            "这是首次生成题目。由于未提供文件资料地址，`search` 工具未注册。"
            f"请只基于已有上下文和已注册的代码工具，生成 {active_question_count} 个高质量口试问题。"
        )

    def _augment_initial_history(self, history, active_question_count: int):
        if not self.course_document_sources:
            return history
        augmented = []
        appended = False
        course_instruction = (
            "\n如果需要老师提供的课程相关知识，请调用 "
            "courseSearch(document_name=课程资料名称, query=\"\")；"
            f"可用课程资料名称：{'、'.join(self.course_document_sources)}。"
        )
        for message in history:
            if not appended and isinstance(message, dict) and message.get("role") == "user":
                copied = dict(message)
                if self.file_local_address:
                    copied["content"] = str(copied.get("content") or "") + course_instruction
                else:
                    copied["content"] = self._build_initial_user_prompt(active_question_count)
                augmented.append(copied)
                appended = True
            else:
                augmented.append(message)
        if not appended:
            augmented.append({
                "role": "user",
                "content": self._build_initial_user_prompt(active_question_count),
            })
        return augmented

    def get_response_format(self):
        class QuestionBlock(BaseModel):
            type: Literal["text", "code"]
            content: Optional[str] = None
            fragment_id: Optional[str] = None

        class CodeFragment(BaseModel):
            id: str
            relative_path: str
            start_line: int
            end_line: int
            language: str = "python"
            title: Optional[str] = None
            lines: List[str]

        class QuestionItem(BaseModel):
            difficulty_level: int
            dimension: str
            Question: str
            standard_answer: str
            question_blocks: List[QuestionBlock] = Field(default_factory=list)
            code_fragments: List[CodeFragment] = Field(default_factory=list)
            knowledge_point: str
            reason: str

        class ResponseFormat(BaseModel):
            project_summary: str
            questions: List[QuestionItem]

        return ResponseFormat
