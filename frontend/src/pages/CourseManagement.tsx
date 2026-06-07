import { useState, useEffect, useCallback, useMemo } from 'react'
import { API_BASE } from '../config'
import { useAuth } from '../hooks/useAuth'

interface Course {
  course_id: string
  course_name: string
  description: string | null
  invite_code: string | null
  invite_code_expires_at: string | null
  invite_code_created_at: string | null
  invite_code_valid: boolean
  status: string
  created_at: string
  updated_at: string
}

interface Dimension {
  name: string
  score: number
}

interface PresetQuestion {
  question_id?: string
  id?: string
  preset_question_id?: string
  question_dimension: string
  question_content: string
  standard_answer: string
  score: number
  sort_order: number
}

interface ExamItem {
  exam_item_id: string
  course_id: string
  exam_item_name: string
  description: string | null
  item_type: string | null
  total_score: number
  participant_count: number
  attempt_count: number
  status: string
  created_at: string
  updated_at: string
  dimension_names: string[]
  dimension_scores: Record<string, number>
  exam_available_valid_times: number
  need_code_repository: boolean
  use_preset_questions: boolean
  exam_available_from: string | null
  exam_available_until: string | null
}

function formatDuration(totalSeconds: number): string {
  if (totalSeconds <= 0) return '已过期'
  const h = Math.floor(totalSeconds / 3600)
  const m = Math.floor((totalSeconds % 3600) / 60)
  const s = Math.floor(totalSeconds % 60)
  const parts: string[] = []
  if (h > 0) parts.push(`${h}时`)
  if (m > 0) parts.push(`${m}分`)
  if (s > 0 || parts.length === 0) parts.push(`${s}秒`)
  return parts.join('')
}

function getRemainingSeconds(until: string | null): number | null {
  if (!until) return null
  const diff = (new Date(until).getTime() - Date.now()) / 1000
  return Math.max(0, Math.floor(diff))
}

function parseDurationInput(h: number, m: number, s: number): number {
  return h * 3600 + m * 60 + s
}

function CountdownTimer({ until }: { until: string | null }) {
  const [remaining, setRemaining] = useState<number | null>(() => getRemainingSeconds(until))

  useEffect(() => {
    if (!until) {
      setRemaining(null)
      return
    }
    const update = () => setRemaining(getRemainingSeconds(until))
    update()
    const timer = setInterval(update, 1000)
    return () => clearInterval(timer)
  }, [until])

  if (remaining === null) return <span>未设置</span>
  return <span className={remaining <= 0 ? 'exam-valid-expired' : 'exam-valid-active'}>{formatDuration(remaining)}</span>
}

export default function CourseManagement() {
  const { user } = useAuth()
  const [courses, setCourses] = useState<Course[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedCourse, setSelectedCourse] = useState<string | null>(null)
  const [examItems, setExamItems] = useState<ExamItem[]>([])
  const [examsLoading, setExamsLoading] = useState(false)
  const [showCreateCourse, setShowCreateCourse] = useState(false)

  const [showExamModal, setShowExamModal] = useState(false)
  const [editingExam, setEditingExam] = useState<ExamItem | null>(null)
  const [examName, setExamName] = useState('')
  const [examDesc, setExamDesc] = useState('')
  const [examItemType, setExamItemType] = useState('')
  const [availH, setAvailH] = useState(0)
  const [availM, setAvailM] = useState(30)
  const [availS, setAvailS] = useState(0)
  const [needCodeRepo, setNeedCodeRepo] = useState(false)
  const [usePresetQuestions, setUsePresetQuestions] = useState(false)
  const [dimensions, setDimensions] = useState<Dimension[]>([{ name: '', score: 0 }])
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)
  const [deleteCourseConfirmId, setDeleteCourseConfirmId] = useState<string | null>(null)
  const [deletingCourse, setDeletingCourse] = useState(false)
  const [copied, setCopied] = useState(false)
  const [showResetInvite, setShowResetInvite] = useState(false)
  const [resetInviteH, setResetInviteH] = useState(0)
  const [resetInviteM, setResetInviteM] = useState(30)
  const [resetInviteS, setResetInviteS] = useState(0)
  const [resettingInvite, setResettingInvite] = useState(false)
  const [showRefreshValid, setShowRefreshValid] = useState(false)
  const [refreshValidId, setRefreshValidId] = useState<string | null>(null)
  const [refreshValidH, setRefreshValidH] = useState(0)
  const [refreshValidM, setRefreshValidM] = useState(30)
  const [refreshValidS, setRefreshValidS] = useState(0)
  const [refreshingValid, setRefreshingValid] = useState(false)

  const [showPresetModal, setShowPresetModal] = useState(false)
  const [presetExamItem, setPresetExamItem] = useState<ExamItem | null>(null)
  const [presetQuestions, setPresetQuestions] = useState<PresetQuestion[]>([])
  const [presetLoading, setPresetLoading] = useState(false)
  const [pqDimension, setPqDimension] = useState('')
  const [pqContent, setPqContent] = useState('')
  const [pqAnswer, setPqAnswer] = useState('')
  const [pqScore, setPqScore] = useState(10)
  const [pqSortOrder, setPqSortOrder] = useState(0)
  const [pqSubmitting, setPqSubmitting] = useState(false)
  const [newCourseName, setNewCourseName] = useState('')
  const [newCourseDesc, setNewCourseDesc] = useState('')

  const totalScore = useMemo(() => dimensions.reduce((sum, d) => sum + (Number(d.score) || 0), 0), [dimensions])

  const fetchCourses = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/courses`, { credentials: 'include' })
      if (!res.ok) throw new Error(`请求失败: ${res.status}`)
      const json = await res.json()
      setCourses(Array.isArray(json) ? json : json.data ?? [])
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取课程列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  const fetchExamItems = useCallback(async (courseId: string) => {
    setExamsLoading(true)
    try {
      const res = await fetch(`${API_BASE}/courses/${courseId}/exam_items`, { credentials: 'include' })
      if (!res.ok) throw new Error(`请求失败: ${res.status}`)
      const json = await res.json()
      setExamItems(Array.isArray(json) ? json : json.data ?? [])
    } catch {
      setExamItems([])
    } finally {
      setExamsLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchCourses()
  }, [fetchCourses])

  useEffect(() => {
    if (selectedCourse) {
      fetchExamItems(selectedCourse)
    } else {
      setExamItems([])
    }
  }, [selectedCourse, fetchExamItems])

  const handleCreateCourse = async () => {
    if (!newCourseName.trim()) return
    try {
      const res = await fetch(`${API_BASE}/courses`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          course_name: newCourseName.trim(),
          description: newCourseDesc.trim() || undefined,
        }),
      })
      if (!res.ok) throw new Error('创建课程失败')
      setShowCreateCourse(false)
      setNewCourseName('')
      setNewCourseDesc('')
      fetchCourses()
    } catch (err) {
      setError(err instanceof Error ? err.message : '创建课程失败')
    }
  }

  const resetExamForm = () => {
    setExamName('')
    setExamDesc('')
    setExamItemType('')
    setAvailH(0)
    setAvailM(30)
    setAvailS(0)
    setNeedCodeRepo(false)
    setUsePresetQuestions(false)
    setDimensions([{ name: '', score: 0 }])
    setEditingExam(null)
  }

  const openCreateExam = () => {
    resetExamForm()
    setShowExamModal(true)
  }

  const openEditExam = (item: ExamItem) => {
    setEditingExam(item)
    setExamName(item.exam_item_name)
    setExamDesc(item.description || '')
    setExamItemType(item.item_type || '')
    const totalSec = item.exam_available_valid_times ?? 1800
    setAvailH(Math.floor(totalSec / 3600))
    setAvailM(Math.floor((totalSec % 3600) / 60))
    setAvailS(totalSec % 60)
    setNeedCodeRepo(item.need_code_repository === true)
    setUsePresetQuestions(item.use_preset_questions === true)
    const dims = item.dimension_names && item.dimension_names.length > 0
      ? item.dimension_names.map(name => ({ name, score: item.dimension_scores?.[name] ?? 0 }))
      : [{ name: '', score: 0 }]
    setDimensions(dims)
    setShowExamModal(true)
  }

  const handleSaveExam = async () => {
    if (!examName.trim() || !selectedCourse) return
    const validDimensions = dimensions.filter(d => d.name.trim() && d.score > 0)
    if (validDimensions.length === 0) return
    const availSeconds = parseDurationInput(availH, availM, availS)
    if (availSeconds <= 0) return

    const body = {
      exam_item_name: examName.trim(),
      dimensions: validDimensions,
      exam_available_valid_times: editingExam ? 0 : availSeconds,
      need_code_repository: needCodeRepo,
      use_preset_questions: usePresetQuestions,
      description: examDesc.trim() || undefined,
      item_type: examItemType.trim() || undefined,
    }

    try {
      let res: Response
      if (editingExam) {
        res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${editingExam.exam_item_id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify(body),
        })
      } else {
        res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify(body),
        })
      }
      if (!res.ok) throw new Error(editingExam ? '修改考试失败' : '创建考试失败')
      setShowExamModal(false)
      resetExamForm()
      fetchExamItems(selectedCourse)
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    }
  }

  const handleDeleteExam = async (examItemId: string) => {
    if (!selectedCourse) return
    setDeleting(true)
    try {
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${examItemId}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!res.ok) throw new Error('删除考试失败')
      setDeleteConfirmId(null)
      fetchExamItems(selectedCourse)
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
    } finally {
      setDeleting(false)
    }
  }

  const handleDeleteCourse = async (courseId: string) => {
    setDeletingCourse(true)
    try {
      const res = await fetch(`${API_BASE}/courses/${courseId}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!res.ok) throw new Error('删除课程失败')
      setDeleteCourseConfirmId(null)
      if (selectedCourse === courseId) {
        setSelectedCourse(null)
        setExamItems([])
      }
      fetchCourses()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除课程失败')
    } finally {
      setDeletingCourse(false)
    }
  }

  const addDimension = () => {
    setDimensions(prev => [...prev, { name: '', score: 0 }])
  }

  const removeDimension = (index: number) => {
    if (dimensions.length <= 1) return
    setDimensions(prev => prev.filter((_, i) => i !== index))
  }

  const updateDimension = (index: number, field: keyof Dimension, value: string | number) => {
    setDimensions(prev => prev.map((d, i) => i === index ? { ...d, [field]: value } : d))
  }

  const selectedCourseData = courses.find(c => c.course_id === selectedCourse)

  const handleCopyCode = () => {
    if (!selectedCourseData?.invite_code) return
    navigator.clipboard.writeText(selectedCourseData.invite_code).then(() => {
      setCopied(true)
      setTimeout(() => setCopied(false), 2000)
    })
  }

  const handleResetInviteCode = async () => {
    if (!selectedCourse) return
    const seconds = parseDurationInput(resetInviteH, resetInviteM, resetInviteS)
    if (seconds <= 0) return
    setResettingInvite(true)
    try {
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/invite_code`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ invite_code_valid_times: seconds }),
      })
      if (!res.ok) throw new Error('重置邀请码失败')
      setShowResetInvite(false)
      setResetInviteH(0)
      setResetInviteM(30)
      setResetInviteS(0)
      fetchCourses()
    } catch (err) {
      setError(err instanceof Error ? err.message : '重置邀请码失败')
    } finally {
      setResettingInvite(false)
    }
  }

  const openRefreshValid = (examItemId: string) => {
    setRefreshValidId(examItemId)
    setRefreshValidH(0)
    setRefreshValidM(30)
    setRefreshValidS(0)
    setShowRefreshValid(true)
  }

  const handleRefreshValidTime = async () => {
    if (!selectedCourse || !refreshValidId) return
    const seconds = parseDurationInput(refreshValidH, refreshValidM, refreshValidS)
    if (seconds <= 0) return
    const examItem = examItems.find(e => e.exam_item_id === refreshValidId)
    if (!examItem) return
    setRefreshingValid(true)
    try {
      const dims = examItem.dimension_names && examItem.dimension_names.length > 0
        ? examItem.dimension_names.map(name => ({ name, score: examItem.dimension_scores?.[name] ?? 0 }))
        : []
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${refreshValidId}`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          exam_item_name: examItem.exam_item_name,
          dimensions: dims,
          exam_available_valid_times: seconds,
          need_code_repository: examItem.need_code_repository === true,
          use_preset_questions: examItem.use_preset_questions === true,
          description: examItem.description || undefined,
          item_type: examItem.item_type || undefined,
        }),
      })
      if (!res.ok) throw new Error('刷新有效期失败')
      setShowRefreshValid(false)
      setRefreshValidId(null)
      fetchExamItems(selectedCourse)
    } catch (err) {
      setError(err instanceof Error ? err.message : '刷新有效期失败')
    } finally {
      setRefreshingValid(false)
    }
  }

  const openPresetQuestions = async (item: ExamItem) => {
    setPresetExamItem(item)
    resetPqForm()
    setShowPresetModal(true)
    setPresetLoading(true)
    try {
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${item.exam_item_id}/preset_questions`, { credentials: 'include' })
      if (res.ok) {
        const json = await res.json()
        console.log('preset questions response:', json)
        setPresetQuestions(Array.isArray(json) ? json : json.data ?? [])
      } else {
        setPresetQuestions([])
      }
    } catch {
      setPresetQuestions([])
    } finally {
      setPresetLoading(false)
    }
  }

  const resetPqForm = () => {
    setPqDimension(presetExamItem?.dimension_names?.[0] || '')
    setPqContent('')
    setPqAnswer('')
    setPqScore(10)
    setPqSortOrder(presetQuestions.length)
  }

  const handleAddPresetQuestion = async () => {
    if (!selectedCourse || !presetExamItem) return
    if (presetQuestions.length >= 10) return
    if (!pqDimension || !pqContent.trim() || !pqAnswer.trim()) return
    setPqSubmitting(true)
    try {
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${presetExamItem.exam_item_id}/preset_questions`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          question_dimension: pqDimension,
          question_content: pqContent.trim(),
          standard_answer: pqAnswer.trim(),
          score: pqScore,
          sort_order: pqSortOrder,
        }),
      })
      if (!res.ok) throw new Error('添加题目失败')
      resetPqForm()
      const listRes = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${presetExamItem.exam_item_id}/preset_questions`, { credentials: 'include' })
      if (listRes.ok) {
        const json = await listRes.json()
        setPresetQuestions(Array.isArray(json) ? json : json.data ?? [])
      }
    } catch (err) {
      setError(err instanceof Error ? err.message : '添加题目失败')
    } finally {
      setPqSubmitting(false)
    }
  }

  const handleDeletePresetQuestion = async (questionId: string) => {
    if (!selectedCourse || !presetExamItem) return
    try {
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${presetExamItem.exam_item_id}/preset_questions/${questionId}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!res.ok) {
        const text = await res.text()
        console.error('删除题目失败:', res.status, text)
        throw new Error('删除题目失败')
      }
      setPresetQuestions(prev => prev.filter(q => {
        const qId = q.question_id || q.id || q.preset_question_id
        return qId !== questionId
      }))
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除题目失败')
    }
  }

  if (user?.role !== 'teacher') {
    return (
      <div className="course-page">
        <div className="course-forbidden">
          <i className="fas fa-lock"></i>
          <p>仅教师可访问课程管理</p>
        </div>
      </div>
    )
  }

  return (
    <div className="course-page">
      <div className="course-layout">
        <div className="course-sidebar-panel">
          <div className="course-panel-header">
            <h3><i className="fas fa-book"></i> 我的课程</h3>
            <button className="course-add-btn" onClick={() => setShowCreateCourse(true)}>
              <i className="fas fa-plus"></i>
            </button>
          </div>

          {loading && (
            <div className="course-loading"><i className="fas fa-spinner fa-spin"></i></div>
          )}

          {error && (
            <div className="course-error-inline">
              <i className="fas fa-exclamation-circle"></i>
              <span>{error}</span>
            </div>
          )}

          <div className="course-list">
            {courses.map(c => (
              <div
                key={c.course_id}
                className={`course-list-item ${selectedCourse === c.course_id ? 'active' : ''}`}
                onClick={() => { setSelectedCourse(c.course_id); setDeleteCourseConfirmId(null) }}
              >
                <div className="course-list-item-name">{c.course_name}</div>
                <div className="course-list-item-meta">
                  <span><i className="fas fa-calendar-alt"></i> {c.created_at?.slice(0, 10)}</span>
                  <button className="course-list-item-del" onClick={e => { e.stopPropagation(); setDeleteCourseConfirmId(c.course_id) }} title="删除课程">
                    <i className="fas fa-trash-alt"></i>
                  </button>
                </div>
                {deleteCourseConfirmId === c.course_id && (
                  <div className="course-list-item-confirm" onClick={e => e.stopPropagation()}>
                    <span>确定删除？</span>
                    <button className="confirm-yes" onClick={() => handleDeleteCourse(c.course_id)} disabled={deletingCourse}>
                      {deletingCourse ? <i className="fas fa-spinner fa-spin"></i> : '确定'}
                    </button>
                    <button className="confirm-no" onClick={() => setDeleteCourseConfirmId(null)}>取消</button>
                  </div>
                )}
              </div>
            ))}
            {!loading && courses.length === 0 && (
              <div className="course-list-empty">
                <i className="fas fa-book-open"></i>
                <p>暂无课程</p>
              </div>
            )}
          </div>
        </div>

        <div className="course-main-panel">
          {!selectedCourse ? (
            <div className="course-placeholder">
              <i className="fas fa-hand-point-left"></i>
              <p>请从左侧选择一个课程</p>
            </div>
          ) : (
            <>
              <div className="course-detail-header">
                <div className="course-detail-info">
                  <h2>{selectedCourseData?.course_name}</h2>
                  <p>{selectedCourseData?.description || '暂无描述'}</p>
                </div>
                <button className="course-add-btn" onClick={openCreateExam}>
                  <i className="fas fa-plus"></i> 新增考试
                </button>
              </div>

              <div className="course-invite-bar">
                <div className="course-invite-bar-left">
                  <i className="fas fa-key"></i>
                  <span className="course-invite-bar-label">课程邀请码</span>
                  {selectedCourseData?.invite_code && selectedCourseData.invite_code_valid ? (
                    <>
                      <span className="course-invite-bar-code">{selectedCourseData.invite_code}</span>
                      <span className="course-invite-bar-exp">
                        <i className="fas fa-clock"></i> 过期: {selectedCourseData.invite_code_expires_at}
                      </span>
                    </>
                  ) : (
                    <span className="course-invite-bar-expired">邀请码不存在或已过期</span>
                  )}
                </div>
                <div className="course-invite-bar-right">
                  {selectedCourseData?.invite_code && selectedCourseData.invite_code_valid ? (
                    <button className="course-invite-copy-btn" onClick={handleCopyCode}>
                      <i className={`fas ${copied ? 'fa-check' : 'fa-copy'}`}></i>
                      {copied ? '已复制' : '复制'}
                    </button>
                  ) : (
                    <button className="course-invite-reset-btn" onClick={() => setShowResetInvite(true)}>
                      <i className="fas fa-sync-alt"></i> 重置邀请码
                    </button>
                  )}
                </div>
              </div>

              <div className="course-exam-list">
                {examsLoading && (
                  <div className="course-loading"><i className="fas fa-spinner fa-spin"></i></div>
                )}

                {!examsLoading && examItems.length === 0 && (
                  <div className="course-list-empty">
                    <i className="fas fa-clipboard-list"></i>
                    <p>暂无考试，点击上方按钮新增</p>
                  </div>
                )}

                {!examsLoading && examItems.map(item => (
                  <div key={item.exam_item_id} className="course-exam-card">
                    <div className="course-exam-card-header">
                      <h4>{item.exam_item_name}</h4>
                      <div className="course-exam-card-actions">
                        <span className="course-exam-meta-score">总分 {item.total_score}</span>
                        <button className="course-exam-action-btn edit" onClick={() => openEditExam(item)} title="编辑">
                          <i className="fas fa-pen"></i>
                        </button>
                        <button className="course-exam-action-btn delete" onClick={() => setDeleteConfirmId(item.exam_item_id)} title="删除">
                          <i className="fas fa-trash-alt"></i>
                        </button>
                      </div>
                    </div>
                    <div className="course-exam-card-body">
                      {item.description && <p className="course-exam-desc">{item.description}</p>}
                      <div className="course-exam-meta">
                        <span><i className="fas fa-layer-group"></i> {item.dimension_names?.length ?? 0} 个维度</span>
                        {item.item_type && <span><i className="fas fa-tag"></i> {item.item_type}</span>}
                        <span><i className="fas fa-users"></i> {item.participant_count} 人参与</span>
                        <span><i className="fas fa-clock"></i> 有效期 <CountdownTimer until={item.exam_available_until} /></span>
                        <button className="course-exam-valid-refresh-btn" onClick={() => openRefreshValid(item.exam_item_id)} title="刷新有效期">
                          <i className="fas fa-sync-alt"></i> 刷新
                        </button>
                        <span><i className="fas fa-calendar-alt"></i> {item.created_at?.slice(0, 10)}</span>
                      </div>
                      {item.dimension_names && item.dimension_names.length > 0 && (
                        <div className="course-exam-dimensions">
                          {item.dimension_names.map((name, i) => (
                            <span key={i} className="course-exam-dim-tag">{name}: {item.dimension_scores?.[name] ?? 0}分</span>
                          ))}
                        </div>
                      )}
                      {(item.use_preset_questions === true || Number(item.use_preset_questions) === 1) && (
                        <div className="course-exam-preset-bar">
                          <button className="course-exam-preset-btn" onClick={() => openPresetQuestions(item)}>
                            <i className="fas fa-database"></i> 预设题库 ({presetQuestions.length})
                          </button>
                        </div>
                      )}
                    </div>

                    {deleteConfirmId === item.exam_item_id && (
                      <div className="course-exam-delete-confirm">
                        <span>确定删除此考试项？</span>
                        <div className="course-exam-delete-confirm-btns">
                          <button className="course-modal-btn cancel" onClick={() => setDeleteConfirmId(null)} disabled={deleting}>取消</button>
                          <button className="course-modal-btn danger" onClick={() => handleDeleteExam(item.exam_item_id)} disabled={deleting}>
                            {deleting ? <i className="fas fa-spinner fa-spin"></i> : '删除'}
                          </button>
                        </div>
                      </div>
                    )}
                  </div>
                ))}
              </div>
            </>
          )}
        </div>
      </div>

      {showCreateCourse && (
        <div className="course-modal-overlay" onClick={() => setShowCreateCourse(false)}>
          <div className="course-modal" onClick={e => e.stopPropagation()}>
            <div className="course-modal-header">
              <h3>新增课程</h3>
              <button className="course-modal-close" onClick={() => setShowCreateCourse(false)}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="course-modal-body">
              <div className="course-modal-field">
                <label>课程名称</label>
                <input type="text" placeholder="请输入课程名称" value={newCourseName} onChange={e => setNewCourseName(e.target.value)} />
              </div>
              <div className="course-modal-field">
                <label>课程描述</label>
                <textarea placeholder="请输入课程描述（选填）" value={newCourseDesc} onChange={e => setNewCourseDesc(e.target.value)} rows={3} />
              </div>
            </div>
            <div className="course-modal-footer">
              <button className="course-modal-btn cancel" onClick={() => setShowCreateCourse(false)}>取消</button>
              <button className="course-modal-btn confirm" onClick={handleCreateCourse} disabled={!newCourseName.trim()}>创建</button>
            </div>
          </div>
        </div>
      )}

      {showExamModal && (
        <div className="course-modal-overlay" onClick={() => setShowExamModal(false)}>
          <div className="course-modal course-modal-wide" onClick={e => e.stopPropagation()}>
            <div className="course-modal-header">
              <h3>{editingExam ? '编辑考试' : '新增考试'}</h3>
              <button className="course-modal-close" onClick={() => setShowExamModal(false)}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="course-modal-body">
              <div className="course-modal-field">
                <label>考试名称</label>
                <input type="text" placeholder="请输入考试名称" value={examName} onChange={e => setExamName(e.target.value)} />
              </div>
              <div className="course-modal-field">
                <label>考试描述</label>
                <textarea placeholder="请输入考试描述（选填）" value={examDesc} onChange={e => setExamDesc(e.target.value)} rows={2} />
              </div>
              <div className="course-modal-field">
                <label>考试类型</label>
                <input type="text" placeholder="请输入考试类型（选填）" value={examItemType} onChange={e => setExamItemType(e.target.value)} />
              </div>

              <div className="course-modal-field">
                <label>是否需要代码仓库</label>
                <div className="course-exam-toggle-row">
                  <button
                    type="button"
                    className={`course-exam-toggle-btn ${needCodeRepo ? 'active' : ''}`}
                    onClick={() => setNeedCodeRepo(true)}
                  >
                    <i className="fas fa-check-circle"></i> 是
                  </button>
                  <button
                    type="button"
                    className={`course-exam-toggle-btn ${!needCodeRepo ? 'active' : ''}`}
                    onClick={() => setNeedCodeRepo(false)}
                  >
                    <i className="fas fa-times-circle"></i> 否
                  </button>
                </div>
              </div>

              <div className="course-modal-field">
                <label>是否需要预备题目</label>
                <div className="course-exam-toggle-row">
                  <button
                    type="button"
                    className={`course-exam-toggle-btn ${usePresetQuestions ? 'active' : ''}`}
                    onClick={() => setUsePresetQuestions(true)}
                  >
                    <i className="fas fa-check-circle"></i> 是
                  </button>
                  <button
                    type="button"
                    className={`course-exam-toggle-btn ${!usePresetQuestions ? 'active' : ''}`}
                    onClick={() => setUsePresetQuestions(false)}
                  >
                    <i className="fas fa-times-circle"></i> 否
                  </button>
                </div>
              </div>

              {!editingExam && (
                <div className="course-modal-field">
                  <label>考试有效时间 <span className="invite-duration-preview">{formatDuration(parseDurationInput(availH, availM, availS))}</span></label>
                <div className="invite-duration-row">
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={23} value={availH || ''} onChange={e => setAvailH(Math.max(0, Number(e.target.value)))} placeholder="0" />
                    <span>时</span>
                  </div>
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={59} value={availM || ''} onChange={e => setAvailM(Math.max(0, Number(e.target.value)))} placeholder="30" />
                    <span>分</span>
                  </div>
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={59} value={availS || ''} onChange={e => setAvailS(Math.max(0, Number(e.target.value)))} placeholder="0" />
                    <span>秒</span>
                  </div>
                </div>
              </div>
              )}

              <div className="course-modal-field">
                <label>
                  评分维度
                  <span className="dimension-total">总分: {totalScore}</span>
                </label>
                <div className="dimension-list">
                  {dimensions.map((dim, idx) => (
                    <div key={idx} className="dimension-row">
                      <span className="dimension-index">{idx + 1}</span>
                      <input
                        type="text"
                        placeholder="输入维度名称，如：语法、流利度、发音"
                        value={dim.name}
                        onChange={e => updateDimension(idx, 'name', e.target.value)}
                        className="dimension-name-input"
                      />
                      <div className="dimension-score-wrapper">
                        <input
                          type="number"
                          placeholder="分值"
                          min={0}
                          value={dim.score || ''}
                          onChange={e => updateDimension(idx, 'score', Number(e.target.value))}
                          className="dimension-score-input"
                        />
                        <span className="dimension-score-unit">分</span>
                      </div>
                      <button
                        type="button"
                        className={`dimension-remove-btn ${dimensions.length <= 1 ? 'disabled' : ''}`}
                        onClick={() => removeDimension(idx)}
                        disabled={dimensions.length <= 1}
                        title="删除此维度"
                      >
                        <i className="fas fa-trash-alt"></i>
                      </button>
                    </div>
                  ))}
                  <button type="button" className="dimension-add-btn" onClick={addDimension}>
                    <i className="fas fa-plus"></i> 添加维度
                  </button>
                </div>
              </div>
            </div>
            <div className="course-modal-footer">
              <button className="course-modal-btn cancel" onClick={() => setShowExamModal(false)}>取消</button>
              <button
                className="course-modal-btn confirm"
                onClick={handleSaveExam}
                disabled={!examName.trim() || dimensions.every(d => !d.name.trim() || d.score <= 0) || (!editingExam && parseDurationInput(availH, availM, availS) <= 0)}
              >
                {editingExam ? '保存修改' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showResetInvite && (
        <div className="course-modal-overlay" onClick={() => setShowResetInvite(false)}>
          <div className="course-modal" onClick={e => e.stopPropagation()}>
            <div className="course-modal-header">
              <h3>重置邀请码</h3>
              <button className="course-modal-close" onClick={() => setShowResetInvite(false)}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="course-modal-body">
              <div className="course-modal-field">
                <label>设置有效时长 <span className="invite-duration-preview">{formatDuration(parseDurationInput(resetInviteH, resetInviteM, resetInviteS))}</span></label>
                <div className="invite-duration-row">
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={23} value={resetInviteH || ''} onChange={e => setResetInviteH(Math.max(0, Number(e.target.value)))} placeholder="0" />
                    <span>时</span>
                  </div>
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={59} value={resetInviteM || ''} onChange={e => setResetInviteM(Math.max(0, Number(e.target.value)))} placeholder="30" />
                    <span>分</span>
                  </div>
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={59} value={resetInviteS || ''} onChange={e => setResetInviteS(Math.max(0, Number(e.target.value)))} placeholder="0" />
                    <span>秒</span>
                  </div>
                </div>
              </div>
            </div>
            <div className="course-modal-footer">
              <button className="course-modal-btn cancel" onClick={() => setShowResetInvite(false)} disabled={resettingInvite}>取消</button>
              <button
                className="course-modal-btn confirm"
                onClick={handleResetInviteCode}
                disabled={resettingInvite || parseDurationInput(resetInviteH, resetInviteM, resetInviteS) <= 0}
              >
                {resettingInvite ? <i className="fas fa-spinner fa-spin"></i> : '确认重置'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showRefreshValid && (
        <div className="course-modal-overlay" onClick={() => setShowRefreshValid(false)}>
          <div className="course-modal" onClick={e => e.stopPropagation()}>
            <div className="course-modal-header">
              <h3>刷新考试有效期</h3>
              <button className="course-modal-close" onClick={() => setShowRefreshValid(false)}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="course-modal-body">
              <div className="course-modal-field">
                <label>设置新的有效时长 <span className="invite-duration-preview">{formatDuration(parseDurationInput(refreshValidH, refreshValidM, refreshValidS))}</span></label>
                <div className="invite-duration-row">
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={23} value={refreshValidH || ''} onChange={e => setRefreshValidH(Math.max(0, Number(e.target.value)))} placeholder="0" />
                    <span>时</span>
                  </div>
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={59} value={refreshValidM || ''} onChange={e => setRefreshValidM(Math.max(0, Number(e.target.value)))} placeholder="30" />
                    <span>分</span>
                  </div>
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={59} value={refreshValidS || ''} onChange={e => setRefreshValidS(Math.max(0, Number(e.target.value)))} placeholder="0" />
                    <span>秒</span>
                  </div>
                </div>
              </div>
            </div>
            <div className="course-modal-footer">
              <button className="course-modal-btn cancel" onClick={() => setShowRefreshValid(false)} disabled={refreshingValid}>取消</button>
              <button
                className="course-modal-btn confirm"
                onClick={handleRefreshValidTime}
                disabled={refreshingValid || parseDurationInput(refreshValidH, refreshValidM, refreshValidS) <= 0}
              >
                {refreshingValid ? <i className="fas fa-spinner fa-spin"></i> : '确认刷新'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showPresetModal && presetExamItem && (
        <div className="course-modal-overlay" onClick={() => setShowPresetModal(false)}>
          <div className="course-modal course-modal-wide" onClick={e => e.stopPropagation()}>
            <div className="course-modal-header">
              <h3><i className="fas fa-database"></i> 预设题库 — {presetExamItem.exam_item_name}</h3>
              <button className="course-modal-close" onClick={() => setShowPresetModal(false)}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="course-modal-body preset-modal-body">
              {presetLoading ? (
                <div className="course-loading"><i className="fas fa-spinner fa-spin"></i></div>
              ) : (
                <>
                  <div className="preset-question-list">
                    {presetQuestions.length === 0 ? (
                      <div className="preset-empty">暂无预设题目</div>
                    ) : presetQuestions.map((q, idx) => {
                      const qId = q.question_id || q.id || q.preset_question_id
                      return (
                      <div key={qId || idx} className="preset-question-item">
                        <div className="preset-question-header">
                          <span className="preset-question-num">#{idx + 1}</span>
                          <span className="preset-question-dim">{q.question_dimension}</span>
                          <span className="preset-question-score">{q.score}分</span>
                          <button className="preset-question-del" onClick={() => qId && handleDeletePresetQuestion(qId)}>
                            <i className="fas fa-trash-alt"></i>
                          </button>
                        </div>
                        <div className="preset-question-content">{q.question_content}</div>
                        <div className="preset-question-answer">
                          <span className="preset-answer-label">参考答案：</span>
                          {q.standard_answer}
                        </div>
                      </div>
                    )})}
                  </div>

                  <div className="preset-add-section">
                    <h4><i className="fas fa-plus-circle"></i> 添加新题目</h4>
                    <div className="course-modal-field">
                      <label>维度</label>
                      <select value={pqDimension} onChange={e => setPqDimension(e.target.value)}>
                        <option value="">选择维度</option>
                        {presetExamItem.dimension_names?.map(name => (
                          <option key={name} value={name}>{name}</option>
                        ))}
                      </select>
                    </div>
                    <div className="course-modal-field">
                      <label>问题内容</label>
                      <textarea value={pqContent} onChange={e => setPqContent(e.target.value)} placeholder="输入问题描述" rows={3} />
                    </div>
                    <div className="course-modal-field">
                      <label>参考答案</label>
                      <textarea value={pqAnswer} onChange={e => setPqAnswer(e.target.value)} placeholder="输入标准答案" rows={2} />
                    </div>
                    <div className="preset-form-row">
                      <div className="course-modal-field preset-field-sm">
                        <label>分值</label>
                        <input type="number" min={1} value={pqScore || ''} onChange={e => setPqScore(Number(e.target.value))} />
                      </div>
                      <div className="course-modal-field preset-field-sm">
                        <label>排序</label>
                        <input type="number" min={0} value={pqSortOrder} onChange={e => setPqSortOrder(Number(e.target.value))} />
                      </div>
                    </div>
                    <div className="preset-form-actions">
                      <button className="course-modal-btn confirm" onClick={handleAddPresetQuestion} disabled={pqSubmitting || presetQuestions.length >= 10 || !pqDimension || !pqContent.trim() || !pqAnswer.trim()}>
                        {pqSubmitting ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-plus"></i>}
                        {pqSubmitting ? '提交中...' : presetQuestions.length >= 10 ? '已达上限(10题)' : '添加题目'}
                      </button>
                    </div>
                  </div>
                </>
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
