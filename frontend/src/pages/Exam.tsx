import { useState, useEffect, useCallback } from 'react'
import { useParams, useLocation, useNavigate } from 'react-router-dom'
import { useWebRTC } from '../hooks/useWebRTC'
import { useMicrophone } from '../hooks/useMicrophone'
import { useToast } from '../hooks/useToast'
import { useExamSession } from '../hooks/useExamSession'
import { useCModeExam } from '../hooks/useCModeExam'
import Sidebar from '../components/Sidebar'
import CModePanel from '../components/CModePanel'

/** 后端哨兵串（驱动 UI 状态切换），不作为题目正文渲染 */
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
  const { toasts, showToast } = useToast()
  const { sessionId, chatStatus, starting, remoteAudioStarted, audioDetected, dcOpen, startSession, closeSession, sendDCMessage, onDCMessage, replaceAudioTrack, cleanupConnection } = useWebRTC(showToast)
  const {
    micStream, micReady, micDevices, selectedDeviceId,
    micStatus, micLevel, enumerateDevices, selectDevice, setMicEnabled, setOnTrackChange,
  } = useMicrophone(showToast)
  const { setExamActive } = useExamSession()

  // 所有考试模式（A/B/C）统一使用同一套交互与输入方式
  const cMode = useCModeExam({
    enabled: true,
    sendDCMessage,
    onDCMessage,
    showToast,
  })

  const [pttActive, setPttActive] = useState(false)

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

  // 后端回显的用户文本不回传：语音 STT 由后端自行处理，
  // 若此处中继会形成 user-text ↔ user-llm-text 死循环，直接忽略
  onDCMessage('user-llm-text', useCallback((_data: Record<string, unknown>) => { /* ignore */ }, []))

  // 题目正文经 bot-llm-text 以 HTML 片段推送，哨兵串忽略，其余累积到当前题目
  onDCMessage('bot-llm-text', useCallback((data: Record<string, unknown>) => {
    const text = (data.data as Record<string, unknown>)?.text as string
    if (!text) return
    if (!BOT_TEXT_SENTINELS.has(text)) cMode.receiveBotText(text)
  }, [cMode.receiveBotText]))

  const handleStart = useCallback(() => {
    console.log('[Exam] 点击 Start, micStream:', micStream ? '已就绪' : '未就绪')
    const examInfo: Record<string, unknown> = { exam_id: examId }
    if (examType) examInfo.exam_type = examType
    startSession(micStream, examInfo)
  }, [startSession, micStream, examId, examType])

  // DataChannel 打开后自动发送 start，获取第一道根问题
  useEffect(() => {
    if (dcOpen) {
      console.log('[Exam] DC 已打开, 发送 start')
      cMode.start()
    }
  }, [dcOpen, cMode.start])

  const handleSendText = useCallback((text: string) => {
    if (!sessionId) {
      showToast('error', '请先点击 Start 开始会话')
      return
    }
    // 文字输入作为一段回答提交
    cMode.sendAnswerChunk(text)
  }, [sessionId, showToast, cMode.sendAnswerChunk])

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
