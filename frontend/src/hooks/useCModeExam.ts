import { useState, useRef, useCallback, useEffect } from 'react'
import type { ShowToastFn } from '../types'

export type CModePhase =
  | 'idle'
  | 'question_active'
  | 'judging'
  | 'followup_generating'
  | 'probe_completed'
  | 'closed'

export interface CModeQuestion {
  question_id: string
  content?: string
  question_content?: string
  standard_answer?: string
  root_question_id?: string
  parent_question_id?: string
  chain_depth?: number
  relation?: string
}

export interface CModeJudgement {
  answer_status?: string
  completion_status?: string
  needs_followup?: boolean
  followup_trigger?: string
  followup_relation?: string
  followup_focus?: string
  matched_keywords?: string[]
  missing_keywords?: string[]
  has_deepening_content?: boolean
  deepening_focus?: string
  has_contradiction?: boolean
  score_reason?: string
}

export interface CModeCompletedInfo {
  question_id: string
  probe_turn_count?: number
  max_probe_turns?: number
  reason?: string
}

/** 回答完毕确认请求 */
export interface CModeConfirmPrompt {
  questionId: string
  message: string
  idleTimeoutSecs: number
}

const POLL_INTERVAL_MS = 1500

/** C 模式需要处理的后端事件类型（文档第 9 节）；所有考试模式统一走此事件流 */
export const C_MODE_EVENT_TYPES = [
  'question',
  'answer_received',
  'judgement_update',
  'judgement_error',
  'followup_generation_started',
  'followup_generation_pending',
  'generated_question_ready',
  'followup_generation_error',
  'question_probe_completed',
  'answer_finish_confirmation_required',
  'waiting',
  'error',
  'closed',
  'finished',
] as const

export const PROBE_COMPLETED_REASONS: Record<string, string> = {
  complete_after_prompt: '提示后补充完整',
  max_probe_turns_reached: '已达最大追问轮数',
}

export function useCModeExam(params: {
  enabled: boolean
  sendDCMessage: (data: Record<string, unknown>) => void
  onDCMessage: (type: string, handler: (data: Record<string, unknown>) => void) => void
  showToast: ShowToastFn
}) {
  const { enabled, sendDCMessage, onDCMessage } = params

  const [phase, setPhase] = useState<CModePhase>('idle')
  const [currentQuestion, setCurrentQuestion] = useState<CModeQuestion | null>(null)
  const [answerRaw, setAnswerRaw] = useState('')
  const [answerCorrected, setAnswerCorrected] = useState('')
  const [probeTurnCount, setProbeTurnCount] = useState<number | null>(null)
  const [maxProbeTurns, setMaxProbeTurns] = useState<number | null>(null)
  const [judgement, setJudgement] = useState<CModeJudgement | null>(null)
  const [followupFocus, setFollowupFocus] = useState<string | null>(null)
  const [completedInfo, setCompletedInfo] = useState<CModeCompletedInfo | null>(null)
  const [errorMsg, setErrorMsg] = useState<string | null>(null)
  const [waitingReason, setWaitingReason] = useState<string | null>(null)
  const [closedInfo, setClosedInfo] = useState<{ exam_completed?: boolean } | null>(null)
  const [confirmPrompt, setConfirmPrompt] = useState<CModeConfirmPrompt | null>(null)
  const [finalReport, setFinalReport] = useState('')
  /** 收到 finished 事件（考试全部结束），页面据此结束会话并返回考试列表 */
  const [examFinished, setExamFinished] = useState(false)

  const phaseRef = useRef<CModePhase>('idle')
  const currentQuestionRef = useRef<CModeQuestion | null>(null)
  const pollTimerRef = useRef<number | null>(null)
  const startedRef = useRef(false)
  // bot-llm-text 输出的题目正文（HTML 片段，可能分片到达）累积器
  const botTextRef = useRef('')
  // 当前题是否已提交过回答，用于判断 bot 正文属于本题流式还是新题开始
  const answeredRef = useRef(false)
  // 是否正在等待用户"回答完毕确认"：期间后端会用 TTS 播报确认消息（bot-llm-text），
  // 这些播报不是新题正文，不能触发新题清理（否则会把确认弹窗清掉）
  const confirmPendingRef = useRef(false)
  // 口试结束后推送的最终报告 HTML（bot-llm-text 分片）累积器
  const finalReportRef = useRef('')

  const setPhaseBoth = useCallback((p: CModePhase) => {
    phaseRef.current = p
    setPhase(p)
  }, [])

  const stopPolling = useCallback(() => {
    if (pollTimerRef.current !== null) {
      clearInterval(pollTimerRef.current)
      pollTimerRef.current = null
    }
  }, [])

  const startPolling = useCallback(() => {
    if (pollTimerRef.current !== null) return
    pollTimerRef.current = window.setInterval(() => {
      sendDCMessage({ type: 'poll_generated_question' })
    }, POLL_INTERVAL_MS)
  }, [sendDCMessage])

  useEffect(() => () => stopPolling(), [stopPolling])

  /** 应用新题目（根问题或追问题），重置回答相关状态 */
  const applyQuestion = useCallback((qRaw: Record<string, unknown>) => {
    const q = qRaw as unknown as CModeQuestion
    const structured = q.content || q.question_content || ''
    // 结构化事件自带正文则重新开始累积；否则保留 bot-llm-text 先到的正文
    if (structured) botTextRef.current = ''
    answeredRef.current = false
    const normalized: CModeQuestion = {
      ...q,
      content: structured || botTextRef.current,
    }
    currentQuestionRef.current = normalized
    setCurrentQuestion(normalized)
    setAnswerRaw('')
    setAnswerCorrected('')
    setJudgement(null)
    setCompletedInfo(null)
    setErrorMsg(null)
    setWaitingReason(null)
    setConfirmPrompt(null)
    confirmPendingRef.current = false
  }, [])

  /** 接收 bot-llm-text 推送的题目正文（HTML 片段），累积合并到当前题目 */
  const receiveBotText = useCallback((text: string) => {
    if (!enabled || !text) return
    // 口试结束后：后端会像 A 模式一样推送最终报告 HTML（bot-llm-text 分片），
    // 累积到最终报告，不再当题目处理
    if (phaseRef.current === 'closed') {
      finalReportRef.current += text
      setFinalReport(finalReportRef.current)
      return
    }
    // 确认弹窗等待期间：后端 TTS 播报的确认文案（"你是否已经回答完毕？"）不是题目正文，
    // 直接忽略，避免被误判为新题而清掉弹窗
    if (confirmPendingRef.current) return
    const prev = currentQuestionRef.current
    // 当前题已作答、或上一题已完成时收到新正文 → 判定为新题开始：
    // 清空旧题气泡内容与回答区，学生聚焦新题
    const isNewQuestion = phaseRef.current === 'probe_completed' || answeredRef.current
    let base: CModeQuestion
    if (isNewQuestion) {
      botTextRef.current = ''
      answeredRef.current = false
      base = { question_id: '' }
      setAnswerRaw('')
      setAnswerCorrected('')
      setJudgement(null)
      setCompletedInfo(null)
      setErrorMsg(null)
      setWaitingReason(null)
      setConfirmPrompt(null)
      confirmPendingRef.current = false
    } else {
      base = prev ?? { question_id: '' }
    }
    botTextRef.current += text
    const updated: CModeQuestion = {
      ...base,
      content: botTextRef.current,
    }
    currentQuestionRef.current = updated
    setCurrentQuestion(updated)
    // 新题目正文到达即进入等待回答（含 idle 与上一题完成的场景）
    if (isNewQuestion || phaseRef.current === 'idle') {
      setPhaseBoth('question_active')
    }
  }, [enabled, setPhaseBoth])

  /** 统一处理后端事件（文档第 9 节全部类型） */
  const handleEvent = useCallback((data: Record<string, unknown>) => {
    const type = data.type as string

    switch (type) {
      case 'question': {
        const q = (data.question ?? data) as Record<string, unknown>
        applyQuestion(q)
        setFollowupFocus(null)
        setPhaseBoth('question_active')
        break
      }
      case 'answer_received': {
        const ans = data.answer as Record<string, unknown> | undefined
        if (ans) {
          if (typeof ans.text === 'string') setAnswerRaw(ans.text)
          if (typeof ans.corrected_text === 'string' && ans.corrected_text) setAnswerCorrected(ans.corrected_text)
          else if (typeof ans.text === 'string' && !ans.corrected_text) setAnswerCorrected(ans.text)
        }
        break
      }
      case 'judgement_update': {
        const ans = data.answer as Record<string, unknown> | undefined
        if (ans) {
          if (typeof ans.text === 'string') setAnswerRaw(ans.text)
          if (typeof ans.corrected_text === 'string' && ans.corrected_text) setAnswerCorrected(ans.corrected_text)
        }
        setJudgement((data.result ?? null) as CModeJudgement | null)
        setPhaseBoth(phaseRef.current === 'closed' ? 'closed' : 'question_active')
        break
      }
      case 'judgement_error': {
        const msg = (data.message as string) || (data.reason as string) || '判断失败'
        setErrorMsg(msg)
        setPhaseBoth('question_active')
        break
      }
      case 'followup_generation_started': {
        setFollowupFocus((data.followup_focus as string) || (data.relation as string) || null)
        setPhaseBoth('followup_generating')
        startPolling()
        break
      }
      case 'followup_generation_pending': {
        if (phaseRef.current !== 'followup_generating') setPhaseBoth('followup_generating')
        startPolling()
        break
      }
      case 'generated_question_ready': {
        stopPolling()
        const q = (data.question ?? {}) as Record<string, unknown>
        applyQuestion(q)
        if (typeof data.probe_turn_count === 'number') setProbeTurnCount(data.probe_turn_count)
        if (typeof data.max_probe_turns === 'number') setMaxProbeTurns(data.max_probe_turns)
        setFollowupFocus(null)
        // 前端不区分根问题/追问，统一进入等待回答
        setPhaseBoth('question_active')
        break
      }
      case 'followup_generation_error': {
        stopPolling()
        const msg = (data.message as string) || (data.reason as string) || '追问生成失败'
        setErrorMsg(msg)
        setPhaseBoth('question_active')
        break
      }
      case 'question_probe_completed': {
        stopPolling()
        setCompletedInfo({
          question_id: (data.question_id as string) || currentQuestionRef.current?.question_id || '',
          probe_turn_count: data.probe_turn_count as number | undefined,
          max_probe_turns: data.max_probe_turns as number | undefined,
          reason: data.reason as string | undefined,
        })
        setPhaseBoth('probe_completed')
        break
      }
      case 'answer_finish_confirmation_required': {
        confirmPendingRef.current = true
        setConfirmPrompt({
          questionId: (data.question_id as string) || currentQuestionRef.current?.question_id || '',
          message: (data.message as string) || '你是否已经回答完毕？',
          idleTimeoutSecs: typeof data.idle_timeout_secs === 'number' ? data.idle_timeout_secs : 0,
        })
        break
      }
      case 'waiting': {
        setWaitingReason((data.reason as string) || null)
        break
      }
      case 'error': {
        setErrorMsg((data.message as string) || (data.detail as string) || '发生错误')
        break
      }
      case 'closed': {
        stopPolling()
        confirmPendingRef.current = false
        setConfirmPrompt(null)
        setClosedInfo({ exam_completed: data.exam_completed as boolean | undefined })
        setPhaseBoth('closed')
        break
      }
      case 'finished': {
        // 考试全部结束：清理会话状态，页面收到 examFinished 后直接返回考试列表
        stopPolling()
        confirmPendingRef.current = false
        setConfirmPrompt(null)
        setClosedInfo({ exam_completed: true })
        setPhaseBoth('closed')
        setExamFinished(true)
        break
      }
      default:
        break
    }
  }, [applyQuestion, setPhaseBoth, startPolling, stopPolling])

  // 注册所有 C 模式事件处理器（enabled 时）
  if (enabled) {
    for (const t of C_MODE_EVENT_TYPES) {
      onDCMessage(t, handleEvent)
    }
    // 后端实际信封为 { type: "c-exam-event", data: { type: "question", ... } }，解包后走同一分发
    onDCMessage('c-exam-event', (data) => handleEvent((data.data as Record<string, unknown>) ?? data))
  }

  /** 发送 start，获取第一道根问题 */
  const start = useCallback(() => {
    if (!enabled || startedRef.current) return
    if (!sendDCMessage) return
    startedRef.current = true
    finalReportRef.current = ''
    setFinalReport('')
    setExamFinished(false)
    sendDCMessage({ type: 'start' })
  }, [enabled, sendDCMessage])

  /** 发送一段回答（STT 稳定分段或文本输入），格式与 A 模式一致，不带 question_id */
  const sendAnswerChunk = useCallback((text: string) => {
    const trimmed = text.trim()
    if (!enabled || !trimmed) return
    if (phaseRef.current === 'probe_completed' || phaseRef.current === 'closed') return
    answeredRef.current = true
    sendDCMessage({ type: 'user-text', data: { text: trimmed } })
  }, [enabled, sendDCMessage])

  /** 回答完毕，触发最终判断 */
  const sendAnswerEnd = useCallback(() => {
    if (!enabled) return
    if (phaseRef.current === 'probe_completed' || phaseRef.current === 'closed') return
    sendDCMessage({ type: 'answer_end' })
  }, [enabled, sendDCMessage])

  /** 用户确认是否回答完毕（finished=false 继续作答 / true 结束本题回答） */
  const sendFinishConfirmation = useCallback((finished: boolean) => {
    if (!enabled) return
    confirmPendingRef.current = false
    setConfirmPrompt(null)
    sendDCMessage({ type: 'answer_finish_confirmation', data: { finished } })
  }, [enabled, sendDCMessage])

  /** 结束口试 */
  const finish = useCallback(() => {
    if (!enabled) return
    stopPolling()
    sendDCMessage({ type: 'finish' })
  }, [enabled, sendDCMessage, stopPolling])

  /** 手动触发一次追问轮询 */
  const pollOnce = useCallback(() => {
    if (!enabled) return
    sendDCMessage({ type: 'poll_generated_question' })
  }, [enabled, sendDCMessage])

  return {
    phase,
    currentQuestion,
    answerRaw,
    answerCorrected,
    probeTurnCount,
    maxProbeTurns,
    judgement,
    followupFocus,
    completedInfo,
    errorMsg,
    waitingReason,
    closedInfo,
    confirmPrompt,
    finalReport,
    examFinished,
    start,
    sendAnswerChunk,
    sendAnswerEnd,
    sendFinishConfirmation,
    receiveBotText,
    finish,
    pollOnce,
  }
}
