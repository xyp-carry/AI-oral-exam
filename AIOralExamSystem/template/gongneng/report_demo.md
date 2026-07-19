# 内核评论报告

## 一、基本信息
- 作品名称：[FIELD:project_name]
- 作者：[FIELD:authors]
- 学校：[FIELD:school]
- 项目仓库地址：[FIELD:repo_url]

## 二、文件统计
- 总文件数：[FIELD:total_files]
- 代码文件数：[FIELD:code_files]
- 开发文档与报告数：[FIELD:doc_files]
- 其他文件数：[FIELD:other_files]
- 网盘/外部文档标注：[FIELD:external_doc_flag | 备注：如存在初赛文档存于网盘，填"⚠️ 评委需核查外部网盘文档：{链接/说明}"，否则填"无"]

## 三、文档介绍

### 3.1 模块介绍
| 模块名称 | 模块功能 | 完成质量评估 | 开发过程以及完成度 | 真实性评估 |
| --- | --- | --- | --- | --- |
[FIELD:module_table | type:table | 备注：每个模块一行，真实性评估依据 git_log 判定，取值：真实/存疑/异常]
#### 3.1.1 详细模块介绍
对每个模块的开发过程进行较为全面的介绍而不是简单的描述。

### 3.2 开发目标
- 明确程度：[FIELD:goal_clarity | type:enum(明确,较为明确,不明确)]
- 依据：[FIELD:goal_reason]

### 3.3 开发计划
- 是否合理：[FIELD:plan_reasonable | type:enum(合理,不合理,部分合理)]
- 理由：[FIELD:plan_reason]

### 3.4 开发过程描述完整性
- 是否完整：[FIELD:process_complete | type:enum(完整,部分完整,不完整)]
- 理由：[FIELD:process_reason]

### 3.5 文档相关评价
- 评级：[FIELD:doc_rating | type:enum(优,良,中,差) | source:llm_judge | rubric:doc]
- 理由：[FIELD:doc_rating_reason]
- 真实性异常标记：[FIELD:doc_auth anomaly | 备注：若真实性异常，强制 doc_rating=差]

## 四、代码相关

### 4.1 Git 提交日志分析
- 提交时间段：[FIELD:git_time_range]
- 提交总数：[FIELD:git_commit_count]
- 活跃性评估：[FIELD:git_activity | type:enum(高,中,低)]
- 持续演进性评估：[FIELD:git_evolution | type:enum(持续到比赛结束,中途停滞,几乎无演进)]
- 分析说明：[FIELD:git_analysis_text]

### 4.2 遭遇的代码问题
| 问题描述 | 解决方法 |
| --- | --- |
[FIELD:problem_table | type:table | source:doc_parse+git_log 交叉]

### 4.3 结果展示
- 是否存在结果展示文档：[FIELD:has_result_demo | type:enum(有,无)]
- 备注：[FIELD:result_demo_note]

### 4.4 代码开发评价
- 评级：[FIELD:code_rating | type:enum(优,良,中,差) | rubric:code]
- 理由：[FIELD:code_rating_reason]

## 五、结果演示评价
- 评级：[FIELD:result_demo_rating | type:enum(优,良,中,差) | rubric:result_demo]
- 理由：[FIELD:result_demo_reason]
