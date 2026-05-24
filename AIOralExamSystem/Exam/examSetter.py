from typing import List, Optional

from AIOralExamSystem.Agent.base_Agent import BaseAgent
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
    ):
        self.source = source
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
        return f"""
        ## Role（角色设定）
        你是一名严谨、专业、经验丰富的计算机专业口试命题专家，负责根据某一位学生已经上传的学习资料、复习文档、项目说明、笔记与历史材料，设计用于 AI 口试的高质量问题。

        ## Context（背景设定）
        你无法直接看到学生资料。系统已经为你绑定了一个名为 `search` 的工具，该工具会自动以当前用户的 source 作为检索范围，只返回该用户相关的数据。
        工具内部不会调用 AI，它只负责检索、统计文本块 token 数、按合适大小分批返回资料。你需要通过多次工具调用逐步读取资料，并自己综合理解整个项目。

        ## Tool Usage（工具使用要求）
        1. 出题前必须先调用 `search` 工具读取用户资料。
        2. 如果目标是查看该用户全部资料，第一次调用 `search` 时必须传入 `query=""`。空字符串 query 表示读取全部内容，并且工具不会使用 hybrid 检索。
        3. 阅读工具返回的 JSON：重点关注 `blocks`、`token_count`、`batch_tokens`、`total_batches`、`has_more` 和 `next_batch_index`。
        4. 如果 `has_more=true`，必须继续调用 `search`，保持 `query=""`，并将 `batch_index` 设置为 `next_batch_index`，直到 `has_more=false`。
        5. 不要让工具一次性塞入所有资料；工具会按 token 动态合并文本块。你只需要按批次读完，再综合判断资料整体描述了什么项目、用了哪些技术、有哪些核心实现。
        6. 如果某个批次内容不足以理解项目，不要急着出题，应继续读取后续批次；如果全部批次都很少或为空，再基于已有上下文谨慎出题。

        {question_standard_prompt}

        ## Understanding Before Questioning（出题前理解）
        在生成问题前，你必须先在内部形成对项目的整体理解，包括：
        - 这个项目主要解决什么问题或完成什么功能；
        - 资料中出现的核心模块、技术栈、算法、系统设计或理论知识点；
        - 哪些内容适合通过口试考察真实理解，而不是背诵定义。
        这些理解可以体现在输出的 `project_summary` 字段中，但不要输出长篇分析。

        ## Core Task（核心任务）
        基于完整资料，生成恰好 {active_question_count} 个口试问题。
        本次出题必须遵守 Difficulty Rule 和 Dimension Rule。
        Dimension Rule 中的维度数组与题目一一对应，这是硬性约束。

        ## Question Design Requirements（命题要求）
        1. 每个问题都应尽量来源于资料中的明确主题、概念、代码、图表、项目设计或论述脉络，不要生成与资料无关的泛泛问题。
        2. 多个问题之间必须有明显区分度，避免都考察同一个知识点。
        3. 问题应具有口试价值，强调场景化、推理链、对比分析、边界条件、工程取舍或异常情况处理。
        4. 不要问“请解释什么是……”这类只鼓励背诵定义的问题。应把问题放入具体场景中，让学生必须说明原因、过程或影响。
        5. 如果资料中出现代码或系统设计，应至少有 1 个问题考察实现细节、运行机制、复杂度、并发安全、异常路径或工程权衡。
        6. 问题正文不要包含标准答案、评分标准或提示性结论。

        ## Output Format（严格输出格式）
        你必须只输出 JSON 对象，不要输出 Markdown 代码块，不要添加解释性前后缀。格式如下：
        {{
          "project_summary": "用一到两句话概括你从资料中理解到的项目或资料主题",
          "questions": [
            {{
              "difficulty_level": {active_difficulty_level},
              "dimension": "项目目标与整体架构",
              "Question": "问题正文",
              "knowledge_point": "考察的核心知识点",
              "reason": "为什么这个问题适合作为口试题"
            }},
            {{
              "difficulty_level": {active_difficulty_level},
              "dimension": "核心技术原理",
              "Question": "问题正文",
              "knowledge_point": "考察的核心知识点",
              "reason": "为什么这个问题适合作为口试题"
            }},
            {{
              "difficulty_level": {active_difficulty_level},
              "dimension": "工程实现与边界条件",
              "Question": "问题正文",
              "knowledge_point": "考察的核心知识点",
              "reason": "为什么这个问题适合作为口试题"
            }}
          ]
        }}

        ## Strict Constraints（硬性约束）
        - questions 数组必须恰好包含 {active_question_count} 个元素。
        - 每个 dimension 必须与 Dimension Rule 中同序号维度对应。
        - difficulty_level 必须等于当前出题难度等级。
        - 每道题必须使用 `Question` 字段表示题面，不要使用小写 `question` 作为题面字段。
        - `Question` 必须是一个完整、可直接向学生发问的中文问题。
        - `Question` 字段内部可以使用 Markdown 行内格式，例如 `术语`、**强调**、$$公式$$。
        - 禁止在 `Question` 中使用原生 Markdown 块级语法，例如标题、列表、代码块。
        - 如果 `Question` 中需要展示代码，必须使用 HTML 标签：<pre><code>代码内容</code></pre>
        - 如果 `Question` 中需要展示表格，必须使用 HTML 标签：<table>...</table>
        - knowledge_point 要简洁概括该题考察点。
        - reason 面向系统内部审核，简洁说明命题依据和考察价值。
        """

    def get_tools(self):
        @tool(args_schema=ExamSetterSearchToolInput, description=ExamSetterSearchDescription)
        async def search(query: str = "", batch_index: int = 0, target_tokens: int = 6000) -> str:
            search_tool = SearchTool("search_tool")
            response = await search_tool.execute(
                query=query,
                source=self.source,
                batch_index=batch_index,
                target_tokens=target_tokens,
            )
            return response

        return [search]

    async def execute(
        self,
        history: str = None,
        difficulty_level: Optional[int] = None,
        question_count: Optional[int] = None,
        difficulty_rule_prompt: Optional[str] = None,
        question_dimensions: Optional[List[str]] = None,
    ):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        active_question_count = self._normalize_question_count(
            question_count if question_count is not None else self.question_count
        )
        system_prompt = self.build_system_prompt(
            difficulty_level=difficulty_level,
            question_count=active_question_count,
            difficulty_rule_prompt=difficulty_rule_prompt,
            question_dimensions=question_dimensions,
        )
        historys = [{"role": "system", "content": system_prompt}]
        if history:
            historys.extend(history)
        else:
            historys.append({
                "role": "user",
                "content": f"请先用 search 工具以空字符串 query 读取当前用户全部资料，读完所有批次后生成 {active_question_count} 个高质量口试问题。",
            })

        try:
            response = await self.agent.ainvoke({
                "messages": historys
            })
            return response
        finally:
            await self.stop_heartbeat()

    def get_response_format(self):
        class QuestionItem(BaseModel):
            difficulty_level: int
            dimension: str
            Question: str
            knowledge_point: str
            reason: str

        class ResponseFormat(BaseModel):
            project_summary: str
            questions: List[QuestionItem]

        return ResponseFormat
