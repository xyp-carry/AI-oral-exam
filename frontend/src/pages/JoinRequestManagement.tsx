import { useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config'
import { useAuth } from '../hooks/useAuth'

interface JoinRequest {
  request_id: string
  course_id: string
  course_name: string
  user_id: string
  username: string
  nickname: string | null
  status: 'pending' | 'approved' | 'rejected'
  created_at: string
}

export default function JoinRequestManagement() {
  const { user } = useAuth()
  const [requests, setRequests] = useState<JoinRequest[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [actionLoading, setActionLoading] = useState<string | null>(null)

  const fetchRequests = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const coursesRes = await fetch(`${API_BASE}/courses`, { credentials: 'include' })
      if (!coursesRes.ok) throw new Error('获取课程失败')
      const coursesJson = await coursesRes.json()
      const courses: { course_id: string }[] = Array.isArray(coursesJson) ? coursesJson : coursesJson.data ?? []

      const allRequests: JoinRequest[] = []
      await Promise.all(
        courses.map(async (c) => {
          try {
            const r = await fetch(`${API_BASE}/courses/${c.course_id}/join_requests`, {
              credentials: 'include',
            })
            if (r.ok) {
              const j = await r.json()
              const items: JoinRequest[] = Array.isArray(j) ? j : j.data ?? []
              allRequests.push(...items)
            }
          } catch { /* skip */ }
        })
      )
      allRequests.sort((a, b) => {
        if (a.status === 'pending' && b.status !== 'pending') return -1
        if (a.status !== 'pending' && b.status === 'pending') return 1
        return b.created_at?.localeCompare(a.created_at ?? '') ?? 0
      })
      setRequests(allRequests)
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取申请列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchRequests()
  }, [fetchRequests])

  const handleAction = async (requestId: string, action: 'approve' | 'reject') => {
    setActionLoading(requestId)
    try {
      const req = requests.find(r => r.request_id === requestId)
      if (!req) return
      const res = await fetch(`${API_BASE}/courses/join_requests/${action}`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          course_id: req.course_id,
          user_id: req.user_id,
        }),
      })
      if (!res.ok) throw new Error('操作失败')
      setRequests(prev => prev.map(r => r.request_id === requestId ? { ...r, status: action === 'approve' ? 'approved' : 'rejected' } : r))
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    } finally {
      setActionLoading(null)
    }
  }

  const statusMap: Record<string, { label: string; cls: string }> = {
    pending: { label: '待审批', cls: 'pending' },
    approved: { label: '已通过', cls: 'approved' },
    rejected: { label: '已拒绝', cls: 'rejected' },
  }

  const pendingCount = requests.filter(r => r.status === 'pending').length

  if (user?.role !== 'teacher') {
    return (
      <div className="join-mgmt-page">
        <div className="course-forbidden">
          <i className="fas fa-lock"></i>
          <p>仅教师可访问申请管理</p>
        </div>
      </div>
    )
  }

  return (
    <div className="join-mgmt-page">
      <div className="join-mgmt-header">
        <div>
          <h2>申请管理</h2>
          <p>{loading ? '加载中...' : `共 ${requests.length} 条申请，${pendingCount} 条待审批`}</p>
        </div>
        <button className="examlist-refresh-btn" onClick={fetchRequests} disabled={loading}>
          <i className={`fas ${loading ? 'fa-spinner fa-spin' : 'fa-sync-alt'}`}></i> 刷新
        </button>
      </div>

      {error && (
        <div className="examlist-error">
          <i className="fas fa-exclamation-circle"></i>
          <span>{error}</span>
          <button onClick={fetchRequests}>重试</button>
        </div>
      )}

      {loading && (
        <div className="examlist-loading">
          <i className="fas fa-spinner fa-spin"></i>
          <span>加载中...</span>
        </div>
      )}

      {!loading && requests.length === 0 && (
        <div className="examlist-empty">
          <i className="fas fa-inbox"></i>
          <p>暂无申请</p>
        </div>
      )}

      {!loading && requests.length > 0 && (
        <div className="join-mgmt-table">
          <div className="join-mgmt-table-header">
            <span>申请人</span>
            <span>课程</span>
            <span>申请时间</span>
            <span>状态</span>
            <span>操作</span>
          </div>
          {requests.map(req => {
            const st = statusMap[req.status] ?? { label: req.status, cls: '' }
            const isLoading = actionLoading === req.request_id
            return (
              <div key={req.request_id} className={`join-mgmt-table-row ${st.cls}`}>
                <span className="join-mgmt-user">
                  <i className="fas fa-user-circle"></i>
                  {req.nickname || req.username}
                </span>
                <span>{req.course_name}</span>
                <span className="join-mgmt-time">{req.created_at?.slice(0, 16)}</span>
                <span className={`join-mgmt-status ${st.cls}`}>{st.label}</span>
                <span className="join-mgmt-actions">
                  {req.status === 'pending' ? (
                    <>
                      <button
                        className="join-mgmt-action-btn approve"
                        onClick={() => handleAction(req.request_id, 'approve')}
                        disabled={isLoading}
                      >
                        {isLoading ? <i className="fas fa-spinner fa-spin"></i> : <><i className="fas fa-check"></i> 通过</>}
                      </button>
                      <button
                        className="join-mgmt-action-btn reject"
                        onClick={() => handleAction(req.request_id, 'reject')}
                        disabled={isLoading}
                      >
                        <i className="fas fa-times"></i> 拒绝
                      </button>
                    </>
                  ) : (
                    <span className="join-mgmt-done">—</span>
                  )}
                </span>
              </div>
            )
          })}
        </div>
      )}
    </div>
  )
}
