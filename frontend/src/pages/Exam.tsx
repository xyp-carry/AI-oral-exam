import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams, useLocation, useNavigate } from 'react-router-dom'
import { useWebRTC } from '../hooks/useWebRTC'
import { useMicrophone } from '../hooks/useMicrophone'
import { useToast } from '../hooks/useToast'
import { useExamSession } from '../hooks/useExamSession'
import { useCModeExam } from '../hooks/useCModeExam'
import Sidebar from '../components/Sidebar'
import ChatPanel from '../components/ChatPanel'
import type { ChatPanelHandle } from '../components/ChatPanel'
import CModePanel from '../components/CModePanel'

/** A 模式用于驱动 UI 状态的哨兵串，C 模式下直接忽略 */
const BOT_TEXT_SENTINELS = new Set([
  'AI口试开始思考',
  'AI口试开始回答',
  'AI口试结束回答',
  'AI评审开始评估',
  'AI评审结束评估',
  'AI考试全部结束',
])

export default function Exam() {
  const { examId } = useParams<{ examId: string }>()
  const location = useLocation()
  const navigate = useNavigate()
  const examType = (location.state as { exam_type?: string } | null)?.exam_type ?? null
  const cModeActive = examType === 'C'
  const { toasts, showToast } = useToast()
  const { sessionId, chatStatus, starting, remoteAudioStarted, audioDetected, dcOpen, startSession, closeSession, sendDCMessage, onDCMessage, replaceAudioTrack, cleanupConnection } = useWebRTC(showToast)
  const {
    micStream, micReady, micDevices, selectedDeviceId,
    micStatus, micLevel, enumerateDevices, selectDevice, setMicEnabled, setOnTrackChange,
  } = useMicrophone(showToast)
  const { setExamActive } = useExamSession()

  const cMode = useCModeExam({
    enabled: cModeActive,
    sendDCMessage,
    onDCMessage,
    showToast,
  })

  const [pttActive, setPttActive] = useState(false)
  const chatPanelRef = useRef<ChatPanelHandle>(null)
  const evaluatingRef = useRef(false)

  useEffect(() => {
    setExamActive(!!sessionId)
  }, [sessionId, setExamActive])

  useEffect(() => {
    setOnTrackChange((newTrack: MediaStreamTrack) => {
      console.log('[Exam] 麦克风 track 变更, new label:', newTrack.label, 'enabled:', newTrack.enabled)
      if (sessionId) {
        replaceAudioTrack(newTrack)
      }
    })
    return () => {
      setOnTrackChange(null)
    }
  }, [sessionId, replaceAudioTrack, setOnTrackChange])

  useEffect(() => {
    return () => {
      cleanupConnection()
    }
  }, [cleanupConnection])

  const togglePTT = useCallback(() => {
    if (!micReady) {
      showToast('error', '请先选择麦克风')
      return
    }
    if (!sessionId) {
      showToast('error', '请先点击 Start 开始会话')
      return
    }

    const newActive = !pttActive
    setPttActive(newActive)
    setMicEnabled(newActive)
    sendDCMessage({ type: null, message: newActive ? 'mic_on' : 'mic_off' })
    console.log(`[PTT] ${newActive ? '开启' : '关闭'}麦克风, sessionId: ${sessionId}`)
  }, [pttActive, micReady, sessionId, setMicEnabled, sendDCMessage, showToast])

  /** 结束回答时重置麦克风为关闭状态 */
  const deactivateMic = useCallback(() => {
    setPttActive(false)
    setMicEnabled(false)
    if (sessionId) {
      sendDCMessage({ type: null, message: 'mic_off' })
      console.log(`[PTT] 结束回答，重置麦克风为关闭, sessionId: ${sessionId}`)
    }
  }, [sessionId, setMicEnabled, sendDCMessage])

  useEffect(() => {
    enumerateDevices()
    navigator.mediaDevices?.addEventListener('devicechange', enumerateDevices)
    return () => {
      navigator.mediaDevices?.removeEventListener('devicechange', enumerateDevices)
    }
  }, [])

  onDCMessage('user-llm-text', useCallback((data: Record<string, unknown>) => {
    const text = (data.data as Record<string, unknown>)?.text as string
    if (!text) return
    if (cModeActive) {
      // C 模式：后端回显的用户文本不回传。
      // 语音 STT 由后端自行处理；若此处中继会形成 user-text ↔ user-llm-text 死循环
      return
    }
    chatPanelRef.current?.receiveUserMessage(text)
  }, [cModeActive]))

  onDCMessage('bot-llm-text', useCallback((data: Record<string, unknown>) => {
    const text = (data.data as Record<string, unknown>)?.text as string
    if (!text) return
    if (cModeActive) {
      // C 模式：题目正文经 bot-llm-text 以 HTML 片段推送，累积到当前题目；A 模式哨兵串忽略
      if (!BOT_TEXT_SENTINELS.has(text)) cMode.receiveBotText(text)
      return
    }
    if (text === 'AI口试开始思考') {
      chatPanelRef.current?.startAIThinking()
    } else if (text === 'AI口试开始回答') {
      chatPanelRef.current?.startAIAnswering()
    } else if (text === 'AI口试结束回答') {
      chatPanelRef.current?.finishAIResponse()
    } else if (text === 'AI评审开始评估') {
      evaluatingRef.current = true
      chatPanelRef.current?.startEvaluating()
    } else if (text === 'AI评审结束评估') {
      evaluatingRef.current = false
      chatPanelRef.current?.stopEvaluating()
    } else if (text === 'AI考试全部结束') {
      closeSession()
    } else if (evaluatingRef.current) {
      chatPanelRef.current?.appendEvaluatingHtml(text)
    } else {
      chatPanelRef.current?.appendAIText(text)
    }
  }, [closeSession, cModeActive, cMode.receiveBotText]))

  const handleStart = useCallback(() => {
    console.log('[Exam] 点击 Start, micStream:', micStream ? '已就绪' : '未就绪')
    const examInfo: Record<string, unknown> = { exam_id: examId }
    if (examType) examInfo.exam_type = examType
    startSession(micStream, examInfo)
  }, [startSession, micStream, examId, examType])

  // C 模式：DataChannel 打开后自动发送 start，获取第一道根问题
  useEffect(() => {
    if (cModeActive && dcOpen) {
      console.log('[Exam][C] DC 已打开, 发送 start')
      cMode.start()
    }
  }, [cModeActive, dcOpen, cMode.start])

  const handleSendText = useCallback((text: string) => {
    if (!sessionId) {
      showToast('error', '请先点击 Start 开始会话')
      return
    }
    if (cModeActive) {
      // C 模式：文字输入作为一段回答提交
      cMode.sendAnswerChunk(text)
      return
    }
    sendDCMessage({ type: 'user-text', data: { text } })
  }, [sessionId, sendDCMessage, showToast, cModeActive, cMode.sendAnswerChunk])

  // 回答完毕确认事件：B/C 模式都会收到，此处统一注册后按模式分流
  // （onDCMessage 为单槽注册，C 模式不再自行注册该类型，避免相互覆盖）
  const [bConfirmPrompt, setBConfirmPrompt] = useState<{ questionId: string; message: string; idleTimeoutSecs: number } | null>(null)

  onDCMessage('answer_finish_confirmation_required', useCallback((data: Record<string, unknown>) => {
    if (cModeActive) {
      // C 模式：转发回 C 模式事件分发（confirmPrompt 弹窗）
      cMode.handleEvent(data)
      return
    }
    // B 模式：本地弹窗确认，选择后回发 answer_finish_confirmation 信号
    setBConfirmPrompt({
      questionId: String(data.question_id ?? ''),
      message: String(data.message ?? '你是否已经回答完毕？'),
      idleTimeoutSecs: typeof data.idle_timeout_secs === 'number' ? data.idle_timeout_secs : 0,
    })
  }, [cModeActive, cMode.handleEvent]))

  /** B 模式：回发回答完毕确认信号（与 C 模式 sendFinishConfirmation 一致） */
  const sendBFinishConfirmation = useCallback((finished: boolean) => {
    setBConfirmPrompt(null)
    if (finished) deactivateMic()
    sendDCMessage({ type: 'answer_finish_confirmation', data: { finished } })
  }, [deactivateMic, sendDCMessage])

  // 会话结束后清理 B 模式确认弹窗
  useEffect(() => {
    if (!sessionId) setBConfirmPrompt(null)
  }, [sessionId])

  /** 收到 finished 事件（考试全部结束）：直接结束会话并返回考试列表 */
  const examFinished = cMode.examFinished
  useEffect(() => {
    if (!examFinished) return
    cleanupConnection()
    navigate('/exam', { replace: true })
  }, [examFinished, cleanupConnection, navigate])

  return (
    <>
      <audio id="remoteAudio" autoPlay />

      {/* B 模式：回答完毕确认弹窗（后端 answer_finish_confirmation_required 触发，与 C 模式同款） */}
      {bConfirmPrompt && !cModeActive && (
        <div className="cmode-confirm-overlay">
          <div className="cmode-confirm-dialog">
            <div className="cmode-confirm-title">
              <i className="fas fa-circle-question"></i> 回答完毕确认
            </div>
            <div className="cmode-confirm-message">
              {bConfirmPrompt.message}
              {bConfirmPrompt.idleTimeoutSecs > 0 && (
                <span className="cmode-confirm-hint">
                  （已 {bConfirmPrompt.idleTimeoutSecs} 秒未检测到语音输入）
                </span>
              )}
            </div>
            <div className="cmode-confirm-actions">
              <button
                className="cmode-confirm-btn continue"
                onClick={() => sendBFinishConfirmation(false)}
              >
                <i className="fas fa-pen-nib"></i> 继续作答
              </button>
              <button
                className="cmode-confirm-btn finish"
                onClick={() => sendBFinishConfirmation(true)}
              >
                <i className="fas fa-check"></i> 结束回答
              </button>
            </div>
          </div>
        </div>
      )}

      <main className="exam-layout">
        <Sidebar
          starting={starting}
          sessionId={sessionId}
          micDevices={micDevices}
          selectedDeviceId={selectedDeviceId}
          micStatus={micStatus}
          micLevel={micLevel}
          onStart={handleStart}
          onClose={closeSession}
          onMicSelect={selectDevice}
        />

        {cModeActive ? (
          <CModePanel
            chatStatus={chatStatus}
            connected={!!sessionId}
            starting={starting}
            remoteAudioStarted={remoteAudioStarted}
            audioDetected={audioDetected}
            pttActive={pttActive}
            onTogglePTT={togglePTT}
            onSendText={handleSendText}
            phase={cMode.phase}
            currentQuestion={cMode.currentQuestion}
            answerRaw={cMode.answerRaw}
            answerCorrected={cMode.answerCorrected}
            judgement={cMode.judgement}
            completedInfo={cMode.completedInfo}
            errorMsg={cMode.errorMsg}
            waitingReason={cMode.waitingReason}
            closedInfo={cMode.closedInfo}
            confirmPrompt={cMode.confirmPrompt}
            finalReport={cMode.finalReport}
            onAnswerEnd={() => { deactivateMic(); cMode.sendAnswerEnd() }}
            onFinish={cMode.finish}
            onConfirmAnswerFinish={(finished) => { if (finished) deactivateMic(); cMode.sendFinishConfirmation(finished) }}
          />
        ) : (
          <ChatPanel
            ref={chatPanelRef}
            chatStatus={chatStatus}
            onTogglePTT={togglePTT}
            pttActive={pttActive}
            onSendText={handleSendText}
            connected={!!sessionId}
            starting={starting}
            remoteAudioStarted={remoteAudioStarted}
            audioDetected={audioDetected}
          />
        )}
      </main>

      <div className="toast-container">
        {toasts.map(t => {
          const icon = t.type === 'success' ? 'fa-check-circle' : t.type === 'error' ? 'fa-times-circle' : 'fa-info-circle'
          const color = t.type === 'success' ? 'var(--accent)' : t.type === 'error' ? '#ef4444' : 'var(--accent2)'
          return (
            <div key={t.id} className={`toast ${t.type}`}>
              <i className={`fas ${icon}`} style={{ color }}></i>
              {t.message}
            </div>
          )
        })}
      </div>
    </>
  )
}
