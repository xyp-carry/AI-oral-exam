from AIOralExamSystem.Agent.base_Agent import BaseAgent
from langchain_core.tools import tool
from pydantic import BaseModel


class JudgerAgent(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        source: str,
        thinking: bool = False,
        response_format: bool = False,
        temperature: float = 0,
    ):
        super().__init__(
            "JudgerAgent",
            model_settings,
            thinking,
            response_format,
            temperature,
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
    ):
        super().__init__(
            "StageJudgerAgent",
            model_settings,
            thinking,
            response_format,
            temperature,
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
        回答质量只有以下 5 个指标，必须严格输出其中之一：

        ```python
        decay_by_quality = {
            "excellent": 1.0,
            "correct": 1.0,
            "average": 0.7,
            "wrong": 0.5,
            "absurd": 0.3,
        }
        ```

        含义：
        - `excellent`: 回答优秀，完整正确，覆盖关键点，并体现机制、因果链、边界条件或工程理解。
        - `correct`: 回答正确，关键点基本完整，但深度或表达略有不足。
        - `average`: 回答一般，有部分遗漏或表达不完整，但核心方向成立。`average` 也算正确。
        - `wrong`: 回答错误，核心概念、关键因果链或主要判断明显不成立。
        - `absurd`: 回答离谱、答非所问、基本空答，或内容与问题无关。

        ## 正确性规则
        - `excellent`、`correct`、`average` 都必须判定为 `answer_correct=true`。
        - `wrong`、`absurd` 必须判定为 `answer_correct=false`。

        ## 约束
        - 不要输出标准答案，也不要在 `reason` 中复述标准答案全文。
        - `reason` 只能简要说明学生回答相对标准答案的覆盖程度、遗漏点或错误点。
        - 不要生成下一道题。
        - 不要判断下一步应该更难还是更简单。
        - 不要输出任何流程控制建议。
        - 后续系统会根据 `excellent/correct/average/wrong/absurd` 维护状态倍率，所以不要输出其他质量标签。

        ## 输出
        严格输出 JSON：
        {
          "answer_correct": bool,
          "correctness_level": "excellent/correct/average/wrong/absurd",
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


class MainJudgerAgent(BaseAgent):
    def __init__(
        self,
        model_settings: dict,
        source: str,
        thinking: bool = True,
        response_format: bool = True,
        temperature: float = 0,
    ):
        super().__init__(
            "MainJudgerAgent",
            model_settings,
            thinking,
            response_format,
            temperature,
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
