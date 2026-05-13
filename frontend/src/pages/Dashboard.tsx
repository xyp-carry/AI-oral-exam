export default function Dashboard() {
  const stats = [
    { icon: 'fa-users', label: '注册用户', value: '1,284', color: 'var(--accent)' },
    { icon: 'fa-microphone', label: '考试次数', value: '3,567', color: 'var(--accent2)' },
    { icon: 'fa-clock', label: '平均时长', value: '12:34', color: '#f59e0b' },
    { icon: 'fa-chart-line', label: '通过率', value: '87.3%', color: '#8b5cf6' },
  ]

  const recentExams = [
    { id: 1, user: '张三', subject: '英语口语', score: 92, time: '2026-05-04 14:30', status: 'passed' },
    { id: 2, user: '李四', subject: '日语口语', score: 78, time: '2026-05-04 13:15', status: 'passed' },
    { id: 3, user: '王五', subject: '英语口语', score: 45, time: '2026-05-04 11:00', status: 'failed' },
    { id: 4, user: '赵六', subject: '法语口语', score: 88, time: '2026-05-03 16:45', status: 'passed' },
    { id: 5, user: '孙七', subject: '英语口语', score: 95, time: '2026-05-03 10:20', status: 'passed' },
  ]

  return (
    <div className="dashboard-page">
      <div className="dashboard-stats">
        {stats.map(s => (
          <div key={s.label} className="stat-card">
            <div className="stat-icon" style={{ background: `${s.color}20`, color: s.color }}>
              <i className={`fas ${s.icon}`}></i>
            </div>
            <div className="stat-info">
              <span className="stat-value">{s.value}</span>
              <span className="stat-label">{s.label}</span>
            </div>
          </div>
        ))}
      </div>

      <div className="dashboard-grid">
        <div className="dashboard-card">
          <div className="dashboard-card-header">
            <h3><i className="fas fa-clock"></i> 最近考试</h3>
          </div>
          <div className="dashboard-card-body">
            <table className="dashboard-table">
              <thead>
                <tr>
                  <th>用户</th>
                  <th>科目</th>
                  <th>分数</th>
                  <th>时间</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {recentExams.map(exam => (
                  <tr key={exam.id}>
                    <td>{exam.user}</td>
                    <td>{exam.subject}</td>
                    <td className="score">{exam.score}</td>
                    <td className="time">{exam.time}</td>
                    <td>
                      <span className={`status-badge ${exam.status}`}>
                        {exam.status === 'passed' ? '通过' : '未通过'}
                      </span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        <div className="dashboard-card">
          <div className="dashboard-card-header">
            <h3><i className="fas fa-chart-bar"></i> 科目分布</h3>
          </div>
          <div className="dashboard-card-body">
            <div className="chart-bars">
              {[
                { label: '英语口语', count: 156, pct: 65 },
                { label: '日语口语', count: 89, pct: 37 },
                { label: '法语口语', count: 67, pct: 28 },
                { label: '德语口语', count: 45, pct: 19 },
                { label: '韩语口语', count: 34, pct: 14 },
              ].map(item => (
                <div key={item.label} className="chart-bar-row">
                  <span className="chart-bar-label">{item.label}</span>
                  <div className="chart-bar-track">
                    <div className="chart-bar-fill" style={{ width: `${item.pct}%` }}></div>
                  </div>
                  <span className="chart-bar-value">{item.count}</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>

      <div className="dashboard-grid">
        <div className="dashboard-card">
          <div className="dashboard-card-header">
            <h3><i className="fas fa-bolt"></i> 快捷操作</h3>
          </div>
          <div className="dashboard-card-body">
            <div className="quick-actions">
              <a href="/exam" className="quick-action-item">
                <i className="fas fa-microphone"></i>
                <span>开始考试</span>
              </a>
              <a href="/upload" className="quick-action-item">
                <i className="fas fa-cloud-upload-alt"></i>
                <span>上传数据</span>
              </a>
              <a href="/history" className="quick-action-item">
                <i className="fas fa-history"></i>
                <span>考试记录</span>
              </a>
            </div>
          </div>
        </div>

        <div className="dashboard-card">
          <div className="dashboard-card-header">
            <h3><i className="fas fa-server"></i> 系统状态</h3>
          </div>
          <div className="dashboard-card-body">
            <div className="system-status-list">
              {[
                { label: 'WebRTC 服务', status: 'online' },
                { label: 'AI 模型服务', status: 'online' },
                { label: '数据库', status: 'online' },
                { label: '文件存储', status: 'online' },
              ].map(item => (
                <div key={item.label} className="system-status-item">
                  <span className="system-status-dot online"></span>
                  <span className="system-status-label">{item.label}</span>
                  <span className="system-status-text">运行中</span>
                </div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}
