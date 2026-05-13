import { useState } from 'react'

interface ExamRecord {
  id: string
  user: string
  subject: string
  score: number
  duration: string
  date: string
  status: 'passed' | 'failed' | 'pending'
  details: string
}

const mockData: ExamRecord[] = [
  { id: 'E001', user: '张三', subject: '英语口语 Level 3', score: 92, duration: '15:23', date: '2026-05-04 14:30', status: 'passed', details: '发音准确，语流自然，词汇丰富' },
  { id: 'E002', user: '李四', subject: '日语口语 Level 2', score: 78, duration: '12:45', date: '2026-05-04 13:15', status: 'passed', details: '基础表达流畅，部分语法需改进' },
  { id: 'E003', user: '王五', subject: '英语口语 Level 4', score: 45, duration: '08:12', date: '2026-05-04 11:00', status: 'failed', details: '发音偏差较大，词汇量不足' },
  { id: 'E004', user: '赵六', subject: '法语口语 Level 1', score: 88, duration: '14:56', date: '2026-05-03 16:45', status: 'passed', details: '语调自然，表达清晰' },
  { id: 'E005', user: '孙七', subject: '英语口语 Level 5', score: 95, duration: '18:30', date: '2026-05-03 10:20', status: 'passed', details: '表现优秀，接近母语水平' },
  { id: 'E006', user: '周八', subject: '德语口语 Level 2', score: 62, duration: '10:05', date: '2026-05-02 15:30', status: 'passed', details: '基础对话尚可，需加强听力理解' },
  { id: 'E007', user: '吴九', subject: '韩语口语 Level 3', score: 38, duration: '06:45', date: '2026-05-02 09:15', status: 'failed', details: '发音问题较多，无法完成基本对话' },
  { id: 'E008', user: '郑十', subject: '英语口语 Level 2', score: 81, duration: '13:20', date: '2026-05-01 14:00', status: 'passed', details: '表达流畅，偶有语法错误' },
  { id: 'E009', user: '陈一一', subject: '日语口语 Level 4', score: 72, duration: '16:40', date: '2026-05-01 11:30', status: 'passed', details: '高级表达有待提升' },
  { id: 'E010', user: '林二二', subject: '法语口语 Level 2', score: 55, duration: '09:50', date: '2026-04-30 16:20', status: 'failed', details: '词汇量不足，表达不连贯' },
]

export default function ExamHistory() {
  const [searchTerm, setSearchTerm] = useState('')
  const [filterStatus, setFilterStatus] = useState<'all' | 'passed' | 'failed'>('all')
  const [filterSubject, setFilterSubject] = useState('all')
  const [expandedId, setExpandedId] = useState<string | null>(null)

  const subjects = [...new Set(mockData.map(d => d.subject.split(' ')[0]))]

  const filtered = mockData.filter(r => {
    if (searchTerm && !r.user.includes(searchTerm) && !r.id.includes(searchTerm)) return false
    if (filterStatus !== 'all' && r.status !== filterStatus) return false
    if (filterSubject !== 'all' && !r.subject.startsWith(filterSubject)) return false
    return true
  })

  return (
    <div className="history-page">
      <div className="history-filters">
        <div className="history-search">
          <i className="fas fa-search"></i>
          <input
            type="text"
            placeholder="搜索用户名或考试编号..."
            value={searchTerm}
            onChange={e => setSearchTerm(e.target.value)}
          />
        </div>

        <div className="history-filter-group">
          <select value={filterStatus} onChange={e => setFilterStatus(e.target.value as typeof filterStatus)}>
            <option value="all">全部状态</option>
            <option value="passed">已通过</option>
            <option value="failed">未通过</option>
          </select>

          <select value={filterSubject} onChange={e => setFilterSubject(e.target.value)}>
            <option value="all">全部科目</option>
            {subjects.map(s => (
              <option key={s} value={s}>{s}</option>
            ))}
          </select>
        </div>
      </div>

      <div className="history-summary">
        <div className="history-summary-item">
          <span className="history-summary-value">{filtered.length}</span>
          <span className="history-summary-label">总记录</span>
        </div>
        <div className="history-summary-item">
          <span className="history-summary-value passed">{filtered.filter(r => r.status === 'passed').length}</span>
          <span className="history-summary-label">已通过</span>
        </div>
        <div className="history-summary-item">
          <span className="history-summary-value failed">{filtered.filter(r => r.status === 'failed').length}</span>
          <span className="history-summary-label">未通过</span>
        </div>
        <div className="history-summary-item">
          <span className="history-summary-value">{filtered.length > 0 ? Math.round(filtered.reduce((a, b) => a + b.score, 0) / filtered.length) : 0}</span>
          <span className="history-summary-label">平均分</span>
        </div>
      </div>

      <div className="history-table-wrapper">
        <table className="history-table">
          <thead>
            <tr>
              <th>编号</th>
              <th>用户</th>
              <th>科目</th>
              <th>分数</th>
              <th>时长</th>
              <th>日期</th>
              <th>状态</th>
              <th>操作</th>
            </tr>
          </thead>
          <tbody>
            {filtered.map(r => (
              <>
                <tr key={r.id} className={expandedId === r.id ? 'expanded' : ''}>
                  <td className="mono">{r.id}</td>
                  <td>{r.user}</td>
                  <td>{r.subject}</td>
                  <td className={`score ${r.score >= 60 ? 'pass' : 'fail'}`}>{r.score}</td>
                  <td className="mono">{r.duration}</td>
                  <td className="time">{r.date}</td>
                  <td>
                    <span className={`status-badge ${r.status}`}>
                      {r.status === 'passed' ? '通过' : r.status === 'failed' ? '未通过' : '待审核'}
                    </span>
                  </td>
                  <td>
                    <button
                      className="history-detail-btn"
                      onClick={() => setExpandedId(expandedId === r.id ? null : r.id)}
                    >
                      <i className={`fas ${expandedId === r.id ? 'fa-chevron-up' : 'fa-chevron-down'}`}></i>
                    </button>
                  </td>
                </tr>
                {expandedId === r.id && (
                  <tr key={`${r.id}-detail`} className="detail-row">
                    <td colSpan={8}>
                      <div className="history-detail">
                        <div className="history-detail-section">
                          <h4>考试评价</h4>
                          <p>{r.details}</p>
                        </div>
                        <div className="history-detail-section">
                          <h4>分数明细</h4>
                          <div className="score-breakdown">
                            {[
                              { label: '发音', score: Math.min(100, r.score + Math.floor(Math.random() * 10 - 5)) },
                              { label: '语法', score: Math.min(100, r.score + Math.floor(Math.random() * 15 - 7)) },
                              { label: '词汇', score: Math.min(100, r.score + Math.floor(Math.random() * 12 - 6)) },
                              { label: '流利度', score: Math.min(100, r.score + Math.floor(Math.random() * 8 - 4)) },
                            ].map(item => (
                              <div key={item.label} className="score-item">
                                <span>{item.label}</span>
                                <div className="score-bar-track">
                                  <div
                                    className="score-bar-fill"
                                    style={{
                                      width: `${item.score}%`,
                                      background: item.score >= 60
                                        ? 'linear-gradient(90deg, var(--accent), var(--accent2))'
                                        : '#ef4444'
                                    }}
                                  ></div>
                                </div>
                                <span className="score-num">{item.score}</span>
                              </div>
                            ))}
                          </div>
                        </div>
                      </div>
                    </td>
                  </tr>
                )}
              </>
            ))}
          </tbody>
        </table>

        {filtered.length === 0 && (
          <div className="history-empty">
            <i className="fas fa-search"></i>
            <p>没有找到匹配的考试记录</p>
          </div>
        )}
      </div>
    </div>
  )
}
