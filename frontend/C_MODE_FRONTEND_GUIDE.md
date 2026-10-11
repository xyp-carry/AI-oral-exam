# C 模式口试前端对接说明

C 模式通过考试项的 `item_type = "C"` 启用。进入口试后，C 模式不是普通的“提交答案 -> 后端返回下一题”流程，而是事件信号驱动流程。

前端需要按后端返回的 `type` 信号更新 UI 状态，并在追问生成时主动轮询。

## 1. 基本原则

C 模式口试的交互单位是事件，事件字段统一使用 `type`。

主要事件：

| 前端发送 type | 作用 |
| --- | --- |
| `start` | 开始口试，获取第一道根问题 |
| `user-text` | 发送一段学生回答，通常来自 STT 稳定分段 |
| `answer_end` | 当前题回答结束，并触发最终判断 |
| `judge_request` | 等价于一次回答结束判断 |
| `poll_generated_question` | 轮询异步生成的追问 |
| `finish` | 结束口试 |
| `stop` | 停止口试 |

后端返回也是一组消息数组，前端按每条消息的 `type` 处理。

## 2. 开始口试

前端发送：

```json
{
  "type": "start"
}
```

后端返回示例：

```json
[
  {
    "type": "question",
    "question": {
      "question_id": "root_001",
      "content": "题目内容",
      "question_content": "题目内容",
      "standard_answer": "参考答案"
    }
  }
]
```

前端展示：

- 优先展示 `question.content`
- 如果没有，则展示 `question.question_content`
- 保存当前 `question_id`，后续回答事件都要带上它

## 3. 分段发送回答

C 模式会对学生回答进行实时分析，所以前端不要只在最后一次性提交答案。建议每一段稳定的语音识别文本发送一次 `user-text`。

前端发送：

```json
{
  "type": "user-text",
  "question_id": "root_001",
  "text": "学生这一段回答内容"
}
```

后端可能返回：

```json
[
  {
    "type": "answer_received",
    "question_id": "root_001",
    "answer": {
      "question_id": "root_001",
      "text": "累计原始回答",
      "corrected_text": "累计修正后回答",
      "segments": ["学生这一段回答内容"],
      "version": 1,
      "finished": false
    }
  },
  {
    "type": "judgement_update",
    "question_id": "root_001",
    "answer": {
      "text": "累计原始回答",
      "corrected_text": "累计修正后回答",
      "version": 1,
      "finished": false
    },
    "job_id": "c_judge_00000001",
    "result": {
      "fixed_text": "修正后的本段文本",
      "corrected_text": "修正后的本段文本",
      "has_contradiction": false,
      "conflicting_points": [],
      "has_deepening_content": true,
      "deepening_focus": "可深挖点",
      "matched_keywords": ["关键词"],
      "completion_status": "none",
      "answer_status": "partial",
      "needs_followup": true,
      "followup_trigger": "keyword_hit",
      "followup_relation": "deepen",
      "followup_focus": "可深挖点",
      "missing_keywords": [],
      "score_delta": 1,
      "score_reason": "keyword_hit"
    }
  },
  {
    "type": "probe_score_update",
    "question_id": "root_001",
    "answer_version": 1,
    "trigger": "keyword_hit",
    "score_reason": "keyword_hit",
    "delta": 1,
    "score_before": 3,
    "score_after": 4
  },
  {
    "type": "followup_generation_started",
    "generation_id": "c_followup_00000001",
    "question_id": "root_001",
    "answer_version": 1,
    "relation": "deepen",
    "followup_trigger": "keyword_hit",
    "followup_focus": "可深挖点"
  }
]
```

前端处理：

| 后端返回 type | 前端行为 |
| --- | --- |
| `answer_received` | 更新当前回答文本和修正文本 |
| `judgement_update` | 展示判断状态、修正文本、追问触发原因 |
| `judgement_error` | 展示判断失败，可允许重试或继续录入 |
| `probe_score_update` | 更新当前探查分数 |
| `followup_generation_started` | 进入“追问生成中”，开始轮询 |
| `followup_generation_error` | 展示追问生成失败 |

## 4. 结束当前题回答

学生点击“回答完毕”或本轮语音结束时，前端发送 `answer_end`。

```json
{
  "type": "answer_end",
  "question_id": "root_001",
  "text": "最后一段回答"
}
```

也可以发送 `judge_request`：

```json
{
  "type": "judge_request",
  "question_id": "root_001",
  "text": "最后一段回答"
}
```

`answer_end` 和 `judge_request` 在 C 模式中都会作为结束判断处理。

注意：如果 `text` 为空，也可以作为结束标记，但不会触发新的本段文本判断，只会返回当前追问生成状态。

## 5. 轮询追问生成

如果后端返回了 `followup_generation_started` 或 `followup_generation_pending`，前端需要主动轮询：

```json
{
  "type": "poll_generated_question"
}
```

追问还没好时：

```json
[
  {
    "type": "followup_generation_pending",
    "question_id": "root_001",
    "pending_generation_ids": ["c_followup_00000001"]
  }
]
```

追问生成完成时：

```json
[
  {
    "type": "generated_question_ready",
    "generation_id": "c_followup_00000001",
    "source_question_id": "root_001",
    "source_answer_version": 1,
    "relation": "deepen",
    "probe_turn_count": 1,
    "max_probe_turns": 3,
    "probe_limit_reached": false,
    "question": {
      "question_id": "root_001_followup_001",
      "content": "追问题目",
      "question_content": "追问题目",
      "standard_answer": "追问参考答案",
      "root_question_id": "root_001",
      "parent_question_id": "root_001",
      "chain_depth": 1,
      "relation": "deepen",
      "status": "active"
    },
    "result": {
      "question_content": "追问题目",
      "standard_answer": "追问参考答案",
      "relation": "deepen",
      "reason": "为什么生成这道追问"
    }
  }
]
```

前端收到 `generated_question_ready` 后：

1. 将当前题切换为返回的 `question`
2. 保存新的 `question.question_id`
3. 继续按 `user-text` / `answer_end` 流程采集学生对追问的回答

## 6. 当前题链完成

后端可能返回：

```json
{
  "type": "question_probe_completed",
  "question_id": "root_001",
  "probe_score": 4,
  "probe_turn_count": 3,
  "max_probe_turns": 3,
  "reason": "max_probe_turns_reached"
}
```

含义：当前根问题及其追问链已经达到完成条件。

可能的 `reason`：

| reason | 含义 |
| --- | --- |
| `complete_after_prompt` | 学生在提示后补充完整 |
| `max_probe_turns_reached` | 已达到最大追问轮数 |

前端建议：

- 展示“当前题完成”状态
- 禁止继续给当前 `question_id` 发送 `user-text`
- 根据后端是否提供下一题事件决定是否进入下一道根问题

当前代码里 C 模式已有 `question_probe_completed` 信号，但没有单独的 `next_root_question` 事件。如果前端需要自动进入下一道根问题，需要后端补充一个事件，或者在 `question_probe_completed` 后由后端自动切下一根问题。

## 7. 结束口试

前端发送：

```json
{
  "type": "finish"
}
```

或：

```json
{
  "type": "stop"
}
```

后端返回：

```json
[
  {
    "type": "closed",
    "exam_completed": true,
    "pending_generation_ids": []
  }
]
```

## 8. 推荐前端状态机

```text
idle
  -> send start
question_active
  -> send user-text repeatedly
judging
  -> receive judgement_update / probe_score_update
followup_generating
  -> send poll_generated_question repeatedly
followup_active
  -> receive generated_question_ready
  -> send user-text repeatedly
probe_completed
  -> receive question_probe_completed
closed
  -> send finish or stop
```

## 9. 前端必须处理的后端消息类型

| 后端消息 type | 是否必须处理 | 说明 |
| --- | --- | --- |
| `question` | 是 | 展示根问题 |
| `answer_received` | 是 | 更新回答文本 |
| `judgement_update` | 是 | 展示判断结果和追问触发信息 |
| `judgement_error` | 是 | 判断失败处理 |
| `probe_score_update` | 是 | 更新探查分数 |
| `followup_generation_started` | 是 | 开始轮询追问 |
| `followup_generation_pending` | 是 | 继续轮询 |
| `generated_question_ready` | 是 | 切换到追问题目 |
| `followup_generation_error` | 是 | 追问生成失败处理 |
| `question_probe_completed` | 是 | 当前题链完成 |
| `waiting` | 是 | 暂无可用状态，按 reason 处理 |
| `error` | 是 | 展示错误或重试 |
| `closed` | 是 | 口试结束 |

## 10. 和 A/B 模式的主要差异

| 项目 | A/B 模式 | C 模式 |
| --- | --- | --- |
| 答案提交 | 通常一次性提交 | 分段提交 `user-text` |
| 判断时机 | 提交后判断 | 每段回答都可判断 |
| 追问生成 | 普通同步下一题或预设题队列 | 异步生成，需要轮询 |
| 前端状态 | 问题/回答/下一题 | 问题/回答分段/判断/追问生成/追问切换 |
| 主要信号 | 普通问答接口 | `start`、`user-text`、`answer_end`、`poll_generated_question` 等 |

## 11. 最小接入流程

前端最小可按下面流程接入：

1. 发送 `{"type": "start"}`
2. 展示返回的 `question`
3. STT 每得到一段稳定文本，发送 `user-text`
4. 如果收到 `followup_generation_started`，定时发送 `poll_generated_question`
5. 如果收到 `generated_question_ready`，切换到追问并继续回答流程
6. 如果收到 `question_probe_completed`，显示当前题完成
7. 口试结束时发送 `finish`

