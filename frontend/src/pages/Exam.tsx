import { useState, useEffect, useCallback, useRef } from 'react'
import { useParams } from 'react-router-dom'
import { useWebRTC } from '../hooks/useWebRTC'
import { useMicrophone } from '../hooks/useMicrophone'
import { useToast } from '../hooks/useToast'
import { useExamSession } from '../hooks/useExamSession'
import Sidebar from '../components/Sidebar'
import ChatPanel from '../components/ChatPanel'
import type { ChatPanelHandle } from '../components/ChatPanel'

export default function Exam() {
  const { examId } = useParams<{ examId: string }>()
  const { toasts, showToast } = useToast()
  const { sessionId, chatStatus, starting, remoteAudioStarted, audioDetected, startSession, closeSession, sendDCMessage, onDCMessage, replaceAudioTrack, cleanupConnection } = useWebRTC(showToast)
  const {
    micStream, micReady, micDevices, selectedDeviceId,
    micStatus, micLevel, enumerateDevices, selectDevice, setMicEnabled, setOnTrackChange,
  } = useMicrophone(showToast)
  const { setExamActive } = useExamSession()

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

  useEffect(() => {
    enumerateDevices()
    navigator.mediaDevices?.addEventListener('devicechange', enumerateDevices)
    return () => {
      navigator.mediaDevices?.removeEventListener('devicechange', enumerateDevices)
    }
  }, [])

  onDCMessage('user-llm-text', useCallback((data: Record<string, unknown>) => {
    const text = (data.data as Record<string, unknown>)?.text as string
    if (text) chatPanelRef.current?.receiveUserMessage(text)
  }, []))

  onDCMessage('bot-llm-text', useCallback((data: Record<string, unknown>) => {
    const text = (data.data as Record<string, unknown>)?.text as string
    if (!text) return
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
  }, [closeSession]))

  const handleStart = useCallback(() => {
    console.log('[Exam] 点击 Start, micStream:', micStream ? '已就绪' : '未就绪')
    startSession(micStream, examId ? { exam_item_id: examId } : undefined)
  }, [startSession, micStream, examId])

  const handleSendText = useCallback((text: string) => {
    if (!sessionId) {
      showToast('error', '请先点击 Start 开始会话')
      return
    }
    sendDCMessage({ type: 'user-text', data: { text } })
  }, [sessionId, sendDCMessage, showToast])

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
