import { useState, useEffect, useRef } from 'react'
import type { MicStatus } from '../types'

interface SidebarProps {
  starting: boolean
  sessionId: string | null
  micDevices: MediaDeviceInfo[]
  selectedDeviceId: string
  micStatus: MicStatus
  micLevel: number
  onStart: () => void
  onClose: () => void
  onMicSelect: ((deviceId: string) => void) & { __enumerate?: () => void }
}

export default function Sidebar({
  starting, sessionId, micDevices, selectedDeviceId,
  micStatus, micLevel, onStart, onClose, onMicSelect,
}: SidebarProps) {
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const [showCloseConfirm, setShowCloseConfirm] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    onMicSelect.__enumerate?.()
  }, [])

  useEffect(() => {
    function handleClickOutside(e: MouseEvent) {
      if (dropdownRef.current && !dropdownRef.current.contains(e.target as Node)) {
        setDropdownOpen(false)
      }
    }
    document.addEventListener('mousedown', handleClickOutside)
    return () => document.removeEventListener('mousedown', handleClickOutside)
  }, [])

  const selectedLabel = micDevices.find(d => d.deviceId === selectedDeviceId)?.label || ''

  return (
    <aside className="sidebar">
      <div className="panel">
        <div className="panel-header">
          <i className="fas fa-bolt"></i> 操作
        </div>
        <div className="panel-body">
          <button
            className="action-btn primary"
            onClick={onStart}
            disabled={starting}
          >
            <i className={`fas ${starting ? 'fa-spinner fa-spin' : sessionId ? 'fa-redo' : 'fa-play'}`}></i>
            {starting ? '启动中...' : sessionId ? 'Restart' : 'Start'}
          </button>

          {sessionId && (
            <button
              className="action-btn danger"
              onClick={() => setShowCloseConfirm(true)}
            >
              <i className="fas fa-times-circle"></i>
              Close
            </button>
          )}

          {sessionId && (
            <div className="session-info">
              <span className="session-label">Session</span>
              <span className="session-id">{sessionId.slice(0, 8)}...</span>
            </div>
          )}
        </div>
      </div>

      <div className="panel">
        <div className="panel-header">
          <i className="fas fa-microphone"></i> 麦克风
        </div>
        <div className="panel-body">
          <div className="mic-dropdown" ref={dropdownRef}>
            <button
              className={`mic-dropdown-trigger ${selectedDeviceId ? 'selected' : ''}`}
              onClick={() => setDropdownOpen(prev => !prev)}
            >
              <span className="mic-dropdown-text">
                {selectedLabel || '选择麦克风设备'}
              </span>
              <i className={`fas fa-chevron-down mic-dropdown-arrow ${dropdownOpen ? 'open' : ''}`}></i>
            </button>

            {dropdownOpen && (
              <div className="mic-dropdown-menu">
                {micDevices.length === 0 ? (
                  <div className="mic-dropdown-empty">
                    <i className="fas fa-microphone-slash"></i>
                    <span>未检测到麦克风</span>
                  </div>
                ) : (
                  micDevices.map(d => (
                    <button
                      key={d.deviceId}
                      className={`mic-dropdown-item ${d.deviceId === selectedDeviceId ? 'active' : ''}`}
                      onClick={() => {
                        onMicSelect(d.deviceId)
                        setDropdownOpen(false)
                      }}
                    >
                      <i className={`fas ${d.deviceId === selectedDeviceId ? 'fa-check-circle' : 'fa-microphone'}`}></i>
                      <span>{d.label || `麦克风 ${d.deviceId.slice(0, 8)}`}</span>
                    </button>
                  ))
                )}
              </div>
            )}
          </div>

          <div className={`mic-status ${micStatus.state}`}>
            <i className={`fas ${micStatus.state === 'granted' ? 'fa-check-circle' : micStatus.state === 'denied' ? 'fa-times-circle' : 'fa-circle'}`}></i>
            {micStatus.text}
          </div>

          {micStatus.state === 'granted' && (
            <div className="mic-level">
              <div className="mic-level-fill" style={{ width: `${micLevel}%` }}></div>
            </div>
          )}
        </div>
      </div>

      {showCloseConfirm && (
        <div className="close-confirm-overlay">
          <div className="close-confirm-card">
            <div className="close-confirm-icon">
              <i className="fas fa-exclamation-triangle"></i>
            </div>
            <div className="close-confirm-title">确认结束口试？</div>
            <div className="close-confirm-desc">
              如果提前结束口试，本次考试成绩将会记为 <strong>0 分</strong>，且无法恢复。
            </div>
            <div className="close-confirm-actions">
              <button
                className="close-confirm-btn cancel"
                onClick={() => setShowCloseConfirm(false)}
              >
                继续考试
              </button>
              <button
                className="close-confirm-btn confirm"
                onClick={() => {
                  setShowCloseConfirm(false)
                  onClose()
                }}
              >
                确认结束
              </button>
            </div>
          </div>
        </div>
      )}
    </aside>
  )
}
