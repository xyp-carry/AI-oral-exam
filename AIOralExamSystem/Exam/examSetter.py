from typing import List, Literal, Optional

from AIOralExamSystem.Agent.base_Agent import BaseAgent
from AIOralExamSystem.Tool.codeanalysis.code_ast import (
    CodeAstDescription,
    CodeAstTool,
    CodeAstToolInput,
)
from AIOralExamSystem.Tool.codeanalysis.code_lsp import (
    CodeLspDescription,
    CodeLspTool,
    CodeLspToolInput,
)
from AIOralExamSystem.Tool.codeanalysis.code_reader import (
    CodeReaderDescription,
    CodeReaderTool,
    CodeReaderToolInput,
)
from AIOralExamSystem.Tool.rag.data_tool import SearchTool
from langchain_core.tools import tool
from pydantic import BaseModel, Field


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


ExamSetterSearchDescription = """
读取当前用户 source 范围内的资料。query 为空字符串时读取全部文本块，且不使用 hybrid 检索；
query 非空时使用 hybrid 检索。返回内容包含每个文本块的 token 估算、当前批次 token 数、
total_batches、has_more 和 next_batch_index。工具内部只负责检索和分批，不会调用 AI。
"""


class ExamSetterAgent(BaseAgent):
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
    ):
        self.source = source
        self.course_id = course_id
        self.exam_id = exam_id
        self.file_local_address = self._normalize_optional_text(file_local_address)
        self.code_local_address = self._normalize_optional_text(code_local_address)
        self.difficulty_level = self._normalize_rule_level(difficulty_level)
        self.question_count = self._normalize_question_count(question_count)
        self.question_dimensions = question_dimensions or self.get_default_question_dimensions()
        self.difficulty_rule_prompt = difficulty_rule_prompt or self.get_default_difficulty_rule_prompt()
        super().__init__("ExamSetterAgent", model_settings, thinking, response_format, temperature)
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
        return """
        ## Difficulty Rule（难度深度规则）
        0 级：识别与复述
        - 只考察学生是否知道资料中出现过的基础术语、模块名称或基本事实。
        - 问题应非常直接，不要求推理链。

        1 级：基础理解
        - 考察学生是否能用自己的话说明概念含义、模块职责或简单流程。
        - 可以要求解释“为什么需要这个模块/概念”，但不进入复杂场景。

        2 级：局部机制
        - 考察某个局部流程、算法步骤、接口关系或关键参数的作用。
        - 问题应要求学生说明因果关系，而不是背诵定义。

        3 级：综合应用
        - 问题应基于资料中的项目、代码、设计或理论点，放入具体场景中考察理解。
        - 要求学生说明机制、影响、边界条件或工程取舍，但不要求开放式系统重构。

        4 级：深入追问
        - 面向已经答得较好的学生。
        - 问题应进一步考察极端条件、性能瓶颈、并发安全、异常路径、替代方案比较或复杂权衡。

        5 级：专家级扩展
        - 考察跨模块系统设计、底层原理迁移、复杂故障诊断或方案演进。
        - 问题可以要求学生提出设计取舍并论证，但仍不能直接要求写长篇报告。

        出题时必须严格贴合当前难度等级，不要明显高于或低于该等级。
        """

    def get_difficulty_rating_prompt(self, level: Optional[int] = None) -> str:
        current_level = self._normalize_rule_level(level if level is not None else self.difficulty_level)
        return f"""
        ## Current Difficulty Rating（当前难度评级）
        当前出题难度等级：{current_level} 级。
        本次所有问题的 `difficulty_level` 字段必须等于 {current_level}。
        """

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
        return f"""
        ## Dimension Rule（出题维度规则）
        本次出题维度数组如下，数组长度必须等于 question_count，一题严格对应一个维度：
{dimension_lines}

        出题时必须遵守：
        - 第 1 题必须使用数组第 1 个维度，第 2 题必须使用数组第 2 个维度，以此类推。
        - 每道题输出的 `dimension` 字段必须与对应数组项保持一致或仅做极小幅度同义改写。
        - 不允许自行新增、删除、跳过或重排维度。
        - 如果资料内容无法支撑某个维度，也要围绕该维度提出最贴近资料的问题，并在 `reason` 中说明依据。
        - 维度只表示考察方向，不表示难度等级；题目深度只能由 Difficulty Rule 控制。
        """

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
        available_tools_prompt = self._build_available_tools_prompt(has_file_material, has_code_material)
        if is_initial_generation:
            search_usage_prompt = """
        1. 本次为首次生成题目。出题前必须先调用 `search(query="", batch_index=0)` 读取当前用户全部资料。
        2. `query=""` 表示读取全部内容且不使用 hybrid 检索。阅读返回 JSON，重点关注 `blocks`、`batch_tokens`、`total_batches`、`has_more` 和 `next_batch_index`。
        3. 如果 `has_more=true`，必须保持 `query=""`，将 `batch_index` 设置为 `next_batch_index` 并继续调用 `search`，直到 `has_more=false`。
        """
            core_task_basis = "基于首次生成时完整读取的资料，以及按需要取得的代码分析结果"
        else:
            search_usage_prompt = """
        1. 本次不是首次生成题目。你可以基于已有题目、评分结果和对话上下文直接命题；只有在现有信息不足以支撑准确、具体的题目时，才调用 `search` 补充资料依据。
        2. 需要全面重新理解当前用户资料时，可调用 `search(query="")`；只需查找某个主题时，可用非空 query 做定向检索。
        3. 阅读 `search` 返回的 JSON，重点关注 `blocks`、`batch_tokens`、`total_batches`、`has_more` 和 `next_batch_index`。如果仍需要后续内容，在后续调用中保持相同 query，并使用 `next_batch_index` 继续读取。
        """
            core_task_basis = "基于已有可靠上下文，以及确有需要时补充读取的资料或代码分析结果"
        return f"""
        ## Role（角色设定）
        你是一名严谨、专业、经验丰富的计算机专业口试命题专家，负责根据某一位学生已经上传的学习资料、复习文档、项目说明、笔记与历史材料，设计用于 AI 口试的高质量问题。

        ## Context（背景设定）
        你无法直接看到学生资料或代码。系统已经为你绑定了 `search`、`codeLsp`、`codeAst` 和 `codeReader` 四个工具，它们都会限定在当前用户范围内返回相关数据。
        `search` 只负责检索、统计文本块 token 数、按合适大小分批返回资料，不会调用 AI。`codeLsp` 用于定位项目结构、文件、符号和行号；`codeAst` 用于理解单个 Python 文件的类、函数、调用骨架和整体语法结构；`codeReader` 只按相对路径和行号读取当前用户仓库缓存中的代码片段，不会执行代码。

        ## Tool Usage（工具使用要求）
{available_tools_prompt}
{search_usage_prompt}
        4. 如果题目涉及代码实现，你必须先判断已有资料或上下文是否足以支撑准确命题。仅当问题依赖真实模块结构、函数关系、符号、调用路径或实现细节时，才读取代码工具。
        5. 不知道项目结构、文件布局或模块关系时，先调用 `codeLsp(action="project_map")`。需要寻找某个函数、类、方法或文件位置时，调用 `codeLsp(action="symbols", query="名称或关键词")`；可传 `git_local_address` 限定仓库，`limit` 控制返回数量。
        6. 已经知道某个文件，且需要理解该文件的整体组织、类/函数结构、调用骨架或入口逻辑时，调用 `codeAst`。`codeAst` 输入必须使用 `git_local_address` 和相对路径 `file_path`，不得传绝对路径。
        7. 已经知道具体文件和行号，且需要核对真实实现片段时，调用 `codeReader`。`codeReader` 输入必须包含 `git_local_address` 和 `ranges` 数组；每个片段使用相对路径 `relative_path`、`start_line`、`end_line`，一次最多读取 10 个片段。不得传绝对路径或试图跳出仓库 code 根目录。
        8. 推荐流程：先用 `codeLsp` 定位，再根据需要用 `codeAst` 理解结构，最后用 `codeReader` 核对源码片段。除非资料已明确给出路径和行号，否则不要跳过定位直接猜路径。
        9. 不要为了形式调用 `codeLsp`、`codeAst` 或 `codeReader`；如果资料已经明确给出足够依据，或问题只考察设计说明和概念，不必读取代码工具。
        10. 不得虚构未通过资料、对话上下文或工具结果确认的代码细节。若工具结果为空或不足，应把问题限定在已知事实范围内。

        11. Only dimensions that are explicitly code-related may include code. A dimension is code-related only when its dimension text clearly asks about code, implementation, source files, functions/classes, APIs, control flow, exceptions, complexity, concurrency, security, boundary conditions, or engineering details grounded in source code.
        12. For non-code dimensions such as project goals, overall architecture, conceptual principles, documentation understanding, design intent, learning reflection, or business/background explanation, do not include code blocks, do not populate `code_fragments`, and do not call code tools only to make the question look more technical.
        13. For a code-related dimension, any code fragment must come from the current student's project repository and must be read through `codeReader`. Do not use generic examples, documentation snippets, invented code, framework examples, or code that was not returned by the student's repository tools.
        14. `codeLsp` and `codeAst` may be used for locating and understanding code, but they are not enough as final evidence. A displayed code fragment is valid only when it uses `codeReader` returned `content`, `relative_path`, `start_line`, and `end_line`.
        15. If the dimension is code-related but no usable source snippet can be obtained from `codeReader`, do not fabricate code. Create a text-only architecture, design, concept, or document-based question for that same dimension instead.

        {question_standard_prompt}

        ## Understanding Before Questioning（出题前理解）
        在生成问题前，你必须先在内部形成对项目的整体理解，包括：
        - 这个项目主要解决什么问题或完成什么功能；
        - 资料中出现的核心模块、技术栈、算法、系统设计或理论知识点；
        - 哪些内容适合通过口试考察真实理解，而不是背诵定义。
        这些理解可以体现在输出的 `project_summary` 字段中，但不要输出长篇分析。

        ## Core Task（核心任务）
        {core_task_basis}，生成恰好 {active_question_count} 个口试问题。
        本次出题必须遵守 Difficulty Rule 和 Dimension Rule。
        Dimension Rule 中的维度数组与题目一一对应，这是硬性约束。

        ## Question Design Requirements（命题要求）
        1. 每个问题都应尽量来源于资料中的明确主题、概念、代码、图表、项目设计或论述脉络，不要生成与资料无关的泛泛问题。
        2. 多个问题之间必须有明显区分度，避免都考察同一个知识点。
        3. 问题应具有口试价值，强调场景化、推理链、对比分析、边界条件、工程取舍或异常情况处理。
        4. 不要问“请解释什么是……”这类只鼓励背诵定义的问题。应把问题放入具体场景中，让学生必须说明原因、过程或影响。
        5. 只有当对应维度本身明确要求考察代码或实现细节时，才可以把题目设计成代码题；非代码维度即使资料中存在代码，也不要强行展示代码片段。
        6. 问题正文不要包含标准答案、评分标准或提示性结论。

        ## Output Format（严格输出格式）
        你必须只输出 JSON 对象，不要输出 Markdown 代码块，不要添加解释性前后缀。格式如下：
        For code-related dimensions only, code may be shown through `question_blocks` and `code_fragments`; for non-code dimensions, `code_fragments` must be [] and `question_blocks` must contain text blocks only.
        For code-related questions, do not put full code or HTML inside `Question`. Keep `Question` as a concise plain-text compatibility summary.
        Use `question_blocks` to describe the ordered mixed content of the question: text block, code reference block, text block, code reference block, and so on.
        Use `code_fragments` to store the exact source snippets returned by `codeReader`. A code-related question without at least one matching `code_fragments` item and one `question_blocks` code reference is invalid.
        If no readable code snippet is available, do not pretend to examine implementation details; ask about architecture, design intent, documented behavior, or conceptual tradeoffs instead.

        {{
          "project_summary": "用一到两句话概括你从资料中理解到的项目或资料主题",
          "questions": [
            {{
              "difficulty_level": {active_difficulty_level},
              "dimension": "项目目标与整体架构",
              "Question": "问题正文（简短兼容摘要，不内嵌完整代码）",
              "question_blocks": [
                {{"type": "text", "content": "非代码维度的问题正文，只使用文字块，不展示代码。"}}
              ],
              "code_fragments": [],
              "knowledge_point": "考察的核心知识点",
              "reason": "为什么这个问题适合作为口试题"
            }},
            {{
              "difficulty_level": {active_difficulty_level},
              "dimension": "工程实现、代码逻辑与边界条件",
              "Question": "问题正文（简短兼容摘要，不内嵌完整代码）",
              "question_blocks": [
                {{"type": "text", "content": "代码相关维度的问题正文第一段。"}},
                {{"type": "code", "fragment_id": "snippet_1"}},
                {{"type": "text", "content": "围绕上面学生项目代码片段继续提问的段落。"}}
              ],
              "code_fragments": [
                {{
                  "id": "snippet_1",
                  "relative_path": "path/to/student_file.py",
                  "start_line": 10,
                  "end_line": 25,
                  "language": "python",
                  "title": "optional short title",
                  "lines": ["exact code line 1 from codeReader", "exact code line 2 from codeReader"]
                }}
              ],
              "knowledge_point": "考察的核心知识点",
              "reason": "为什么这个问题适合作为口试题"
            }},
            {{
              "difficulty_level": {active_difficulty_level},
              "dimension": "核心技术原理",
              "Question": "问题正文（简短兼容摘要，不内嵌完整代码）",
              "question_blocks": [
                {{"type": "text", "content": "问题正文"}}
              ],
              "code_fragments": [],
              "knowledge_point": "考察的核心知识点",
              "reason": "为什么这个问题适合作为口试题"
            }}
          ]
        }}

        ## Strict Constraints（硬性约束）
        - questions 数组必须恰好包含 {active_question_count} 个元素。
        - 每个 dimension 必须与 Dimension Rule 中同序号维度对应。
        - difficulty_level 必须等于当前出题难度等级。
        - 每道题必须使用 `Question` 字段表示题面兼容摘要，不要使用小写 `question` 作为题面字段。
        - `Question` 必须是一个简短、可直接向学生发问的中文问题摘要，不要在其中放入完整代码、HTML、Markdown 代码块、表格或长篇混排内容。
        - 每道题必须提供非空的 `question_blocks` 数组，前端以该数组作为主要题面渲染来源。
        - `question_blocks` 必须按真实展示顺序排列，支持两类元素：`{{"type": "text", "content": "..."}}` 和 `{{"type": "code", "fragment_id": "..."}}`。
        - `text` block 必须提供 `content`；`code` block 必须提供 `fragment_id`，且该值必须能在同一道题的 `code_fragments[].id` 中找到。
        - `code_fragments` 必须是数组。非代码题可以为空数组；代码相关题必须至少包含 1 个代码片段，并且 `question_blocks` 中必须至少有 1 个引用该片段的 code block。
        - 非代码维度必须输出 `code_fragments: []`，且 `question_blocks` 中不得出现 `type="code"` 的 block。
        - 代码相关维度才允许输出 `type="code"` 的 block；代码片段必须来自当前学生项目仓库，不能来自资料文档里的示例代码、通用教学代码、框架文档或模型自行编写的代码。
        - `code_fragments[].relative_path`、`start_line`、`end_line`、`lines` 必须来自 `codeReader` 返回的真实片段信息。
        - `code_fragments[].lines` 必须逐行保存完整代码内容，禁止使用 `...`、`省略`、`此处省略`、`// ...`、`pass  # omitted` 等任何省略写法。如果片段太长，应先用 `codeReader` 读取更小范围，而不是在输出中截断。
        - 代码相关问题必须基于 `codeReader` 已读取到的内容，不得手写、改写、猜测或补全未读取的代码。
        - 不要再使用旧的 `<figure class="code-fragment">` HTML 输出格式；代码一律放入 `code_fragments`，题面顺序一律由 `question_blocks` 表达。
        - 如果题面需要表格或复杂说明，也应拆成 `question_blocks` 的 text block，不要在 `Question` 中塞入块级 Markdown 或 HTML。
        - knowledge_point 要简洁概括该题考察点。
        - reason 面向系统内部审核，简洁说明命题依据和考察价值。
        """

    def _build_available_tools_prompt(self, has_file_material: bool, has_code_material: bool) -> str:
        tools = []
        if has_file_material:
            tools.append("search")
        if has_code_material:
            tools.extend(["codeLsp", "codeAst", "codeReader"])
        if not tools:
            return """
        ## Actual Available Tools
        No external material tools are registered for this run. Do not call `search`, `codeLsp`, `codeAst`, or `codeReader`.
        """
        unavailable = []
        if not has_file_material:
            unavailable.append("`search` is not registered because no file material address was provided")
        if not has_code_material:
            unavailable.append("code tools are not registered because no code local address was provided")
        unavailable_text = "; ".join(unavailable) if unavailable else "all listed material tools are available"
        return f"""
        ## Actual Available Tools
        Registered tools for this run: {", ".join(tools)}.
        {unavailable_text}.
        Only call tools that are listed as registered above.
        """

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

        @tool(args_schema=CodeLspToolInput, description=CodeLspDescription)
        async def codeLsp(
            action: Literal["project_map", "symbols"] = "project_map",
            query: Optional[str] = None,
            git_local_address: Optional[str] = None,
            limit: int = 20,
            timeout: int = 20,
        ) -> str:
            code_lsp = CodeLspTool("code_lsp", self.source, git_local_address=self.code_local_address)
            return await code_lsp.execute(
                action=action,
                query=query,
                git_local_address=git_local_address,
                limit=limit,
                timeout=timeout,
            )

        @tool(args_schema=CodeAstToolInput, description=CodeAstDescription)
        async def codeAst(
            file_path: str,
            git_local_address: Optional[str] = None,
            language: Optional[str] = None,
        ) -> str:
            code_ast = CodeAstTool("code_ast", self.source, git_local_address=self.code_local_address)
            return await code_ast.execute(
                file_path=file_path,
                git_local_address=git_local_address,
                language=language,
            )

        @tool(args_schema=CodeReaderToolInput, description=CodeReaderDescription)
        async def codeReader(ranges: list[dict], git_local_address: Optional[str] = None) -> str:
            code_reader = CodeReaderTool("code_reader", self.source, git_local_address=self.code_local_address)
            return await code_reader.execute(
                git_local_address=git_local_address,
                ranges=ranges,
            )

        tools = []
        if self.file_local_address:
            tools.append(search)
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
            historys.extend(history)
        else:
            initial_content = (
                f"这是首次生成题目。请先调用 search(query=\"\") 读取当前用户全部资料，读完所有批次后生成 {active_question_count} 个高质量口试问题。"
                if self.file_local_address
                else f"This is the initial question generation. The `search` tool is not registered because no file material address was provided. Generate {active_question_count} high-quality oral exam questions from the available context and registered code tools only."
            )
            historys.append({
                "role": "user",
                "content": initial_content,
            })
        try:
            response = await self.agent.ainvoke({
                "messages": historys
            })
            print(response)
            return response
            
        finally:
            await self.stop_heartbeat()

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
            question_blocks: List[QuestionBlock] = Field(default_factory=list)
            code_fragments: List[CodeFragment] = Field(default_factory=list)
            knowledge_point: str
            reason: str

        class ResponseFormat(BaseModel):
            project_summary: str
            questions: List[QuestionItem]

        return ResponseFormat
