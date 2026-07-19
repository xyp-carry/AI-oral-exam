import json
from typing import List, Optional

from AIOralExamSystem.Agent.base_Agent import BaseAgent
from AIOralExamSystem.Tool.rag.data_tool import SearchTool
from langchain_core.tools import tool
from pydantic import BaseModel, Field


class DocReportSearchToolInput(BaseModel):
    query: str = Field(
        default="",
        description="检索学生报告内容。query 为空字符串时读取全部报告文本块；query 非空时按主题检索报告内容。",
    )
    batch_index: int = Field(
        default=0,
        description="资料批次编号，从 0 开始；如果工具返回 has_more=true，请使用 next_batch_index 继续读取。",
    )
    target_tokens: int = Field(
        default=6000,
        description="每次工具返回给 Agent 的目标 token 数，建议 4000-8000。",
    )


class DocTeacherSearchToolInput(DocReportSearchToolInput):
    document_name: str = Field(description="教师参考资料名称，必须来自系统给出的 teacher_document_sources 列表。")


DocReportSearchDescription = """
检索学生提交的报告内容。query 为空字符串时读取报告全文文本块，不使用 hybrid 检索；
query 非空时使用 hybrid 检索。返回内容包含每个文本块的 token 估算、当前批次 token 数、
total_batches、has_more 和 next_batch_index。该工具只读取学生报告，不读取教师参考资料。
"""


DocTeacherSearchDescription = """
检索教师提供的课程、实验或考试参考资料。document_name 必须来自 teacher_document_sources；
query 为空字符串时读取该教师资料的全文文本块，query 非空时使用 hybrid 检索。
该工具只读取教师参考资料，不能用于读取学生报告。
"""


class JudgerAgent(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        source: str,
        thinking: bool = False,
        response_format: bool = False,
        temperature: float = 0,
        show_tool_io: bool = False,
    ):
        super().__init__(
            "JudgerAgent",
            model_settings,
            thinking,
            response_format,
            temperature,
            show_tool_io=show_tool_io,
        )
        self.source = source
        self.system_prompt_step1 = """
        ## Role
        你是一名客观的评委，负责根据学生与面试官的对话内容判断学生的回答质量。

        ## Task
        1. 根据学生与面试官的对话内容，判断学生每一轮回答的质量。
        2. 如果有多轮对话，每轮对话后都需要根据学生回答质量判断是否符合要求。
        3. 不仅要给出分数，还需要给出评分依据。

        ## Scoring
        - 好：7-10 分
        - 中：4-6 分
        - 差：0-3 分

        ## Output
        【第 n 轮】
        第 n 轮分数：{score}
        第 n 轮评分依据：{reason}

        【第 n+1 轮】
        第 n+1 轮分数：{score}
        第 n+1 轮评分依据：{reason}
        ...
        """

    async def execute(self, history: str):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        historys = [{"role": "system", "content": self.system_prompt_step1}]
        historys.extend(history)

        response = await self.agent.ainvoke({
            "messages": historys
        })

        await self.stop_heartbeat()
        return response


class StageJudgerAgent(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        source: str,
        thinking: bool = True,
        response_format: bool = True,
        temperature: float = 0,
        show_tool_io: bool = False,
    ):
        super().__init__(
            "StageJudgerAgent",
            model_settings,
            thinking,
            response_format,
            temperature,
            show_tool_io=show_tool_io,
        )
        self.source = source
        self.system_prompt = """
        ## 角色
        你是阶段性口试评审，只负责评价“当前这一轮问题”中学生回答的质量。

        ## 输入
        user message 会提供当前问题、问题维度、当前分数区间、学生回答、题目的 `standard_answer` 标准答案或参考要点，以及已有问答记录。
        你只评价当前这一轮回答，不决定下一题，不继续提问，不输出给学生看的长反馈。

        ## 标准答案使用规则
        - 如果 `question.standard_answer` 非空，必须把它作为本轮评价的主要参考依据。
        - 评价时重点比较学生回答是否覆盖标准答案中的核心知识点、关键推理步骤、重要边界条件和必要工程取舍。
        - 标准答案不是唯一措辞。学生可以使用不同表达、不同顺序或合理补充，只要关键含义与标准答案一致，就应按正确方向评价。
        - 如果学生回答与标准答案冲突，或遗漏标准答案中的关键结论、关键因果链、关键边界条件，应据此降低质量等级。
        - 如果 `question.standard_answer` 为空，才退回到基于题目正文、`question_blocks`、`code_fragments`、学生回答和历史上下文进行评价。

        ## 质量标签
        回答质量只有以下 4 个指标，必须严格输出其中之一：

        ```python
        decay_by_quality = {
            "excellent": 1.0,
            "correct": 1.0,
            "average": 0.7,
            "wrong": 0.5,
        }
        ```

        含义：
        - `excellent`: 回答优秀，完整正确，覆盖关键点，并体现机制、因果链、边界条件或工程理解。
        - `correct`: 回答正确，关键点基本完整，但深度或表达略有不足。
        - `average`: 回答一般，有部分遗漏或表达不完整，但核心方向成立。`average` 也算正确。
        - `wrong`: 回答错误，核心概念、关键因果链或主要判断明显不成立。

        ## 正确性规则
        - `excellent`、`correct`、`average` 都必须判定为 `answer_correct=true`。
        - `wrong` 必须判定为 `answer_correct=false`。

        ## 约束
        - 不要输出标准答案，也不要在 `reason` 中复述标准答案全文。
        - `reason` 只能简要说明学生回答相对标准答案的覆盖程度、遗漏点或错误点。
        - 不要生成下一道题。
        - 不要判断下一步应该更难还是更简单。
        - 不要输出任何流程控制建议。
        - 后续系统会根据 `excellent/correct/average/wrong` 维护状态倍率，所以不要输出其他质量标签。

        ## 输出
        严格输出 JSON：
        {
          "answer_correct": bool,
          "correctness_level": "excellent/correct/average/wrong",
          "reason": "简要说明判断依据"
        }
        """

    async def execute(self, history: str):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        historys = [{"role": "system", "content": self.system_prompt}]
        historys.extend(history)

        response = await self.agent.ainvoke({
            "messages": historys
        })

        await self.stop_heartbeat()
        return response

    def get_response_format(self):
        class ResponseFormat(BaseModel):
            answer_correct: bool
            correctness_level: str
            reason: str

        return ResponseFormat


class StageJudgeAdjudicatorAgent(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        source: str,
        thinking: bool = True,
        response_format: bool = True,
        temperature: float = 0,
        show_tool_io: bool = False,
    ):
        super().__init__(
            "StageJudgeAdjudicatorAgent",
            model_settings,
            thinking,
            response_format,
            temperature,
            show_tool_io=show_tool_io,
        )
        self.source = source
        self.system_prompt = """
        ## 核心职责
        你只接收并整合 N 个 `StageJudgerAgent` 对同一道题的评判结果，不独立重新评分。
        如果输入中只有一个有效评判结果，系统不会调用你；你只处理多个评判结果存在分歧或需要汇总裁决的情况。
        最终输出必须与单个 `StageJudgerAgent` 完全一致，只包含 `answer_correct`、`correctness_level`、`reason`。
        不要输出 `panel_judges`、`adjudicated_by`、`adjudicator_error` 或任何调试字段。

        ## 角色
        你是口试单题评分裁决员，负责根据多个评分 Agent 的结论，对同一道题的学生回答做最终裁决。

        ## 输入
        user message 会提供当前题目、学生回答、历史上下文，以及多个评分 Agent 的评分结果。
        每个评分结果可能包含 answer_correct、correctness_level、reason 或 agent_error。

        ## 裁决规则
        - 只裁决当前这一轮回答，不生成下一题，不输出给学生看的反馈。
        - 优先参考没有 agent_error 的有效评分。
        - 如果多数评分一致，通常采用多数结果。
        - 如果评分分歧明显，结合题目、standard_answer 和各评分 reason 判断最终等级。
        - `excellent`、`correct`、`average` 必须对应 `answer_correct=true`。
        - `wrong` 必须对应 `answer_correct=false`。

        ## 输出
        严格输出 JSON：
        {
          "answer_correct": bool,
          "correctness_level": "excellent/correct/average/wrong",
          "reason": "简要说明最终裁决依据"
        }
        """

    async def execute(self, history: str):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        historys = [{"role": "system", "content": self.system_prompt}]
        historys.extend(history)

        response = await self.agent.ainvoke({
            "messages": historys
        })

        await self.stop_heartbeat()
        return response

    def get_response_format(self):
        class ResponseFormat(BaseModel):
            answer_correct: bool
            correctness_level: str
            reason: str

        return ResponseFormat


class MainJudgerAgent(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        source: str,
        thinking: bool = True,
        response_format: bool = True,
        temperature: float = 0,
        show_tool_io: bool = False,
    ):
        super().__init__(
            "MainJudgerAgent",
            model_settings,
            thinking,
            response_format,
            temperature,
            show_tool_io=show_tool_io,
        )
        self.source = source
        self.system_prompt = """
        ## Role
        你是一名口试答辩总结评审，负责根据完整答辩记录总结学生在本次口试中的整体表现。

        ## Task
        1. 阅读全部题目、学生回答、阶段性评价、维度信息和历史记录。
        2. 总结学生的整体答辩过程，不要重新打分，不要给等级，不要裁决是否通过。
        3. 概括学生的主要优点、主要不足、各维度表现和后续改进建议。
        4. 所有分数由外部系统维护，你不能计算或输出任何分数。

        ## Output
        严格输出 JSON：
        {
            "overall_summary": "对整个答辩过程的总体总结",
            "dimension_summaries": [
                {
                    "dimension": "维度名称",
                    "summary": "该维度表现总结"
                }
            ],
            "strengths": ["主要优点"],
            "weaknesses": ["主要不足"],
            "suggestions": ["后续改进建议"]
        }
        """

    async def execute(self, history: str):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        historys = [{"role": "system", "content": self.system_prompt}]
        historys.extend(history)

        response = await self.agent.ainvoke({
            "messages": historys
        })

        await self.stop_heartbeat()
        return response

    def get_response_format(self):
        class ResponseFormat(BaseModel):
            overall_summary: str
            dimension_summaries: list
            strengths: list
            weaknesses: list
            suggestions: list

        return ResponseFormat


class DocJudgerAgent(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        report_source: str,
        course_id: str,
        exam_id: Optional[str] = None,
        teacher_document_sources: Optional[List[str]] = None,
        report_judge_rule: Optional[str] = None,
    ):
        self.report_source = self._normalize_optional_text(report_source)
        self.course_id = self._normalize_optional_text(course_id)
        self.exam_id = self._normalize_optional_text(exam_id)
        self.teacher_document_sources = self._normalize_source_list(teacher_document_sources)
        self.report_judge_rule = self._normalize_optional_text(report_judge_rule) or self.get_default_report_judge_rule()
        super().__init__(
            "DocJudgerAgent",
            model_settings,
        )
        self.system_prompt = self.build_system_prompt()

    def _normalize_optional_text(self, value: Optional[str]) -> Optional[str]:
        if value is None:
            return None
        value = str(value).strip()
        return value or None

    def _normalize_source_list(self, values: Optional[List[str]]) -> List[str]:
        if not values:
            return []
        result = []
        for value in values:
            normalized = self._normalize_optional_text(value)
            if normalized and normalized not in result:
                result.append(normalized)
        return result

    def get_default_report_judge_rule(self) -> str:
        return (
            "按 100 分制评价学生报告。重点关注：1. 报告是否完整回应课程、实验或考试要求；"
            "2. 是否覆盖关键知识点、实验步骤、结果分析和必要结论；"
            "3. 是否与教师参考资料、实验指导或课程要求一致；"
            "4. 表达是否清晰，能否支撑学生后续口试说明。"
        )

    def build_system_prompt(self) -> str:
        teacher_sources_text = (
            "、".join(self.teacher_document_sources)
            if self.teacher_document_sources
            else "未注册教师参考资料"
        )
        teacher_tool_rule = (
            "如需核对课程、实验或考试要求，可调用 `teacherSearch(document_name=教师资料名称, query=\"\")`；"
            f"可用教师资料名称：{teacher_sources_text}。"
            if self.teacher_document_sources
            else "当前未注册 `teacherSearch`，不得声称已经核对教师参考资料。"
        )
        return f"""
        ## Role
        你是报告评价 Agent，负责根据学生提交的报告内容、教师设置的报告评判规则，以及必要的教师参考资料，对报告完成情况进行客观打分。

        ## Tools
        - `reportSearch`：只用于检索学生报告内容。必须优先调用 `reportSearch(query="", batch_index=0)` 读取学生报告。
        - 如果 `reportSearch` 返回 `has_more=true`，应保持相同 query，并使用 `next_batch_index` 继续读取必要批次。
        - `teacherSearch`：只用于检索教师参考资料，不能用于读取学生报告。{teacher_tool_rule}
        - 不要混淆两类证据：学生报告证据只能来自 `reportSearch`，教师要求或参考依据只能来自 `teacherSearch` 或用户输入。

        ## Report Judge Rule
        {self.report_judge_rule}

        ## Evaluation Focus
        1. 内容完整性：报告是否覆盖任务背景、方法或步骤、关键过程、结果、分析和结论。
        2. 关键知识点覆盖：报告是否体现课程、实验或考试要求中的核心概念、原理、推理链和边界条件。
        3. 参考一致性：报告是否与教师资料、实验指导、评分规则或课程要求一致；没有教师资料时，只基于报告和输入规则判断。
        4. 口试支撑度：报告表达是否足以支撑学生在口试中解释自己的工作，而不是只堆砌文本。

        ## Constraints
        - 如果没有检索到学生报告内容，不要臆造报告，应按报告缺失或证据不足处理。
        - 不要评价口试问答本身；只评价报告。口试表现只能作为判断报告表达支撑度的辅助上下文。
        - 不要输出工具调用过程或大段原文，只输出结论、依据、扣分点和建议。

        ## Output
        严格输出 JSON：
        {{
          "report_score": 0,
          "completion_level": "excellent/good/pass/weak/missing",
          "content_completeness": "报告完整性评价",
          "key_points_coverage": "关键知识点覆盖情况",
          "reference_consistency": "与教师资料或要求的一致性",
          "oral_support": "报告是否能支撑口试表现",
          "evidence": ["来自学生报告的关键依据"],
          "deductions": ["扣分点"],
          "suggestions": ["改进建议"]
        }}
        """

    def get_tools(self):
        @tool(args_schema=DocReportSearchToolInput, description=DocReportSearchDescription)
        async def reportSearch(query: str = "", batch_index: int = 0, target_tokens: int = 6000) -> str:
            search_tool = SearchTool("doc_report_search_tool")
            return await search_tool.execute(
                query=query,
                source=self.report_source,
                course_id=self.course_id,
                exam_id=self.exam_id,
                batch_index=batch_index,
                target_tokens=target_tokens,
            )

        @tool(args_schema=DocTeacherSearchToolInput, description=DocTeacherSearchDescription)
        async def teacherSearch(
            document_name: str,
            query: str = "",
            batch_index: int = 0,
            target_tokens: int = 6000,
        ) -> str:
            document_name = str(document_name or "").strip()
            if document_name not in self.teacher_document_sources:
                return json.dumps(
                    {
                        "error": "TEACHER_DOCUMENT_SOURCE_NOT_ALLOWED",
                        "document_name": document_name,
                        "teacher_document_sources": self.teacher_document_sources,
                    },
                    ensure_ascii=False,
                )
            search_tool = SearchTool("doc_teacher_search_tool")
            return await search_tool.execute(
                query=query,
                source=document_name,
                course_id=self.course_id,
                exam_id=None,
                batch_index=batch_index,
                target_tokens=target_tokens,
            )

        tools = []
        if self.report_source and self.course_id:
            tools.append(reportSearch)
        if self.teacher_document_sources and self.course_id:
            tools.append(teacherSearch)
        return tools

    async def execute(self, history: str):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        historys = [{"role": "system", "content": self.system_prompt}]
        if history:
            historys.extend(history)

        try:
            response = await self.agent.ainvoke({
                "messages": historys
            })
            return response
        finally:
            await self.stop_heartbeat()

    def get_response_format(self):
        class ResponseFormat(BaseModel):
            report_score: float
            completion_level: str
            content_completeness: str
            key_points_coverage: str
            reference_consistency: str
            oral_support: str
            evidence: list
            deductions: list
            suggestions: list

        return ResponseFormat
