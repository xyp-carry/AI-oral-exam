import { useState, useRef } from 'react'
import type { ChatStatus } from '../types'
import type {
  CModePhase, CModeQuestion, CModeJudgement,
  CModeCompletedInfo, CModeConfirmPrompt,
} from '../hooks/useCModeExam'
import { renderContent } from '../utils/renderContent'

interface CModePanelProps {
  chatStatus: ChatStatus
  connected: boolean
  starting: boolean
  remoteAudioStarted: boolean
  audioDetected: boolean
  pttActive: boolean
  onTogglePTT: () => void
  onSendText: (text: string) => void
  phase: CModePhase
  currentQuestion: CModeQuestion | null
  answerRaw: string
  answerCorrected: string
  judgement: CModeJudgement | null
  completedInfo: CModeCompletedInfo | null
  errorMsg: string | null
  waitingReason: string | null
  closedInfo: { exam_completed?: boolean } | null
  confirmPrompt: CModeConfirmPrompt | null
  finalReport: string
  onAnswerEnd: () => void
  onFinish: () => void
  onConfirmAnswerFinish: (finished: boolean) => void
}

const PHASE_META: Record<CModePhase, { text: string; cls: string }> = {
  idle: { text: '等待开始', cls: 'idle' },
  question_active: { text: '等待回答', cls: 'active' },
  judging: { text: '判断中', cls: 'busy' },
  followup_generating: { text: '正在准备下一题', cls: 'busy' },
  probe_completed: { text: '本题完成', cls: 'done' },
  closed: { text: '口试已结束', cls: 'closed' },
}

const ANSWER_STATUS_LABELS: Record<string, string> = {
  none: '尚未命中',
  partial: '部分正确',
  correct: '回答正确',
  wrong: '回答有误',
}

export default function CModePanel(props: CModePanelProps) {
  const {
    chatStatus, connected, starting, remoteAudioStarted, audioDetected,
    pttActive, onTogglePTT, onSendText,
    phase, currentQuestion, answerRaw, answerCorrected,
    judgement, completedInfo, errorMsg, waitingReason, closedInfo,
    confirmPrompt, onAnswerEnd, onFinish, onConfirmAnswerFinish, finalReport,
  } = props

  const [textInput, setTextInput] = useState('')
  const [showRaw, setShowRaw] = useState(false)
  const textInputRef = useRef<HTMLInputElement>(null)

  const handleTextSend = () => {
    const trimmed = textInput.trim()
    if (!trimmed || !connected) return
    onSendText(trimmed)
    setTextInput('')
    textInputRef.current?.focus()
  }

  const handleTextKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleTextSend()
    }
  }

  const phaseMeta = PHASE_META[phase]
  // 前端只区分"等待回答"这一个交互状态：题目已出且口试未结束即可作答
  const canAnswer = connected && phase === 'question_active'

  return (
    <div className="content">
      <div className="cmode-container">
        <div className="chat-header">
          <span className="chat-title">
            <i className="fas fa-microphone-lines"></i> C 模式口试
          </span>
          <span className={`chat-status ${chatStatus.ready ? 'connected' : ''}`}>
            <span className={`status-dot ${chatStatus.ready ? 'online' : 'offline'}`}></span>
            {chatStatus.text}
          </span>
        </div>

        <div className="cmode-body">
          {/* 状态徽章行 */}
          <div className="cmode-status-row">
            <span className={`cmode-phase-badge ${phaseMeta.cls}`}>
              <i className={`fas ${phase === 'closed' ? 'fa-flag-checkered' : phaseMeta.cls === 'busy' ? 'fa-spinner fa-spin' : 'fa-circle'}`}></i>
              {phaseMeta.text}
            </span>
          </div>

          {/* 未开始 */}
          {phase === 'idle' && !starting && (
            <div className="cmode-empty">
              <i className="fas fa-hourglass-half"></i>
              <p>{connected ? '正在等待第一道题目…' : '点击 Start 开始 C 模式口试'}</p>
            </div>
          )}

          {/* 题目卡片：不区分根问题/追问，仅展示当前题目 */}
          {currentQuestion && phase !== 'idle' && (
            <div className="cmode-question-card">
              {currentQuestion.content ? (
                <div
                  className="cmode-question-content ai-md"
                  dangerouslySetInnerHTML={renderContent(currentQuestion.content)}
                />
              ) : (
                <div className="cmode-question-content">（题目内容为空）</div>
              )}
            </div>
          )}

          {/* 本题完成 */}
          {phase === 'probe_completed' && completedInfo && (
            <div className="cmode-banner completed">
              <i className="fas fa-check-double"></i>
              <div>
                <div className="cmode-banner-title">
                  本题完成
                </div>
              </div>
            </div>
          )}

          {/* 已结束 */}
          {phase === 'closed' && (
            <div className="cmode-banner closed-banner">
              <i className="fas fa-flag-checkered"></i>
              <div>
                <div className="cmode-banner-title">口试已结束</div>
                <div className="cmode-banner-sub">
                  {closedInfo?.exam_completed ? '考试全部完成' : '会话已关闭'}
                </div>
              </div>
            </div>
          )}

          {/* 口试结束后的最终报告（后端经 bot-llm-text 推送的 HTML，与 A 模式一致） */}
          {phase === 'closed' && finalReport && (
            <div className="cmode-question-card">
              <div className="cmode-question-header">
                <span><i className="fas fa-file-alt"></i> 最终报告</span>
              </div>
              <div
                className="cmode-question-content ai-md"
                dangerouslySetInnerHTML={renderContent(finalReport)}
              />
            </div>
          )}

          {/* 等待原因 */}
          {waitingReason && phase !== 'closed' && (
            <div className="cmode-banner waiting">
              <i className="fas fa-pause-circle"></i>
              <div className="cmode-banner-title">等待中：{waitingReason}</div>
            </div>
          )}

          {/* 错误 */}
          {errorMsg && (
            <div className="cmode-banner error">
              <i className="fas fa-exclamation-triangle"></i>
              <div className="cmode-banner-title">{errorMsg}</div>
            </div>
          )}

          {/* 回答区 */}
          {currentQuestion && phase !== 'idle' && phase !== 'closed' && (
            <div className="cmode-answer-card">
              <div className="cmode-answer-header">
                <span><i className="fas fa-user"></i> 我的回答</span>
                {answerRaw && answerRaw !== answerCorrected && (
                  <button className="cmode-raw-toggle" onClick={() => setShowRaw(v => !v)}>
                    {showRaw ? '查看修正后' : '查看原始'}
                  </button>
                )}
              </div>
              <div className="cmode-answer-text">
                {answerCorrected || answerRaw || (
                  <span className="cmode-answer-placeholder">
                    按住麦克风说话，或使用下方输入框输入回答
                  </span>
                )}
              </div>
              {showRaw && answerRaw && (
                <div className="cmode-answer-raw">{answerRaw}</div>
              )}

              {/* 判断信息 */}
              {judgement && (
                <div className="cmode-judge-box">
                  {judgement.answer_status && (
                    <span className={`cmode-judge-status ${judgement.answer_status}`}>
                      {ANSWER_STATUS_LABELS[judgement.answer_status] ?? judgement.answer_status}
                    </span>
                  )}
                  {!!judgement.matched_keywords?.length && (
                    <div className="cmode-kw-line">
                      <span className="cmode-kw-label">已命中：</span>
                      {judgement.matched_keywords.map((k, i) => (
                        <span key={i} className="cmode-kw-chip hit">{k}</span>
                      ))}
                    </div>
                  )}
                  {!!judgement.missing_keywords?.length && (
                    <div className="cmode-kw-line">
                      <span className="cmode-kw-label">未命中：</span>
                      {judgement.missing_keywords.map((k, i) => (
                        <span key={i} className="cmode-kw-chip miss">{k}</span>
                      ))}
                    </div>
                  )}
                  {judgement.has_deepening_content && judgement.deepening_focus && (
                    <div className="cmode-judge-extra">
                      <i className="fas fa-search-plus"></i> 检测到可深挖点：{judgement.deepening_focus}
                    </div>
                  )}
                  {judgement.needs_followup && judgement.followup_focus && (
                    <div className="cmode-judge-extra">
                      <i className="fas fa-reply"></i> 可能触发追问：{judgement.followup_focus}
                    </div>
                  )}
                </div>
              )}
            </div>
          )}
        </div>

        {/* 输入控制区 */}
        <div className="cmode-controls">
          <button
            className={`chat-ptt-btn ${pttActive ? 'recording' : ''}`}
            onClick={onTogglePTT}
            disabled={!connected}
            title={pttActive ? '点击静音' : '点击说话'}
          >
            <i className="fas fa-microphone"></i>
          </button>

          <div className="chat-text-input-wrap">
            <input
              ref={textInputRef}
              type="text"
              className="chat-text-input"
              placeholder={canAnswer ? '输入文字回答（作为一段回答提交）…' : connected ? '等待题目…' : '请先开始会话'}
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={handleTextKeyDown}
              disabled={!canAnswer}
            />
            <button
              className="chat-text-send-btn"
              onClick={handleTextSend}
              disabled={!canAnswer || !textInput.trim()}
              title="发送回答"
            >
              <i className="fas fa-paper-plane"></i>
            </button>
          </div>

          <button
            className="cmode-action-btn answer-end"
            onClick={onAnswerEnd}
            disabled={!canAnswer}
            title="结束当前题回答，触发最终判断"
          >
            <i className="fas fa-check-double"></i> 回答完毕
          </button>

          <button
            className="cmode-action-btn finish"
            onClick={onFinish}
            disabled={!connected || phase === 'closed'}
            title="结束口试"
          >
            <i className="fas fa-flag-checkered"></i> 结束口试
          </button>
        </div>

        {/* 连接提示 */}
        {connected && !remoteAudioStarted && (
          <div className="cmode-hint">
            <i className="fas fa-headphones"></i> 等待音频通道建立…
          </div>
        )}
        {connected && remoteAudioStarted && !audioDetected && phase !== 'closed' && (
          <div className="cmode-hint">
            <i className="fas fa-volume-up"></i> 请点击麦克风按钮开始说话
          </div>
        )}
      </div>

      {/* 回答完毕确认弹窗（后端 answer_finish_confirmation_required 触发） */}
      {confirmPrompt && (
        <div className="cmode-confirm-overlay">
          <div className="cmode-confirm-dialog">
            <div className="cmode-confirm-title">
              <i className="fas fa-circle-question"></i> 回答完毕确认
            </div>
            <div className="cmode-confirm-message">
              {confirmPrompt.message}
              {confirmPrompt.idleTimeoutSecs > 0 && (
                <span className="cmode-confirm-hint">
                  （已 {confirmPrompt.idleTimeoutSecs} 秒未检测到语音输入）
                </span>
              )}
            </div>
            <div className="cmode-confirm-actions">
              <button
                className="cmode-confirm-btn continue"
                onClick={() => onConfirmAnswerFinish(false)}
              >
                <i className="fas fa-pen-nib"></i> 继续作答
              </button>
              <button
                className="cmode-confirm-btn finish"
                onClick={() => onConfirmAnswerFinish(true)}
              >
                <i className="fas fa-check"></i> 结束回答
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
