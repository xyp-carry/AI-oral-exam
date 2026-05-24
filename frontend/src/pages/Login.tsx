import { useState, type FormEvent } from 'react'
import { useNavigate } from 'react-router-dom'
import { useAuth } from '../hooks/useAuth'

type Mode = 'login' | 'register'

export default function Login() {
  const navigate = useNavigate()
  const { loading, login, register } = useAuth()
  const [mode, setMode] = useState<Mode>('login')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [email, setEmail] = useState('')
  const [nickname, setNickname] = useState('')
  const [role, setRole] = useState<'student' | 'teacher'>('student')
  const [rememberMe, setRememberMe] = useState(false)
  const [error, setError] = useState('')

  const resetForm = () => {
    setUsername('')
    setPassword('')
    setEmail('')
    setNickname('')
    setRole('student')
    setRememberMe(false)
    setError('')
  }

  const switchMode = (m: Mode) => {
    setMode(m)
    resetForm()
  }

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    setError('')

    if (!username.trim() || !password.trim()) {
      setError('请输入用户名和密码')
      return
    }
    if (mode === 'register' && !email.trim()) {
      setError('请输入邮箱')
      return
    }

    try {
      if (mode === 'login') {
        await login(username.trim(), password, rememberMe)
      } else {
        await register(username.trim(), password, email.trim(), nickname.trim() || undefined, role)
      }
      navigate('/dashboard', { replace: true })
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    }
  }

  return (
    <div className="login-page">
      <div className="login-bg-orb login-bg-orb-1"></div>
      <div className="login-bg-orb login-bg-orb-2"></div>

      <div className="login-card">
        <div className="login-header">
          <div className="login-logo">
            <div className="login-logo-icon"><i className="fas fa-robot"></i></div>
          </div>
          <h1>AI Oral Exam</h1>
          <p>智能口语考试管理系统</p>
        </div>

        <div className="login-tabs">
          <button
            className={`login-tab ${mode === 'login' ? 'active' : ''}`}
            onClick={() => switchMode('login')}
          >
            登录
          </button>
          <button
            className={`login-tab ${mode === 'register' ? 'active' : ''}`}
            onClick={() => switchMode('register')}
          >
            注册
          </button>
        </div>

        <form className="login-form" onSubmit={handleSubmit}>
          {error && (
            <div className="login-error">
              <i className="fas fa-exclamation-circle"></i>
              {error}
            </div>
          )}

          <div className="login-field">
            <label htmlFor="username">
              <i className="fas fa-user"></i> 用户名
            </label>
            <input
              id="username"
              type="text"
              placeholder="请输入用户名"
              value={username}
              onChange={e => setUsername(e.target.value)}
              autoComplete="username"
            />
          </div>

          {mode === 'register' && (
            <div className="login-field">
              <label htmlFor="email">
                <i className="fas fa-envelope"></i> 邮箱
              </label>
              <input
                id="email"
                type="email"
                placeholder="请输入邮箱"
                value={email}
                onChange={e => setEmail(e.target.value)}
                autoComplete="email"
              />
            </div>
          )}

          <div className="login-field">
            <label htmlFor="password">
              <i className="fas fa-lock"></i> 密码
            </label>
            <input
              id="password"
              type="password"
              placeholder="请输入密码"
              value={password}
              onChange={e => setPassword(e.target.value)}
              autoComplete={mode === 'login' ? 'current-password' : 'new-password'}
            />
          </div>

          {mode === 'register' && (
            <div className="login-field">
              <label htmlFor="nickname">
                <i className="fas fa-id-card"></i> 昵称
                <span className="login-field-hint">（选填）</span>
              </label>
              <input
                id="nickname"
                type="text"
                placeholder="给自己起个名字吧"
                value={nickname}
                onChange={e => setNickname(e.target.value)}
              />
            </div>
          )}

          {mode === 'register' && (
            <div className="login-field">
              <label>
                <i className="fas fa-user-tag"></i> 角色
              </label>
              <div className="login-role-group">
                <button
                  type="button"
                  className={`login-role-btn ${role === 'student' ? 'active' : ''}`}
                  onClick={() => setRole('student')}
                >
                  <i className="fas fa-user-graduate"></i>
                  <span>学生</span>
                </button>
                <button
                  type="button"
                  className={`login-role-btn ${role === 'teacher' ? 'active' : ''}`}
                  onClick={() => setRole('teacher')}
                >
                  <i className="fas fa-chalkboard-teacher"></i>
                  <span>教师</span>
                </button>
              </div>
            </div>
          )}

          {mode === 'login' && (
            <div className="login-options">
              <label className="login-remember">
                <input
                  type="checkbox"
                  checked={rememberMe}
                  onChange={e => setRememberMe(e.target.checked)}
                />
                <span>记住我</span>
              </label>
            </div>
          )}

          <button className="login-btn" type="submit" disabled={loading}>
            {loading ? (
              <><i className="fas fa-spinner fa-spin"></i> {mode === 'login' ? '登录中' : '注册中'}...</>
            ) : (
              <><i className={`fas ${mode === 'login' ? 'fa-sign-in-alt' : 'fa-user-plus'}`}></i> {mode === 'login' ? '登录' : '注册'}</>
            )}
          </button>
        </form>

        <div className="login-footer">
          {mode === 'login' ? (
            <p>还没有账号？<a href="#" onClick={e => { e.preventDefault(); switchMode('register') }}>立即注册</a></p>
          ) : (
            <p>已有账号？<a href="#" onClick={e => { e.preventDefault(); switchMode('login') }}>去登录</a></p>
          )}
        </div>
      </div>
    </div>
  )
}
