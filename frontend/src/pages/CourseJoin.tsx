import { useState } from 'react'
import { API_BASE } from '../config'

interface CoursePreview {
  course_id: string
  course_name: string
  description: string | null
  exam_item_name: string
  teacher_name: string
}

export default function CourseJoin() {
  const [inviteCode, setInviteCode] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [preview, setPreview] = useState<CoursePreview | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [success, setSuccess] = useState(false)

  const handleLookup = async () => {
    const code = inviteCode.trim()
    if (code.length !== 5) {
      setError('请输入5位邀请码')
      return
    }
    setLoading(true)
    setError('')
    setPreview(null)
    setSuccess(false)
    try {
      const res = await fetch(`${API_BASE}/courses/by_invite_code/${code}`, {
        credentials: 'include',
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || data.message || '邀请码无效或已过期')
      }
      const json = await res.json()
      setPreview(json.data ?? json)
    } catch (err) {
      setError(err instanceof Error ? err.message : '查询失败')
    } finally {
      setLoading(false)
    }
  }

  const handleConfirmJoin = async () => {
    if (!preview) return
    setSubmitting(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/courses/${preview.course_id}/join_requests`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
      })
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || data.message || '申请失败')
      }
      setSuccess(true)
    } catch (err) {
      setError(err instanceof Error ? err.message : '申请失败')
    } finally {
      setSubmitting(false)
    }
  }

  const handleReset = () => {
    setInviteCode('')
    setPreview(null)
    setError('')
    setSuccess(false)
  }

  return (
    <div className="join-page">
      <div className="join-card">
        {!success ? (
          <>
            <div className="join-card-header">
              <h2><i className="fas fa-user-plus"></i> 加入课程</h2>
              <p>输入教师提供的5位邀请码加入课程</p>
            </div>

            <div className="join-card-body">
              <div className="join-input-group">
                <input
                  type="text"
                  maxLength={5}
                  placeholder="输入5位邀请码"
                  value={inviteCode}
                  onChange={e => setInviteCode(e.target.value.replace(/[^a-zA-Z0-9]/g, '').slice(0, 5))}
                  className="join-code-input"
                  disabled={loading || !!preview}
                />
                {!preview ? (
                  <button
                    className="join-lookup-btn"
                    onClick={handleLookup}
                    disabled={inviteCode.trim().length !== 5 || loading}
                  >
                    {loading ? <i className="fas fa-spinner fa-spin"></i> : '查询'}
                  </button>
                ) : (
                  <button className="join-reset-btn" onClick={handleReset}>
                    <i className="fas fa-undo"></i>
                  </button>
                )}
              </div>

              {error && (
                <div className="join-error">
                  <i className="fas fa-exclamation-circle"></i> {error}
                </div>
              )}

              {preview && (
                <div className="join-preview">
                  <div className="join-preview-icon">
                    <i className="fas fa-book-open"></i>
                  </div>
                  <div className="join-preview-info">
                    <h3>{preview.course_name}</h3>
                    {preview.teacher_name && <p className="join-preview-teacher"><i className="fas fa-user-tie"></i> {preview.teacher_name}</p>}
                    {preview.description && <p className="join-preview-desc">{preview.description}</p>}
                    {preview.exam_item_name && <p className="join-preview-exam"><i className="fas fa-clipboard-list"></i> {preview.exam_item_name}</p>}
                  </div>
                  <button
                    className="join-confirm-btn"
                    onClick={handleConfirmJoin}
                    disabled={submitting}
                  >
                    {submitting ? <i className="fas fa-spinner fa-spin"></i> : <><i className="fas fa-check"></i> 确认加入</>}
                  </button>
                </div>
              )}
            </div>
          </>
        ) : (
          <div className="join-success">
            <div className="join-success-icon">
              <i className="fas fa-check-circle"></i>
            </div>
            <h2>申请已提交</h2>
            <p>请等待教师审批</p>
            <button className="join-confirm-btn" onClick={handleReset}>继续加入其他课程</button>
          </div>
        )}
      </div>
    </div>
  )
}
