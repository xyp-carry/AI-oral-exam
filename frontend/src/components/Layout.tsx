import { useState, useEffect } from 'react'
import { Outlet, NavLink, useNavigate, useLocation } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'
import { useExamSession } from '../hooks/useExamSession'

const baseNavItems = [
  { to: '/dashboard', icon: 'fa-chart-pie', label: '仪表盘' },
  { to: '/exam', icon: 'fa-microphone', label: 'AI 口语考试' },
  { to: '/upload', icon: 'fa-cloud-upload-alt', label: '数据上传' },
  { to: '/history', icon: 'fa-history', label: '考试历史管理' },
]

const teacherOnlyItems = [
  { to: '/courses', icon: 'fa-book', label: '课程管理' },
]

const pageTitles: Record<string, string> = {
  '/dashboard': '仪表盘',
  '/exam': 'AI 口语考试',
  '/upload': '数据上传',
  '/history': '考试历史管理',
  '/courses': '课程管理',
}

export default function Layout() {
  const [collapsed, setCollapsed] = useState(false)
  const navigate = useNavigate()
  const location = useLocation()
  const { user, fetchMe, logout } = useAuth()
  const { active: examActive } = useExamSession()

  useEffect(() => {
    fetchMe()
  }, [fetchMe])

  useEffect(() => {
    if (user === null) navigate('/login', { replace: true })
  }, [user, navigate])

  useEffect(() => {
    if (!examActive) return
    const handler = (e: BeforeUnloadEvent) => {
      e.preventDefault()
    }
    window.addEventListener('beforeunload', handler)
    return () => window.removeEventListener('beforeunload', handler)
  }, [examActive])

  const pageTitle = pageTitles[location.pathname] || 'AI Oral Exam'

  const navItems = user?.role === 'teacher'
    ? [...baseNavItems.slice(0, 2), ...teacherOnlyItems, ...baseNavItems.slice(2)]
    : baseNavItems

  const handleLogout = async () => {
    if (examActive) return
    await logout()
    navigate('/login', { replace: true })
  }

  const handleNavClick = (e: React.MouseEvent, to: string) => {
    if (examActive && to !== '/exam') {
      e.preventDefault()
    }
  }

  return (
    <div className={`admin-layout ${collapsed ? 'sidebar-collapsed' : ''}`}>
      <aside className="admin-sidebar">
        <div className="admin-sidebar-header">
          <div className="admin-logo">
            <div className="admin-logo-icon"><i className="fas fa-robot"></i></div>
            {!collapsed && (
              <div className="admin-logo-text">
                <h1>AI Oral Exam</h1>
                <p>管理系统</p>
              </div>
            )}
          </div>
          <button className="admin-collapse-btn" onClick={() => setCollapsed(!collapsed)}>
            <i className={`fas ${collapsed ? 'fa-chevron-right' : 'fa-chevron-left'}`}></i>
          </button>
        </div>

        <nav className="admin-nav">
          {navItems.map(item => {
            const disabled = examActive && item.to !== '/exam'
            return (
              <NavLink
                key={item.to}
                to={item.to}
                className={({ isActive }) =>
                  `admin-nav-item ${isActive ? 'active' : ''} ${disabled ? 'nav-disabled' : ''}`
                }
                onClick={(e) => handleNavClick(e, item.to)}
                title={collapsed ? item.label : disabled ? '考试进行中，无法跳转' : item.label}
              >
                <i className={`fas ${item.icon}`}></i>
                {!collapsed && <span>{item.label}</span>}
                {disabled && !collapsed && <i className="fas fa-lock nav-lock-icon"></i>}
              </NavLink>
            )
          })}
        </nav>

        <div className="admin-sidebar-footer">
          <button
            className={`admin-nav-item logout ${examActive ? 'nav-disabled' : ''}`}
            onClick={handleLogout}
            title={examActive ? '考试进行中，无法退出' : '退出登录'}
          >
            <i className="fas fa-sign-out-alt"></i>
            {!collapsed && <span>退出登录</span>}
            {examActive && !collapsed && <i className="fas fa-lock nav-lock-icon"></i>}
          </button>
        </div>
      </aside>

      <div className="admin-main">
        <header className="admin-topbar">
          <div className="admin-topbar-left">
            <h2 className="admin-page-title">{pageTitle}</h2>
          </div>
          <div className="admin-topbar-right">
            {examActive && (
              <span className="exam-active-badge">
                <span className="exam-active-dot"></span>
                考试进行中
              </span>
            )}
            <div className="admin-user-info">
              <div className="admin-avatar">
                <i className="fas fa-user"></i>
              </div>
              <span className="admin-username">{user?.nickname || user?.username || '用户'}</span>
            </div>
          </div>
        </header>

        <main className="admin-content">
          <Outlet />
        </main>
      </div>
    </div>
  )
}
