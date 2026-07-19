import { useState, useEffect, useCallback } from 'react'
import { useNavigate } from 'react-router-dom'
import { API_BASE } from '../config'
import { useAuth } from '../hooks/useAuth'

interface Course {
  course_id: string
  course_name: string
  description: string | null
  status: string
  created_at: string
  updated_at: string
}

interface ExamSession {
  exam_id: string
  user_id: string
  course_id: string
  exam_item_id: string
  exam_item_name: string
  description: string | null
  item_type: string | null
  total_score: number
  dimension_count: number
  question_count: number
  dimension_scores: Record<string, number>
  repository_url: string | null
  need_code_repository: boolean | number
  enable_report_analysis?: boolean
  report_total_score?: number | null
  report_judge_rule?: string | null
  exam_completed: boolean
  ended_at: string | null
  created_at: string
}

function GitUploadPanel({ item, onClose }: { item: ExamSession; onClose: () => void }) {
  const [uploadMode, setUploadMode] = useState<'git' | 'zip'>('git')
  const [gitUrl, setGitUrl] = useState('')
  const [gitBranch, setGitBranch] = useState('main')
  const [zipFile, setZipFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  const [askReload, setAskReload] = useState(false)
  const [reloadUrl, setReloadUrl] = useState('')
  const [reloadBranch, setReloadBranch] = useState('')

  const handleSubmit = async (reload = false) => {
    if (uploadMode === 'git') {
      if (!reload && !gitUrl.trim()) return
    } else {
      if (!zipFile) return
    }
    setSubmitting(true)
    setError('')
    setAskReload(false)
    try {
      if (uploadMode === 'zip') {
        const formData = new FormData()
        formData.append('course_id', item.course_id)
        formData.append('exam_id', item.exam_id)
        formData.append('file', zipFile)
        const res = await fetch(`${API_BASE}/git/repository`, {
          method: 'POST',
          credentials: 'include',
          body: formData,
        })
        const text = await res.text()
        let data: Record<string, unknown> = {}
        try { data = JSON.parse(text) } catch { /* non-JSON */ }
        if (!res.ok || data.success === false) {
          throw new Error((data.detail as string) || (data.message as string) || '上传失败')
        }
        setSuccess(true)
      } else {
        const body: Record<string, unknown> = {
          course_id: item.course_id,
          exam_id: item.exam_id,
          git_url: reload ? reloadUrl : gitUrl.trim(),
          git_branch: (reload ? reloadBranch : gitBranch).trim() || 'main',
        }
        if (reload) body.reload = true
        const res = await fetch(`${API_BASE}/git/repository`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify(body),
        })
        const data = await res.json().catch(() => ({}))
        if (!res.ok || data.success === false) {
          if (data.reason) {
            setReloadUrl(gitUrl.trim())
            setReloadBranch(gitBranch.trim())
            setAskReload(true)
            setError(data.message || data.reason)
          } else {
            throw new Error(data.detail || data.message || '提交失败')
          }
          return
        }
        setSuccess(true)
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const canSubmit = uploadMode === 'git' ? gitUrl.trim() : zipFile

  return (
    <div className="examlist-upload-overlay">
      <div className="examlist-upload-panel">
        <div className="examlist-upload-header">
          <h3><i className="fas fa-code-branch"></i> 上传代码仓库</h3>
          <button className="examlist-upload-close" onClick={onClose}>
            <i className="fas fa-times"></i>
          </button>
        </div>
        <div className="examlist-upload-body">
          {success ? (
            <div className="examlist-git-success">
              <i className="fas fa-check-circle"></i>
              <p>{uploadMode === 'git' ? '仓库地址提交成功' : '文件上传成功'}</p>
              <button className="examlist-git-confirm-btn" onClick={onClose}>确定</button>
            </div>
          ) : (
            <>
              <div className="examlist-git-info">
                <span><i className="fas fa-book"></i> {item.exam_item_name}</span>
              </div>

              {/* 模式切换 */}
              <div className="examlist-upload-tabs">
                <button
                  className={`examlist-upload-tab ${uploadMode === 'git' ? 'active' : ''}`}
                  onClick={() => { setUploadMode('git'); setError('') }}
                >
                  <i className="fab fa-git-alt"></i> Git 仓库
                </button>
                <button
                  className={`examlist-upload-tab ${uploadMode === 'zip' ? 'active' : ''}`}
                  onClick={() => { setUploadMode('zip'); setError('') }}
                >
                  <i className="fas fa-file-archive"></i> ZIP 压缩包
                </button>
              </div>

              {uploadMode === 'git' ? (
                <>
                  <div className="examlist-git-field">
                    <label>Git 仓库地址</label>
                    <input
                      type="text"
                      placeholder="https://github.com/user/repo.git"
                      value={gitUrl}
                      onChange={e => setGitUrl(e.target.value)}
                      disabled={askReload}
                    />
                  </div>
                  <div className="examlist-git-field">
                    <label>分支</label>
                    <input
                      type="text"
                      placeholder="main"
                      value={gitBranch}
                      onChange={e => setGitBranch(e.target.value)}
                      disabled={askReload}
                    />
                  </div>
                </>
              ) : (
                <div className="examlist-git-field">
                  <label>选择 ZIP 文件</label>
                  <div className="examlist-zip-drop">
                    <input
                      type="file"
                      accept=".zip"
                      id="zip-file-input"
                      className="examlist-zip-input"
                      onChange={e => setZipFile(e.target.files?.[0] ?? null)}
                    />
                    <label htmlFor="zip-file-input" className="examlist-zip-label">
                      <i className="fas fa-cloud-upload-alt"></i>
                      <span>{zipFile ? zipFile.name : '点击选择 .zip 文件'}</span>
                      {zipFile && (
                        <span className="examlist-zip-size">
                          {(zipFile.size / 1024 / 1024).toFixed(2)} MB
                        </span>
                      )}
                    </label>
                    {zipFile && (
                      <button
                        className="examlist-zip-clear"
                        onClick={() => setZipFile(null)}
                        title="移除文件"
                      >
                        <i className="fas fa-times"></i>
                      </button>
                    )}
                  </div>
                </div>
              )}

              {error && (
                <div className="examlist-git-error">
                  <i className="fas fa-exclamation-circle"></i> {error}
                </div>
              )}
              {askReload ? (
                <div className="examlist-reload-confirm">
                  <p>是否需要重置并重新上传？</p>
                  <div className="examlist-reload-actions">
                    <button className="examlist-git-cancel-btn" onClick={onClose}>不需要</button>
                    <button
                      className="examlist-git-submit-btn"
                      onClick={() => handleSubmit(true)}
                      disabled={submitting}
                    >
                      {submitting ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-redo"></i>}
                      {submitting ? '重置中...' : '重置并重新上传'}
                    </button>
                  </div>
                </div>
              ) : (
                <div className="examlist-git-actions">
                  <button className="examlist-git-cancel-btn" onClick={onClose} disabled={submitting}>取消</button>
                  <button
                    className="examlist-git-submit-btn"
                    onClick={() => handleSubmit(false)}
                    disabled={!canSubmit || submitting}
                  >
                    {submitting ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-check"></i>}
                    {submitting ? '提交中...' : '确定'}
                  </button>
                </div>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}

function ReportScorePanel({ item, onClose }: { item: ExamSession; onClose: () => void }) {
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [result, setResult] = useState<{ score?: number; feedback?: string } | null>(null)

  const handleSubmit = async () => {
    setSubmitting(true)
    setError('')
    setResult(null)
    try {
      const res = await fetch(`${API_BASE}/courses/${item.course_id}/exam_items/${item.exam_item_id}/report_score`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ rule: item.report_judge_rule ?? '' }),
      })
      const text = await res.text()
      let data: Record<string, unknown> = {}
      try { data = JSON.parse(text) } catch { /* non-JSON */ }
      if (!res.ok) {
        throw new Error((data.detail as string) || (data.message as string) || '提交失败')
      }
      setResult({
        score: data.score as number | undefined,
        feedback: data.feedback as string | undefined,
      })
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  return (
    <div className="examlist-upload-overlay">
      <div className="examlist-upload-panel report-score-panel">
        <div className="examlist-upload-header report-score-header">
          <h3><i className="fas fa-file-medical-alt"></i> 报告评估</h3>
          <button className="examlist-upload-close" onClick={onClose}>
            <i className="fas fa-times"></i>
          </button>
        </div>
        <div className="examlist-upload-body">
          {result ? (
            <div className="report-score-result">
              {result.score !== undefined && (
                <div className="report-score-grade">
                  <span className="report-score-grade-label">评分</span>
                  <span className="report-score-grade-value">{result.score}<small>/{item.report_total_score ?? 100}</small></span>
                </div>
              )}
              {result.feedback && (
                <div className="report-score-feedback">
                  <label>评价反馈</label>
                  <div className="report-score-feedback-text">{result.feedback}</div>
                </div>
              )}
              <button className="examlist-git-confirm-btn" onClick={onClose}>确定</button>
            </div>
          ) : (
            <>
              <div className="examlist-git-info report-score-info">
                <span><i className="fas fa-book"></i> {item.exam_item_name}</span>
                {item.report_total_score != null && (
                  <span className="report-score-max">满分 {item.report_total_score}</span>
                )}
              </div>

              {item.report_judge_rule && (
                <div className="report-score-rule-card">
                  <div className="report-score-rule-header">
                    <i className="fas fa-gavel"></i>
                    <span>评审规则</span>
                  </div>
                  <div className="report-score-rule-text">{item.report_judge_rule}</div>
                </div>
              )}

              <div className="report-score-tip">
                <i className="fas fa-info-circle"></i>
                <span>将基于已上传仓库中的报告内容进行评估</span>
              </div>

              {error && (
                <div className="examlist-git-error">
                  <i className="fas fa-exclamation-circle"></i> {error}
                </div>
              )}
              <div className="examlist-git-actions">
                <button className="examlist-git-cancel-btn" onClick={onClose} disabled={submitting}>取消</button>
                <button
                  className="examlist-git-submit-btn"
                  onClick={handleSubmit}
                  disabled={submitting}
                >
                  {submitting ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-paper-plane"></i>}
                  {submitting ? '评估中...' : '开始评估'}
                </button>
              </div>
            </>
          )}
        </div>
      </div>
    </div>
  )
}

export default function ExamList() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [courses, setCourses] = useState<Course[]>([])
  const [examMap, setExamMap] = useState<Record<string, ExamSession[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expandedCourse, setExpandedCourse] = useState<string | null>(null)
  const [uploadTarget, setUploadTarget] = useState<ExamSession | null>(null)
  const [reportTarget, setReportTarget] = useState<ExamSession | null>(null)
  const [repoChecking, setRepoChecking] = useState<string | null>(null)
  const [repoError, setRepoError] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/courses`, { credentials: 'include' })
      if (!res.ok) throw new Error(`请求失败: ${res.status}`)
      const json = await res.json()
      const courseList: Course[] = Array.isArray(json) ? json : json.data ?? []
      setCourses(courseList)

      const map: Record<string, ExamSession[]> = {}
      await Promise.all(
        courseList.map(async (c) => {
          try {
            const r = await fetch(`${API_BASE}/courses/${c.course_id}/exam_sessions`, { credentials: 'include' })
            if (r.ok) {
              const j = await r.json()
              map[c.course_id] = Array.isArray(j) ? j : j.data ?? []
            } else {
              map[c.course_id] = []
            }
          } catch {
            map[c.course_id] = []
          }
        })
      )
      setExamMap(map)
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取课程列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  const handleEnterExam = async (item: ExamSession) => {
    const needRepo = item.need_code_repository === true || item.need_code_repository === 1
    if (needRepo) {
      setRepoChecking(item.exam_id)
      setRepoError(null)
      try {
        const res = await fetch(
          `${API_BASE}/exam_sessions/repository_status?course_id=${item.course_id}&exam_id=${item.exam_id}`,
          { credentials: 'include' }
        )
        if (!res.ok) throw new Error('检查仓库状态失败')
        const json = await res.json()
        if (!json.has_repository_url) {
          setRepoError(item.exam_id)
          setRepoChecking(null)
          return
        }
      } catch {
        setRepoError(item.exam_id)
        setRepoChecking(null)
        return
      }
      setRepoChecking(null)
    }
    navigate(`/exam/${item.exam_id}`)
  }

  const toggleCourse = (courseId: string) => {
    setExpandedCourse(prev => prev === courseId ? null : courseId)
  }

  const totalExams = Object.values(examMap).reduce((sum, items) => sum + items.length, 0)

  if (user?.role !== 'teacher' && user?.role !== 'student') {
    return null
  }

  return (
    <div className="examlist-page">
      <div className="examlist-header">
        <div className="examlist-header-info">
          <h2>{user?.role === 'teacher' ? '管理的考试' : '我的考试'}</h2>
          <p>
            {loading ? '加载中...' : `共 ${courses.length} 门课程，${totalExams} 场考试`}
          </p>
        </div>
        <button className="examlist-refresh-btn" onClick={fetchData} disabled={loading}>
          <i className={`fas ${loading ? 'fa-spinner fa-spin' : 'fa-sync-alt'}`}></i>
          刷新
        </button>
      </div>

      {error && (
        <div className="examlist-error">
          <i className="fas fa-exclamation-circle"></i>
          <span>{error}</span>
          <button onClick={fetchData}>重试</button>
        </div>
      )}

      {loading && (
        <div className="examlist-loading">
          <i className="fas fa-spinner fa-spin"></i>
          <span>加载中...</span>
        </div>
      )}

      {!loading && !error && (
        <>
          {courses.map(course => {
            const items = examMap[course.course_id] ?? []
            const isExpanded = expandedCourse === course.course_id
            return (
              <div key={course.course_id} className="examlist-course-group">
                <div
                  className={`examlist-course-header ${isExpanded ? 'expanded' : ''}`}
                  onClick={() => toggleCourse(course.course_id)}
                >
                  <div className="examlist-course-info">
                    <i className="fas fa-book"></i>
                    <span className="examlist-course-name">{course.course_name}</span>
                    <span className="examlist-course-count">{items.length} 场考试</span>
                  </div>
                  <i className={`fas fa-chevron-${isExpanded ? 'up' : 'down'} examlist-course-arrow`}></i>
                </div>

                {isExpanded && (
                  <div className="examlist-course-body">
                    {items.length === 0 ? (
                      <div className="examlist-course-empty">暂无考试</div>
                    ) : (
                      <div className="examlist-grid">
                        {items.map(item => {
                          const needRepo = item.need_code_repository === true || item.need_code_repository === 1
                          const dimNames = item.dimension_scores ? Object.keys(item.dimension_scores) : []
                          return (
                            <div
                              key={item.exam_id}
                              className="examlist-card"
                            >
                              <div className="examlist-card-header">
                                <h4>{item.exam_item_name}</h4>
                                <span className="examlist-meta-score">总分 {item.total_score}</span>
                              </div>
                              {item.description && (
                                <p className="examlist-card-desc">{item.description}</p>
                              )}
                              <div className="examlist-card-meta">
                                <span><i className="fas fa-layer-group"></i> {dimNames.length} 维度</span>
                                {item.item_type && <span><i className="fas fa-tag"></i> {item.item_type}</span>}
                                {item.exam_completed && <span className="examlist-completed-badge"><i className="fas fa-check-circle"></i> 已完成</span>}
                                {needRepo && <span className="examlist-repo-badge"><i className="fas fa-code"></i> 需要仓库</span>}
                              </div>
                              {dimNames.length > 0 && (
                                <div className="examlist-card-dimensions">
                                  {dimNames.map((name, i) => (
                                    <span key={i} className="examlist-card-dim-tag">{name}: {item.dimension_scores[name]}分</span>
                                  ))}
                                </div>
                              )}
                              {repoError === item.exam_id && (
                                <div className="examlist-repo-error">
                                  <i className="fas fa-exclamation-triangle"></i>
                                  请先上传代码仓库后再进入考试
                                </div>
                              )}
                              <div className="examlist-card-actions">
                                {needRepo && (
                                  <button
                                    className="examlist-upload-btn"
                                    onClick={(e) => { e.stopPropagation(); setUploadTarget(item) }}
                                    disabled={item.exam_completed}
                                  >
                                    <i className="fas fa-code-branch"></i> 上传仓库
                                  </button>
                                )}
                                {item.enable_report_analysis && (
                                  <button
                                    className="examlist-report-btn"
                                    onClick={(e) => { e.stopPropagation(); setReportTarget(item) }}
                                    disabled={item.exam_completed}
                                  >
                                    <i className="fas fa-file-medical-alt"></i> 报告评估
                                  </button>
                                )}
                                <button
                                  className="examlist-enter-btn"
                                  onClick={() => handleEnterExam(item)}
                                  disabled={repoChecking === item.exam_id || item.exam_completed}
                                >
                                  <i className={`fas ${item.exam_completed ? 'fa-lock' : repoChecking === item.exam_id ? 'fa-spinner fa-spin' : 'fa-arrow-right'}`}></i>
                                  {item.exam_completed ? '已完成' : repoChecking === item.exam_id ? '检查中...' : '进入考试'}
                                </button>
                              </div>
                            </div>
                          )
                        })}
                      </div>
                    )}
                  </div>
                )}
              </div>
            )
          })}

          {courses.length === 0 && (
            <div className="examlist-empty">
              <i className="fas fa-clipboard-list"></i>
              <p>暂无课程</p>
            </div>
          )}
        </>
      )}

      {uploadTarget && (
        <GitUploadPanel item={uploadTarget} onClose={() => setUploadTarget(null)} />
      )}
      {reportTarget && (
        <ReportScorePanel item={reportTarget} onClose={() => setReportTarget(null)} />
      )}
    </div>
  )
}
