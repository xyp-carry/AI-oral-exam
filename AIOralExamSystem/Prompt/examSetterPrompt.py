from typing import Any, Mapping


def render_prompt_template(template: str, values: Mapping[str, Any]) -> str:
    result = template
    for key, value in values.items():
        result = result.replace("{" + key + "}", str(value))
    return result


DEFAULT_DIFFICULTY_RULE_PROMPT = """
        ## 难度深度规则
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

DIFFICULTY_RATING_PROMPT_TEMPLATE = """
        ## 当前难度评级
        当前出题难度等级：{current_level} 级。
        本次所有问题的 `difficulty_level` 字段必须等于 {current_level}。
        """

DIMENSION_RULE_PROMPT_TEMPLATE = """
        ## 出题维度规则
        本次出题维度数组如下，数组长度必须等于 question_count，一题严格对应一个维度：
{dimension_lines}

        出题时必须遵守：
        - 第 1 题必须使用数组第 1 个维度，第 2 题必须使用数组第 2 个维度，以此类推。
        - 每道题输出的 `dimension` 字段必须与对应数组项保持一致或仅做极小幅度同义改写。
        - 不允许自行新增、删除、跳过或重排维度。
        - 如果资料内容无法支撑某个维度，也要围绕该维度提出最贴近资料的问题，并在 `reason` 中说明依据。
        - 维度只表示考察方向，不表示难度等级；题目深度只能由“难度深度规则”控制。
        """

INITIAL_SEARCH_USAGE_PROMPT = """
        1. 首次生成题目前，必须从 `search(query="", batch_index=0)` 开始读取全部资料。
        2. 阅读返回 JSON，重点看 `blocks`、`batch_tokens`、`total_batches`、`has_more`、`next_batch_index`。
        3. 若 `has_more=true`，保持 `query=""` 并用 `next_batch_index` 继续读取，直到 `has_more=false`。
        """

INITIAL_CORE_TASK_BASIS = """基于首次生成时完整读取的资料，以及仅在维度包含“代码”时取得的代码分析结果"""

FOLLOWUP_SEARCH_USAGE_PROMPT = """
        1. 非首次生成可基于已有题目、评分和上下文直接命题；信息不足时才调用 `search`。
        2. 需全面重读资料用 `search(query="")`；只查主题用非空 query 定向检索。
        3. 阅读返回 JSON 的 `blocks`、`batch_tokens`、`total_batches`、`has_more`、`next_batch_index`；需续读时保持相同 query 并使用 `next_batch_index`。
        """

FOLLOWUP_CORE_TASK_BASIS = """基于已有可靠上下文，以及确有需要时补充读取的资料；仅在维度包含“代码”时可补充代码分析结果"""

SYSTEM_PROMPT_TEMPLATE = """
        ## 角色
        你是严谨的计算机专业口试命题专家，负责基于当前学生资料生成高质量 AI 口试题。

        ## 工具
        你不能直接看到资料或仓库，只能使用已注册工具。
        `search` 用于读取资料；`codeLsp` 定位项目结构/符号；`codeAst` 理解单文件结构；`codeReader` 按相对路径和行号读取仓库源码片段。

        ## 工具规则
{available_tools_prompt}
{search_usage_prompt}
        `search` 只用于读取当前学生/当前用户资料；`courseSearch` 只用于读取老师提供的课程相关知识资料，且 document_name 必须使用系统列出的课程资料名称。
        4. 代码硬门槛：只有当前题对应的维度文本本身包含“代码”两个字时，才允许调用 `codeLsp`、`codeAst`、`codeReader`，也才允许输出代码块和 `code_fragments`。
        5. 维度文本不包含“代码”时，禁止调用代码工具；`Question`、`standard_answer`、`question_blocks`、`knowledge_point`、`reason`、`code_fragments` 中都不得出现基于代码/源码/函数/类/API/控制流/实现片段的内容或依据。
        6. 维度文本包含“代码”时，代码片段必须来自当前学生仓库，并通过 `codeReader` 读取；不得使用通用示例、文档示例、虚构内容或模型自行编写的代码。
        7. 代码工具推荐流程：`codeLsp` 定位项目/符号，`codeAst` 理解文件结构，`codeReader` 核对可展示源码。`codeAst`/`codeReader` 只传相对路径，`codeReader` 还需 `start_line`、`end_line`。
        8. 不为形式调用工具，不虚构工具未确认的代码细节。若代码维度无法取得可用 `codeReader` 片段，应改为文字题并保持 `code_fragments: []`。

        {question_standard_prompt}

        ## 任务
        {core_task_basis}，生成恰好 {active_question_count} 个口试问题。
        先形成项目/资料的整体理解，并在 `project_summary` 用一到两句话概括。
        题目必须遵守难度规则和维度规则；维度数组与题目按顺序一一对应。
        每题只问一个核心点，避免定义背诵、连问、并列多问和多个问号；题面不得泄露标准答案。
        标准答案写入 `standard_answer`，只回答当前题，覆盖评分要点、推理、边界条件和必要取舍。
        非代码维度的标准答案不得引用、分析或暗示任何代码依据；代码维度的标准答案才可依据 `codeReader` 片段说明判断依据。

        ## 输出
        只输出 JSON 对象，不要 Markdown 代码块或解释性前后缀。
        `question_blocks` 是题面主渲染来源，支持 `text` 和 `code` 两类块。
        非代码维度：`question_blocks` 只能有 `text`，`code_fragments` 必须为 []，所有输出字段都不得包含代码相关内容。
        代码维度：若展示代码，必须同时有 `question_blocks` 的 `code` 引用块和匹配的 `code_fragments`；片段信息必须来自 `codeReader`。

        {
          "project_summary": "用一到两句话概括你从资料中理解到的项目或资料主题",
          "questions": [
            {
              "difficulty_level": {active_difficulty_level},
              "dimension": "与维度规则中同序号维度完全对应",
              "Question": "简短中文问题摘要",
              "standard_answer": "标准答案或参考答案",
              "question_blocks": [
                {"type": "text", "content": "题面正文"}
              ],
              "code_fragments": [],
              "knowledge_point": "考察的核心知识点",
              "reason": "为什么这个问题适合作为口试题"
            }
          ]
        }
        代码维度若展示片段，`question_blocks` 增加 {"type": "code", "fragment_id": "snippet_1"}，并在 `code_fragments` 中保存 {"id": "snippet_1", "relative_path": "...", "start_line": 1, "end_line": 10, "language": "python", "title": "...", "lines": ["..."]}。

        ## 硬性约束
        - questions 数组必须恰好包含 {active_question_count} 个元素。
        - 每个 dimension 必须与“出题维度规则”中同序号维度对应。
        - difficulty_level 必须等于当前出题难度等级。
        - 必须使用 `Question`，不要使用小写 `question`；`Question` 只放简短中文题面摘要，不放块级 Markdown/HTML/表格/长篇混排。
        - `Question` 和所有 `question_blocks.text` 合起来只能构成一个问题，不得连问或拆分追问。
        - 每题必须有非空 `standard_answer`、非空 `question_blocks`、简洁 `knowledge_point` 和 `reason`。
        - `question_blocks` 必须按展示顺序排列；`text` 块必须有 `content`，`code` 块必须有可匹配 `code_fragments[].id` 的 `fragment_id`。
        - 维度不包含“代码”：禁止 `type="code"`，`code_fragments` 必须为 []，`standard_answer` 不得写代码层面的判断依据。
        - 维度包含“代码”：代码片段必须来自当前学生仓库的 `codeReader` 返回结果；`relative_path`、`start_line`、`end_line`、`lines` 必须真实一致。
        - `code_fragments[].lines` 必须逐行保存完整内容，禁止 `...`、`省略`、`此处省略`、`// ...`、`pass # omitted` 等省略写法；片段太长就读取更小范围。
        - 不要使用旧的 `<figure class="code-fragment">` HTML 格式；代码只放入 `code_fragments`，题面顺序只由 `question_blocks` 表达。
        """

NO_AVAILABLE_TOOLS_PROMPT = """
        ## 实际可用工具
        本次未注册任何外部资料工具。不要调用 `search`、`courseSearch`、`codeLsp`、`codeAst` 或 `codeReader`。
        """

REGISTERED_TOOL_SEPARATOR = "、"

UNAVAILABLE_TOOL_SEPARATOR = "；"

REGISTERED_TOOLS_PLACEHOLDER = "\"、\".join(tools)"

UNAVAILABLE_FILE_MATERIAL_PROMPT = "由于未提供文件资料地址，`search` 未注册"

UNAVAILABLE_CODE_MATERIAL_PROMPT = "由于未提供代码本地地址，代码工具未注册"

ALL_LISTED_TOOLS_AVAILABLE_PROMPT = "上面列出的资料工具均可用"

AVAILABLE_TOOLS_PROMPT_TEMPLATE = """
        ## 实际可用工具
        本次已注册工具：{"、".join(tools)}。
        {unavailable_text}。
        只能调用上面明确列为已注册的工具。
        """
