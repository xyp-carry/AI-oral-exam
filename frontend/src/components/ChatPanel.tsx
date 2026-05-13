import { useState, useRef, useImperativeHandle, forwardRef, useEffect } from 'react'
import { marked } from 'marked'
import type { ChatStatus, ChatMessage } from '../types'

interface ChatPanelProps {
  chatStatus: ChatStatus
  onTogglePTT: () => void
  pttActive: boolean
}

export interface ChatPanelHandle {
  receiveUserMessage: (text: string) => void
  startAIThinking: () => void
  appendAIText: (text: string) => void
  finishAIResponse: () => void
  receiveAIMessage: (text: string) => void
  startEvaluating: () => void
  appendEvaluatingHtml: (text: string) => void
  stopEvaluating: () => void
}

const ChatPanel = forwardRef<ChatPanelHandle, ChatPanelProps>(function ChatPanel({
  chatStatus, onTogglePTT, pttActive,
}, ref) {
  const [messages, setMessages] = useState<ChatMessage[]>([])
  const [evaluating, setEvaluating] = useState(false)
  const [evalResult, setEvalResult] = useState<string | null>(null)
  const evalHtmlRef = useRef('')
  const messagesEndRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages])

  const receiveUserMessage = (text: string) => {
    setMessages(prev => [...prev, { role: 'user', text }])
  }

  const startAIThinking = () => {
    setMessages(prev => [...prev, { role: 'ai', text: '', thinking: true, streaming: true }])
  }

  const appendAIText = (text: string) => {
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

  useImperativeHandle(ref, () => ({
    receiveUserMessage,
    startAIThinking,
    appendAIText,
    finishAIResponse,
    receiveAIMessage,
    startEvaluating,
    appendEvaluatingHtml,
    stopEvaluating,
  }))

  const renderMarkdown = (text: string) => {
    try {
      return { __html: marked.parse(text) as string }
    } catch {
      return { __html: text }
    }
  }

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
            <div className="evaluating-overlay">
              <div className="eval-result-card">
                <div className="eval-result-header">
                  <span className="eval-result-title">
                    <i className="fas fa-clipboard-check"></i> 评审结果
                  </span>
                  <button className="eval-result-close" onClick={dismissEvalResult}>
                    <i className="fas fa-times"></i>
                  </button>
                </div>
                <div className="eval-result-body" dangerouslySetInnerHTML={{ __html: evalResult }} />
              </div>
            </div>
          )}

          {messages.length === 0 && !evaluating && !evalResult && (
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
                    <div className="chat-bubble ai-md" dangerouslySetInnerHTML={renderMarkdown(msg.text)} />
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

        <div className="chat-ptt-area">
          <button
            className={`chat-ptt-btn ${pttActive ? 'recording' : ''}`}
            onClick={onTogglePTT}
          >
            <i className="fas fa-microphone"></i>
          </button>
          <span className={`chat-ptt-label ${pttActive ? 'recording' : ''}`}>
            {pttActive ? '点击静音' : '点击说话'}
          </span>
        </div>
      </div>
    </div>
  )
})

export default ChatPanel
