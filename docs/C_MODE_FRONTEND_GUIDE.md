# C 模式前端接入

## 1. 实时消息

C 模式通过 `c-exam-event` 发送流程状态。题目展示仍使用原有文本通道；最终评审 HTML 不走文本通道，也不放进实时事件。

```json
{
  "type": "c-exam-event",
  "data": {"type": "final_review_ready", "exam_id": "本次考试ID"}
}
```

阶段性的 `judgement_update`、`judgement_error`、`probe_score_update` 和候选追问预生成信息只在后台处理。题链完成消息不携带分数或评价历史，题目消息不携带参考答案。

| data.type | 含义 |
| --- | --- |
| question | 主问题就绪 |
| answer_received | 已接收回答 |
| answer_finish_confirmation_required | 提示用户确认本轮回答是否结束 |
| generated_question_ready | 下一道追问就绪 |
| followup_generation_pending | 等待追问生成，服务层负责轮询 |
| followup_generation_error | 追问生成失败 |
| question_probe_completed | 当前题链完成 |
| root_question_completed | 当前主问题完成 |
| final_review_ready | 最终评审已保存，可调用报告接口 |
| final_review_error | 总评生成或保存失败，可重试 |
| finished | 全部主问题完成，结束会话 |
| closed | 主动结束会话 |
| waiting / error | 等待状态或操作错误 |

## 2. 回答与追问

连接建立后，服务层自动启动题目流程。用户回答通过现有 `user-stt`、`answer_text` 或 `user-text` 消息输入；服务层负责缓冲并提交内部 `answer_chunk`。

服务层请求结束确认后，前端发送 `answer_finish_confirmation`（含 `finished`）或 `answer_end` 确认本轮结束。

每道主问题正常追问 3 次。主问题和未选中的候选追问不计入这 3 次；答中核心观点不会提前结束。第 3 次追问回答结束后切换下一道主问题；轮询发现追问生成失败也会结束当前题链。

## 3. 报告生成与保存

每道主问题及其追问链完成后，后端定稿一份最终评价，包含最终分数、掌握的核心点、观察到的错误及评价结论。

总结 Agent 只接收这些定稿评价、确定的分数和完成状态，不接收原始回答分块、阶段判断或计分历史。Agent 生成报告正文 HTML；后端清理标签和属性、补充固定样式与确定分数后保存完整 HTML。

`exam_sessions` 中：

- `final_review_html MEDIUMTEXT NULL`：最终展示的完整 HTML。
- `final_review_json`：定稿评价、成绩和完成状态。
- `exam_score`：学生实得分；`total_score` 仍保留配置总分。
- `exam_dimension_scores_json`：本次报告的得分明细。

同一事务保存报告和成绩。数据库按考试 ID 加锁，避免多个工作进程重复生成。已保存报告直接复用；保存失败时保留本次已生成内容用于当前会话重试。

## 4. 按需读取报告

`GET /exam_sessions/{exam_id}/final_review`

使用现有登录鉴权。学生只能读取自己且属于已加入课程的报告；教师只能读取有权限课程的报告；管理员沿用现有查询权限。

成功响应：

```json
{
  "success": true,
  "data": {
    "exam_id": "本次考试ID",
    "html": "<!doctype html><html lang=\"zh-CN\">...</html>"
  }
}
```

| HTTP 状态 | 含义 |
| --- | --- |
| 200 | 已保存的报告 HTML |
| 403 | 无权访问 |
| 404 | 考试不存在 |
| 409 | 报告尚未就绪 |
| 500 | 查询失败 |

报告是完整 HTML 文档，建议在隔离的报告视图或无脚本权限的 iframe 中展示。历史页面也调用此接口，不重新生成报告。考试列表不包含 HTML 正文。

## 5. 结束顺序与重试

自然完成：

```text
题链全部完成
→ 总结 Agent 生成 HTML
→ 校验并提交数据库
→ final_review_ready（仅考试 ID）
→ finished
→ EndFrame
```

主动发送 `{"type":"finish"}` 时，根据当前已收集评价生成报告，结束事件为 `closed`；尚未完成的题目明确标记，报告状态为 `partial`。

生成或保存失败时：

```json
{
  "type": "c-exam-event",
  "data": {
    "type": "final_review_error",
    "exam_id": "本次考试ID",
    "error": "FINAL_REVIEW_FAILED",
    "retryable": true
  }
}
```

此时不会发送就绪通知或结束帧。前端可发送 `{"type":"finish"}` 重试。报告生成期间不再接收新的回答；保存成功后才通知就绪并结束。重连时如已有报告，直接通知就绪，不重新出题。

前端应将报告就绪通知与普通对话渲染分开处理；不要再等待文本通道中的最终评审 HTML。
