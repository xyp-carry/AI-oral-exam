import { useState, useRef, useImperativeHandle, forwardRef, useEffect } from 'react'
import { renderContent } from '../utils/renderContent'
import type { ChatStatus, ChatMessage } from '../types'

interface ChatPanelProps {
  chatStatus: ChatStatus
  onTogglePTT: () => void
  pttActive: boolean
  onSendText: (text: string) => void
  connected: boolean
  starting: boolean
  remoteAudioStarted: boolean
  audioDetected: boolean
}

export interface ChatPanelHandle {
  receiveUserMessage: (text: string) => void
  startAIThinking: () => void
  startAIAnswering: () => void
  appendAIText: (text: string) => void
  finishAIResponse: () => void
  receiveAIMessage: (text: string) => void
  startEvaluating: () => void
  appendEvaluatingHtml: (text: string) => void
  stopEvaluating: () => void
}

const ChatPanel = forwardRef<ChatPanelHandle, ChatPanelProps>(function ChatPanel({
  chatStatus, onTogglePTT, pttActive, onSendText, connected, starting, remoteAudioStarted, audioDetected,
}, ref) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [evaluating, setEvaluating] = useState(false)
  const [evalResult, setEvalResult] = useState<string | null>(null)
  const [textInput, setTextInput] = useState('')
  const [tipIndex, setTipIndex] = useState(0)
  const [waitingFirstResponse, setWaitingFirstResponse] = useState(false)
  const evalHtmlRef = useRef('')
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const textInputRef = useRef<HTMLInputElement>(null)

  const startingTips = [
    '正在预备题目',
    '等待老师入会',
    '正在建立连接',
    '准备考试环境',
    '加载考试资源',
    '老师正在入场',
    '题目准备就绪',
    '即将开始考试',
  ]

  useEffect(() => {
    if (!waitingFirstResponse) {
      setTipIndex(0)
      return
    }
    const timer = setInterval(() => {
      setTipIndex(prev => (prev + 1) % startingTips.length)
    }, 2500)
    return () => clearInterval(timer)
  }, [waitingFirstResponse])

  useEffect(() => {
    if (starting) {
      setWaitingFirstResponse(true)
    }
  }, [starting])

  useEffect(() => {
    if (!connected) {
      setWaitingFirstResponse(false)
    }
  }, [connected])

  useEffect(() => {
    if (audioDetected) {
      setWaitingFirstResponse(false)
    }
  }, [audioDetected])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const receiveUserMessage = (text: string) => {
    setMessages(prev => [...prev, { role: 'user', text }])
  }

  const startAIThinking = () => {
    setWaitingFirstResponse(false)
    setMessages(prev => [...prev, { role: 'ai', text: '', thinking: true, streaming: true }])
  }

  const startAIAnswering = () => {
    setWaitingFirstResponse(false)
    setMessages(prev => {
      const last = prev[prev.length - 1]
      if (last?.role === 'ai' && last.thinking) {
        return [...prev.slice(0, -1), { ...last, thinking: false }]
      }
      return prev
    })
  }

  const appendAIText = (text: string) => {
    setWaitingFirstResponse(false)
    setMessages(prev => {
      const last = prev[prev.length - 1]
      if (last?.role === 'ai' && last.streaming) {
        return [...prev.slice(0, -1), { ...last, text: last.text + text, thinking: false }]
      }
      return [...prev, { role: 'ai', text, streaming: true }]
    })
  }

  const finishAIResponse = () => {
    setMessages(prev => {
      const last = prev[prev.length - 1]
      if (last?.role === 'ai' && last.streaming) {
        return [...prev.slice(0, -1), { ...last, streaming: false, thinking: false }]
      }
      return prev
    })
  }

  const receiveAIMessage = (text: string) => {
    setWaitingFirstResponse(false)
    setMessages(prev => [...prev, { role: 'ai', text }])
  }

  const startEvaluating = () => {
    evalHtmlRef.current = ''
    setEvalResult(null)
    setEvaluating(true)
  }

  const appendEvaluatingHtml = (text: string) => {
    evalHtmlRef.current += text
  }

  const stopEvaluating = () => {
    setEvaluating(false)
    if (evalHtmlRef.current) {
      setEvalResult(evalHtmlRef.current)
      evalHtmlRef.current = ''
    }
  }

  const dismissEvalResult = () => {
    setEvalResult(null)
  }

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

  useImperativeHandle(ref, () => ({
    receiveUserMessage,
    startAIThinking,
    startAIAnswering,
    appendAIText,
    finishAIResponse,
    receiveAIMessage,
    startEvaluating,
    appendEvaluatingHtml,
    stopEvaluating,
  }))

  return (
    <div className="content">
      <div className="chat-container">
        <div className="chat-header">
          <span className="chat-title">
            <i className="fas fa-comments"></i> 对话
          </span>
          <span className={`chat-status ${chatStatus.ready ? 'connected' : ''}`}>
            <span className={`status-dot ${chatStatus.ready ? 'online' : 'offline'}`}></span>
            {chatStatus.text}
          </span>
        </div>

        <div className="chat-messages">
          {evaluating && (
            <div className="evaluating-overlay">
              <div className="evaluating-card">
                <div className="evaluating-spinner">
                  <div className="spinner-ring"></div>
                  <i className="fas fa-clipboard-check evaluating-icon"></i>
                </div>
                <div className="evaluating-text">评审组正在评估</div>
                <div className="evaluating-sub">请稍候，AI 评审正在分析您的表现...</div>
              </div>
            </div>
          )}

          {evalResult && (
            <div className="eval-result-fullscreen">
              <div className="eval-result-card">
                <div className="eval-result-header">
                  <span className="eval-result-title">
                    <i className="fas fa-clipboard-check"></i> 评审结果
                  </span>
                </div>
                <div className="eval-result-body" dangerouslySetInnerHTML={{ __html: evalResult }} />
                <div className="eval-result-footer">
                  <button className="eval-result-confirm-btn" onClick={dismissEvalResult}>
                    <i className="fas fa-check-circle"></i> 确认
                  </button>
                </div>
              </div>
            </div>
          )}

          {waitingFirstResponse && (
            <div className="starting-overlay">
              <div className="starting-card">
                <div className="starting-spinner">
                  <div className="starting-ring"></div>
                  <i className="fas fa-graduation-cap starting-icon"></i>
                </div>
                <div className="starting-tip-wrap">
                  <span key={tipIndex} className="starting-tip">{startingTips[tipIndex]}</span>
                </div>
                <div className="starting-dots">
                  <span></span><span></span><span></span>
                </div>
              </div>
            </div>
          )}

          {messages.length === 0 && !evaluating && !evalResult && !waitingFirstResponse && (
            <div className="chat-empty">
              <i className="fas fa-robot"></i>
              <p>点击 Start 开始会话</p>
            </div>
          )}

          {messages.map((msg, i) => (
            <div key={i} className={`chat-msg ${msg.role}`}>
              <div className="chat-msg-avatar">
                <i className={`fas ${msg.role === 'user' ? 'fa-user' : 'fa-robot'}`}></i>
              </div>
              <div className="chat-msg-body">
                {msg.role === 'ai' ? (
                  msg.thinking ? (
                    <div className="chat-bubble ai-md ai-thinking">
                      <div className="thinking-indicator">
                        <span className="thinking-icon"><i className="fas fa-brain"></i></span>
                        <span className="thinking-text">正在生成提问</span>
                        <span className="thinking-dots">
                          <span></span><span></span><span></span>
                        </span>
                      </div>
                    </div>
                  ) : (
                    <div className="chat-bubble ai-md" dangerouslySetInnerHTML={renderContent(msg.text)} />
                  )
                ) : (
                  <div className="chat-bubble user-bubble">
                    <p>{msg.text}</p>
                  </div>
                )}
                {msg.streaming && !msg.thinking && <span className="streaming-cursor">▎</span>}
              </div>
            </div>
          ))}

          <div ref={messagesEndRef} />
        </div>

        <div className="chat-input-area">
          <button
            className={`chat-ptt-btn ${pttActive ? 'recording' : ''}`}
            onClick={onTogglePTT}
            title={pttActive ? '点击静音' : '点击说话'}
          >
            <i className="fas fa-microphone"></i>
          </button>
          <div className="chat-text-input-wrap">
            <input
              ref={textInputRef}
              type="text"
              className="chat-text-input"
              placeholder={connected ? '输入文字消息...' : '请先开始会话'}
              value={textInput}
              onChange={(e) => setTextInput(e.target.value)}
              onKeyDown={handleTextKeyDown}
              disabled={!connected}
            />
            <button
              className="chat-text-send-btn"
              onClick={handleTextSend}
              disabled={!connected || !textInput.trim()}
              title="发送"
            >
              <i className="fas fa-paper-plane"></i>
            </button>
          </div>
        </div>
      </div>
    </div>
  )
})

export default ChatPanel
