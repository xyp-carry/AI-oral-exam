import json
from typing import Any, Dict, List, Optional

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


class StandardAnswerJudgerAgent(BaseAgent):
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
            "StandardAnswerJudgerAgent",
            model_settings,
            thinking,
            response_format,
            temperature,
            show_tool_io=show_tool_io,
        )
        self.source = source
        self.system_prompt = """
        ## Role
        You are a standard-answer judge. You only evaluate whether the student's
        answer matches the standard answer for the current question.

        ## Input
        The user message is JSON with these fields:
        - question: current question text
        - standard_answer: standard/reference answer
        - student_answer: student's answer
        - question_dimension: question dimension
        - difficulty: question difficulty

        ## Task
        Judge only from question, standard_answer, and student_answer.
        The standard answer is the main reference, but equivalent wording,
        different order, and reasonable additions are acceptable.

        ## Levels
        correctness_level must be exactly one of these 4 values:
        - fully_correct: covers the core conclusion, key steps, and key conditions with no obvious error.
        - mostly_correct: covers the main points, with minor omissions or imperfect expression, but the main conclusion is correct.
        - slightly_correct: only mentions scattered relevant points; core conclusion is incomplete or key reasoning is missing.
        - wrong: conflicts with the standard answer, has core conceptual errors, is off-topic, or has almost no valid content.

        ## Correctness mapping
        - fully_correct and mostly_correct must output answer_correct=true.
        - slightly_correct and wrong must output answer_correct=false.

        ## Constraints
        - Do not generate the next question.
        - Do not decide difficulty changes or flow-control actions.
        - Do not provide long student-facing feedback.
        - Do not repeat the full standard answer.
        - reason should briefly explain coverage, omissions, or errors.

        ## Output
        Strict JSON only:
        {
          "answer_correct": bool,
          "correctness_level": "fully_correct/mostly_correct/slightly_correct/wrong",
          "reason": "brief reason"
        }

        """

    async def execute(
        self,
        payload: Optional[Dict[str, Any]] = None,
        history: Optional[List[Dict[str, str]]] = None,
    ):
        self.set_heartbeat_interval(1)
        await self.start_heartbeat()

        historys = [{"role": "system", "content": self.system_prompt}]
        if history is not None:
            historys.extend(history)
        else:
            historys.append({
                "role": "user",
                "content": json.dumps(payload or {}, ensure_ascii=False),
            })

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

