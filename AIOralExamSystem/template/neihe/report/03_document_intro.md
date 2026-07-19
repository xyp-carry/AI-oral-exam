## 三、文档介绍

### 3.1 模块介绍
| 模块名称 | 模块功能 | 完成质量评估 | 开发过程以及完成度 | 真实性评估 |
| --- | --- | --- | --- | --- |
[FIELD:module_table | type:table | 备注：每个模块一行，取值：真实/存疑/异常]
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

--ps--:开发过程要从开发日志之类的地方查看，如果没有找到，就说没有介绍。## 文档相关评价（doc）
- 优：所有部分全部包含且均达最高标准
- 良：全部包含，部分评估达最高标准
- 中：全部包含，少部分达较高标准
- 差：不完整，且无任何评估达最高标准
- 硬性规则：真实性异常 → 强制差