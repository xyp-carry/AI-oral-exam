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
  exam_type: string | null
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

interface AnalysisProgress {
  status: string
  stage: string
  completed_rounds: number | null
  current_round: number | null
  last_tool: string
  percent: number
  /** 解析完成后返回的仓库标题 */
  title: string
}

/** 从进度响应解析百分数：优先后端百分比字段，否则用已完成轮次估算；上限 99（避免未完成却显示 100%） */
function parseAnalysisPercent(data: Record<string, unknown>): number {
  const raw = (data.percent ?? data.progress ?? data.progress_percent ?? data.percentage
    ?? data.completed_rounds ?? data.rounds_completed) as unknown
  let v = typeof raw === 'number' ? Math.floor(raw) : 0
  if (v >= 100) v = 99
  if (v < 0) v = 0
  return v
}

/** 解析进度响应为统一结构（无分析任务/文档时 status 为空） */
function parseAnalysisProgress(data: Record<string, unknown>): AnalysisProgress {
  const completedRounds = (data.completed_rounds ?? data.rounds_completed) as unknown
  const currentRound = (data.current_round ?? data.running_round) as unknown
  return {
    status: String(data.status ?? ''),
    stage: String(data.stage ?? data.current_stage ?? data.phase ?? ''),
    completed_rounds: typeof completedRounds === 'number' ? completedRounds : null,
    current_round: typeof currentRound === 'number' ? currentRound : null,
    last_tool: String(data.current_tool ?? data.last_tool ?? data.latest_tool ?? data.active_tool ?? ''),
    percent: parseAnalysisPercent(data),
    title: typeof data.title === 'string' ? data.title : '',
  }
}

function GitUploadPanel({ item, onClose, onLeaveAnalysis }: {
  item: ExamSession
  onClose: () => void
  /** 弹窗关闭时若解析仍在进行，通知列表页按 exam_id+course_id 继续轮询（userid 由后端从登录态解析） */
  onLeaveAnalysis?: () => void
}) {
  const [uploadMode, setUploadMode] = useState<'zip' | 'analysis'>('analysis')
  const [gitUrl, setGitUrl] = useState('')
  const [zipFile, setZipFile] = useState<File | null>(null)
  const [submitting, setSubmitting] = useState(false)
  const [error, setError] = useState('')
  const [success, setSuccess] = useState(false)
  // Git 仓库解析进度轮询（analysisId 仅作为"已提交、进入等待态"的标志）
  const [analysisId, setAnalysisId] = useState<string | null>(null)
  const [analysisProgress, setAnalysisProgress] = useState<AnalysisProgress | null>(null)

  /** 每 1 秒轮询解析进度（按 exam_id + course_id 查询），completed/failed 时停止 */
  useEffect(() => {
    if (!analysisId) return
    let active = true
    let timer: number | undefined
    const poll = async () => {
      if (!active) return
      try {
        const res = await fetch(
          `${API_BASE}/Agent/analysis/exams/${item.exam_id}/progress?course_id=${item.course_id}`,
          { credentials: 'include' }
        )
        if (!res.ok) return
        const json = await res.json().catch(() => null)
        const data = ((json?.data ?? json) ?? {}) as Record<string, unknown>
        const progress = parseAnalysisProgress(data)
        setAnalysisProgress(progress)
        if (progress.status === 'completed') {
          active = false
          if (timer !== undefined) clearInterval(timer)
          setSuccess(true)
        } else if (progress.status === 'failed') {
          active = false
          if (timer !== undefined) clearInterval(timer)
          setAnalysisId(null)
          setError(String(data.message ?? data.error ?? '解析失败'))
        }
      } catch { /* 单次轮询失败忽略，下秒重试 */ }
    }
    poll()
    timer = window.setInterval(poll, 1000)
    return () => {
      active = false
      if (timer !== undefined) clearInterval(timer)
    }
  }, [analysisId, item.exam_id, item.course_id])

  /** 关闭弹窗：解析仍在进行时通知列表页接管轮询 */
  const handleClose = () => {
    if (analysisId && !success) onLeaveAnalysis?.()
    onClose()
  }

  const handleSubmit = async () => {
    if (uploadMode === 'analysis') {
      if (!gitUrl.trim()) return
    } else {
      if (!zipFile) return
    }
    setSubmitting(true)
    setError('')
    try {
      if (uploadMode === 'analysis') {
        // Git 仓库：POST /Agent/analysis（url + exam_id + course_id），无需选择分支
        const res = await fetch(`${API_BASE}/Agent/analysis`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            url: gitUrl.trim(),
            exam_id: item.exam_id,
            course_id: item.course_id,
          }),
        })
        const data = await res.json().catch(() => ({}))
        if (!res.ok || data.success === false) {
          throw new Error(data.detail || data.message || '解析请求失败')
        }
        // 拿到 analysis_id 后进入进度轮询；未返回则视为直接完成
        const aid = (data?.data?.analysis_id ?? data?.analysis_id) as string | undefined
        if (aid) {
          setAnalysisProgress(null)
          setAnalysisId(aid)
        } else {
          setSuccess(true)
        }
      } else {
        const formData = new FormData()
        formData.append('course_id', item.course_id)
        formData.append('exam_id', item.exam_id)
        if (zipFile) formData.append('file', zipFile)
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
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '提交失败')
    } finally {
      setSubmitting(false)
    }
  }

  const canSubmit = uploadMode === 'zip' ? zipFile : gitUrl.trim()

  return (
    <div className="examlist-upload-overlay">
      <div className="examlist-upload-panel">
        <div className="examlist-upload-header">
          <h3><i className="fas fa-code-branch"></i> 上传代码仓库</h3>
          <button className="examlist-upload-close" onClick={handleClose}>
            <i className="fas fa-times"></i>
          </button>
        </div>
        <div className="examlist-upload-body">
          {success ? (
            <div className="examlist-git-success">
              <i className="fas fa-check-circle"></i>
              <p>{uploadMode === 'analysis' ? '仓库解析完成' : '文件上传成功'}</p>
              {uploadMode === 'analysis' && analysisProgress?.title && (
                <p className="examlist-analysis-title-result">
                  <i className="fas fa-bookmark"></i> {analysisProgress.title}
                </p>
              )}
              <button className="examlist-git-confirm-btn" onClick={handleClose}>确定</button>
            </div>
          ) : analysisId ? (
            /* 解析进度等待视图：百分比展示，可离开（列表页继续轮询） */
            <div className="examlist-analysis-progress">
              <div className="examlist-analysis-head">
                <i className="fas fa-cloud-upload-alt"></i>
                <div>
                  <p className="examlist-analysis-title">正在上传请耐心等待 {analysisProgress?.percent ?? 0}%</p>
                  <p className="examlist-analysis-sub">解析完成后将自动继续；离开后可在考试列表查看进度</p>
                </div>
              </div>
              <div className="examlist-analysis-bar">
                <div className="examlist-analysis-bar-fill" style={{ width: `${analysisProgress?.percent ?? 0}%` }}></div>
              </div>
              <div className="examlist-analysis-rows">
                <div className="examlist-analysis-row">
                  <span>当前阶段</span>
                  <b>{analysisProgress?.stage || '准备中'}</b>
                </div>
                {analysisProgress?.completed_rounds != null && (
                  <div className="examlist-analysis-row">
                    <span>已完成轮次</span>
                    <b>{analysisProgress.completed_rounds}</b>
                  </div>
                )}
                {analysisProgress?.current_round != null && analysisProgress.current_round > 0 && (
                  <div className="examlist-analysis-row">
                    <span>进行中轮次</span>
                    <b>第 {analysisProgress.current_round} 轮</b>
                  </div>
                )}
                {analysisProgress?.last_tool && (
                  <div className="examlist-analysis-row">
                    <span>最近启动工具</span>
                    <b>{analysisProgress.last_tool}</b>
                  </div>
                )}
              </div>
              <div className="examlist-analysis-actions">
                <button className="examlist-git-cancel-btn" onClick={handleClose}>
                  <i className="fas fa-door-open"></i> 离开
                </button>
              </div>
            </div>
          ) : (
            <>
              <div className="examlist-git-info">
                <span><i className="fas fa-book"></i> {item.exam_item_name}</span>
              </div>

              {/* 模式切换 */}
              <div className="examlist-upload-tabs">
                <button
                  className={`examlist-upload-tab ${uploadMode === 'analysis' ? 'active' : ''}`}
                  onClick={() => { setUploadMode('analysis'); setError('') }}
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

              {uploadMode === 'analysis' ? (
                <div className="examlist-git-field">
                  <label>Git 仓库地址</label>
                  <input
                    type="text"
                    placeholder="https://github.com/user/repo.git"
                    value={gitUrl}
                    onChange={e => setGitUrl(e.target.value)}
                  />
                  <div className="examlist-git-hint">
                    <i className="fas fa-info-circle"></i> 仅需仓库地址，无需选择分支，提交后由解析服务处理
                  </div>
                </div>
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
              <div className="examlist-git-actions">
                <button className="examlist-git-cancel-btn" onClick={handleClose} disabled={submitting}>取消</button>
                <button
                  className="examlist-git-submit-btn"
                  onClick={handleSubmit}
                  disabled={!canSubmit || submitting}
                >
                  {submitting ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-check"></i>}
                  {submitting ? '提交中...' : '确定'}
                </button>
              </div>
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

/** 考试结果查看弹窗：拉取 GET /exam_sessions/{exam_id}/final_review，展示 data.html */
function FinalReviewPanel({ item, onClose }: { item: ExamSession; onClose: () => void }) {
  const [loading, setLoading] = useState(true)
  const [html, setHtml] = useState<string | null>(null)
  const [failed, setFailed] = useState(false)

  useEffect(() => {
    let cancelled = false
    ;(async () => {
      try {
        const res = await fetch(`${API_BASE}/exam_sessions/${item.exam_id}/final_review`, { credentials: 'include' })
        const json = await res.json().catch(() => ({})) as { success?: boolean; data?: { html?: string } }
        if (cancelled) return
        const htmlStr = json?.data?.html
        if (!res.ok || !json?.success || !htmlStr) {
          setFailed(true)
        } else {
          setHtml(htmlStr)
        }
      } catch {
        if (!cancelled) setFailed(true)
      } finally {
        if (!cancelled) setLoading(false)
      }
    })()
    return () => { cancelled = true }
  }, [item.exam_id])

  return (
    <div className="examlist-upload-overlay">
      <div className="examlist-upload-panel final-review-panel">
        <div className="examlist-upload-header">
          <h3><i className="fas fa-file-alt"></i> 考试结果 · {item.exam_item_name}</h3>
          <button className="examlist-upload-close" onClick={onClose}>
            <i className="fas fa-times"></i>
          </button>
        </div>
        <div className="examlist-upload-body">
          {loading ? (
            <div className="final-review-loading">
              <i className="fas fa-spinner fa-spin"></i>
              <span>加载中...</span>
            </div>
          ) : failed ? (
            <div className="final-review-empty">
              <i className="fas fa-hourglass-half"></i>
              <p>还没有完成评价</p>
              <button className="examlist-git-confirm-btn" onClick={onClose}>确定</button>
            </div>
          ) : (
            <iframe
              className="final-review-frame"
              srcDoc={html!}
              title="考试结果"
              sandbox="allow-same-origin"
            />
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
  const [reviewTarget, setReviewTarget] = useState<ExamSession | null>(null)
  const [repoChecking, setRepoChecking] = useState<string | null>(null)
  const [repoError, setRepoError] = useState<string | null>(null)
  // 进行中的仓库解析（按 exam_id+course_id 轮询）：exam_id -> { courseId, percent, stage }
  const [activeAnalyses, setActiveAnalyses] = useState<Record<string, { courseId: string; percent: number; stage: string }>>({})
  // 解析完成后的仓库标题：exam_id -> title（卡片显示"已上传"）
  const [analysisTitles, setAnalysisTitles] = useState<Record<string, string>>({})

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

      // 遍历所有考试，按 exam_id+course_id 查询解析进度，恢复进行中的解析状态（页面刷新后不丢失）
      const running: Record<string, { courseId: string; percent: number; stage: string }> = {}
      const titles: Record<string, string> = {}
      await Promise.all(
        Object.entries(map).flatMap(([courseId, exams]) =>
          exams.map(async (ex) => {
            try {
              const r = await fetch(
                `${API_BASE}/Agent/analysis/exams/${ex.exam_id}/progress?course_id=${courseId}`,
                { credentials: 'include' }
              )
              if (!r.ok) return
              const j = await r.json().catch(() => null)
              const data = ((j?.data ?? j) ?? {}) as Record<string, unknown>
              const progress = parseAnalysisProgress(data)
              if (progress.status && progress.status !== 'completed' && progress.status !== 'failed') {
                running[ex.exam_id] = { courseId, percent: progress.percent, stage: progress.stage }
              } else if (progress.status === 'completed' && progress.title) {
                titles[ex.exam_id] = progress.title
              }
            } catch { /* ignore */ }
          })
        )
      )
      if (Object.keys(running).length > 0) {
        setActiveAnalyses(prev => ({ ...running, ...prev }))
      }
      if (Object.keys(titles).length > 0) {
        setAnalysisTitles(prev => ({ ...prev, ...titles }))
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取课程列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchData()
  }, [fetchData])

  /** 列表页轮询所有进行中的解析，completed/failed 时移除并刷新列表 */
  useEffect(() => {
    const entries = Object.entries(activeAnalyses)
    if (entries.length === 0) return
    let active = true
    const timer = window.setInterval(async () => {
      if (!active) return
      await Promise.all(entries.map(async ([examId, info]) => {
        try {
          const res = await fetch(
            `${API_BASE}/Agent/analysis/exams/${examId}/progress?course_id=${info.courseId}`,
            { credentials: 'include' }
          )
          if (!res.ok) return
          const json = await res.json().catch(() => null)
          const data = ((json?.data ?? json) ?? {}) as Record<string, unknown>
          const progress = parseAnalysisProgress(data)
          if (progress.status === 'completed') {
            if (progress.title) {
              setAnalysisTitles(prev => ({ ...prev, [examId]: progress.title }))
            }
            setActiveAnalyses(prev => {
              const next = { ...prev }
              delete next[examId]
              return next
            })
            fetchData()
          } else if (progress.status === 'failed') {
            setActiveAnalyses(prev => {
              const next = { ...prev }
              delete next[examId]
              return next
            })
          } else {
            setActiveAnalyses(prev => prev[examId]
              ? { ...prev, [examId]: { ...prev[examId], percent: progress.percent, stage: progress.stage } }
              : prev)
          }
        } catch { /* 单次失败忽略 */ }
      }))
    }, 1000)
    return () => {
      active = false
      clearInterval(timer)
    }
  }, [activeAnalyses, fetchData])

  const handleEnterExam = async (item: ExamSession) => {
    const needRepo = item.need_code_repository === true || item.need_code_repository === 1
    if (needRepo) {
      setRepoChecking(item.exam_id)
      setRepoError(null)
      try {
        // 通过解析进度接口判断仓库是否已上传并解析完成
        const res = await fetch(
          `${API_BASE}/Agent/analysis/exams/${item.exam_id}/progress?course_id=${item.course_id}`,
          { credentials: 'include' }
        )
        if (!res.ok) throw new Error('检查仓库状态失败')
        const json = await res.json().catch(() => null)
        const data = ((json?.data ?? json) ?? {}) as Record<string, unknown>
        const progress = parseAnalysisProgress(data)
        if (progress.status !== 'completed') {
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
    navigate(`/exam/${item.exam_id}`, { state: { exam_type: item.exam_type } })
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
                                {item.item_type && <span><i className="fas fa-tag"></i> {item.item_type}</span>}
                                {item.exam_completed && <span className="examlist-completed-badge"><i className="fas fa-check-circle"></i> 已完成</span>}
                                {needRepo && <span className="examlist-repo-badge"><i className="fas fa-code"></i> 需要仓库</span>}
                                {analysisTitles[item.exam_id] && (
                                  <span className="examlist-uploaded-badge" data-tip={`已上传仓库：${analysisTitles[item.exam_id]}`}>
                                    <i className="fas fa-check-circle"></i>
                                    已上传
                                  </span>
                                )}
                                {activeAnalyses[item.exam_id] && (
                                  <span className="examlist-analysis-badge">
                                    <i className="fas fa-sync-alt fa-spin"></i>
                                    解析中 {activeAnalyses[item.exam_id].percent}%
                                  </span>
                                )}
                              </div>
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
                                    disabled={item.exam_completed || !!activeAnalyses[item.exam_id]}
                                    title={activeAnalyses[item.exam_id] ? '仓库解析进行中，完成后可再次上传' : undefined}
                                  >
                                    <i className={`fas ${activeAnalyses[item.exam_id] ? 'fa-sync-alt fa-spin' : 'fa-code-branch'}`}></i>
                                    {activeAnalyses[item.exam_id] ? `解析中 ${activeAnalyses[item.exam_id].percent}%` : '上传仓库'}
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
                                  onClick={() => item.exam_completed ? setReviewTarget(item) : handleEnterExam(item)}
                                  disabled={!item.exam_completed && repoChecking === item.exam_id}
                                >
                                  <i className={`fas ${item.exam_completed ? 'fa-file-alt' : repoChecking === item.exam_id ? 'fa-spinner fa-spin' : 'fa-arrow-right'}`}></i>
                                  {item.exam_completed ? '查看考试结果' : repoChecking === item.exam_id ? '检查中...' : '进入考试'}
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
        <GitUploadPanel
          item={uploadTarget}
          onClose={() => setUploadTarget(null)}
          onLeaveAnalysis={() => {
            const { exam_id, course_id } = uploadTarget
            setActiveAnalyses(prev => prev[exam_id]
              ? prev
              : { ...prev, [exam_id]: { courseId: course_id, percent: 0, stage: '' } })
          }}
        />
      )}
      {reportTarget && (
        <ReportScorePanel item={reportTarget} onClose={() => setReportTarget(null)} />
      )}
      {reviewTarget && (
        <FinalReviewPanel item={reviewTarget} onClose={() => setReviewTarget(null)} />
      )}
    </div>
  )
}
