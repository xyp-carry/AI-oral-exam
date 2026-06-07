import { useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config'

interface ExamRecord {
  candidate_id: string
  total_score: number
  dimension_count: number
  question_count: number
  ended_at: string
  created_at: string
}

export default function ExamHistory() {
  const [records, setRecords] = useState<ExamRecord[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [searchTerm, setSearchTerm] = useState('')
  const [selectedId, setSelectedId] = useState<string | null>(null)

  const fetchRecords = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/exam_history`, { credentials: 'include' })
      if (!res.ok) throw new Error(`请求失败: ${res.status}`)
      const json = await res.json()
      setRecords(Array.isArray(json) ? json : json.data ?? json.records ?? json.exams ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取考试记录失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchRecords()
  }, [fetchRecords])

  const filtered = records.filter(r => {
    if (!searchTerm) return true
    const q = searchTerm.toLowerCase()
    return (
      r.candidate_id?.toLowerCase().includes(q) ||
      r.created_at?.toLowerCase().includes(q) ||
      r.ended_at?.toLowerCase().includes(q)
    )
  })

  const formatTime = (t: string) => {
    if (!t) return '-'
    try {
      return new Date(t).toLocaleString('zh-CN', {
        year: 'numeric', month: '2-digit', day: '2-digit',
        hour: '2-digit', minute: '2-digit',
      })
    } catch {
      return t
    }
  }

  const avgScore = filtered.length > 0
    ? (filtered.reduce((a, b) => a + (b.total_score ?? 0), 0) / filtered.length).toFixed(1)
    : '0'

  const handleRowClick = (record: ExamRecord) => {
    setSelectedId(selectedId === record.candidate_id ? null : record.candidate_id)
  }

  return (
    <div className="history-page">
      <div className="history-filters">
        <div className="history-search">
          <i className="fas fa-search"></i>
          <input
            type="text"
            placeholder="搜索考试记录..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
          />
        </div>
        <button className="history-refresh-btn" onClick={fetchRecords} disabled={loading}>
          <i className={`fas ${loading ? 'fa-spinner fa-spin' : 'fa-sync-alt'}`}></i>
          刷新
        </button>
      </div>

      <div className="history-summary">
        <div className="history-summary-item">
          <span className="history-summary-value">{filtered.length}</span>
          <span className="history-summary-label">总记录</span>
        </div>
        <div className="history-summary-item">
          <span className="history-summary-value">{avgScore}</span>
          <span className="history-summary-label">平均分</span>
        </div>
        <div className="history-summary-item">
          <span className="history-summary-value">
            {filtered.reduce((a, b) => a + (b.question_count ?? 0), 0)}
          </span>
          <span className="history-summary-label">总题数</span>
        </div>
        <div className="history-summary-item">
          <span className="history-summary-value">
            {filtered.reduce((a, b) => a + (b.dimension_count ?? 0), 0)}
          </span>
          <span className="history-summary-label">总维度</span>
        </div>
      </div>

      <div className="history-table-wrapper">
        {error && (
          <div className="history-error">
            <i className="fas fa-exclamation-circle"></i>
            <span>{error}</span>
            <button onClick={fetchRecords}>重试</button>
          </div>
        )}

        {!error && (
          <table className="history-table">
            <thead>
              <tr>
                <th>总分</th>
                <th>维度数</th>
                <th>题目数</th>
                <th>开始时间</th>
                <th>结束时间</th>
                <th>操作</th>
              </tr>
            </thead>
            <tbody>
              {filtered.map(r => (
                <tr
                  key={r.candidate_id}
                  className={selectedId === r.candidate_id ? 'selected' : ''}
                  onClick={() => handleRowClick(r)}
                  style={{ cursor: 'pointer' }}
                >
                  <td className={`score ${(r.total_score ?? 0) >= 60 ? 'pass' : 'fail'}`}>
                    {r.total_score ?? '-'}
                  </td>
                  <td>{r.dimension_count ?? '-'}</td>
                  <td>{r.question_count ?? '-'}</td>
                  <td className="time">{formatTime(r.created_at)}</td>
                  <td className="time">{formatTime(r.ended_at)}</td>
                  <td>
                    <button
                      className="history-detail-btn"
                      onClick={(e) => {
                        e.stopPropagation()
                        handleRowClick(r)
                      }}
                    >
                      <i className={`fas ${selectedId === r.candidate_id ? 'fa-chevron-up' : 'fa-chevron-down'}`}></i>
                    </button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}

        {!error && !loading && filtered.length === 0 && (
          <div className="history-empty">
            <i className="fas fa-folder-open"></i>
            <p>{searchTerm ? '没有找到匹配的考试记录' : '暂无考试记录'}</p>
          </div>
        )}

        {loading && (
          <div className="history-loading">
            <i className="fas fa-spinner fa-spin"></i>
            <span>加载中...</span>
          </div>
        )}
      </div>
    </div>
  )
}
