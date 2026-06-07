import { useState, useEffect, useCallback, useMemo } from 'react'
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

interface Dimension {
  name: string
  score: number
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
  const [dimensions, setDimensions] = useState<Dimension[]>([{ name: '', score: 0 }])
  const [deleteConfirmId, setDeleteConfirmId] = useState<string | null>(null)
  const [deleting, setDeleting] = useState(false)

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

    const body = {
      exam_item_name: examName.trim(),
      dimensions: validDimensions,
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
                onClick={() => setSelectedCourse(c.course_id)}
              >
                <div className="course-list-item-name">{c.course_name}</div>
                <div className="course-list-item-meta">
                  <span><i className="fas fa-calendar-alt"></i> {c.created_at?.slice(0, 10)}</span>
                </div>
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

                {!examsLoading && examItems.map(item => {
                  return (
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
                          <span><i className="fas fa-calendar-alt"></i> {item.created_at?.slice(0, 10)}</span>
                        </div>
                        {item.dimension_names && item.dimension_names.length > 0 && (
                          <div className="course-exam-dimensions">
                            {item.dimension_names.map((name, i) => (
                              <span key={i} className="course-exam-dim-tag">{name}: {item.dimension_scores?.[name] ?? 0}分</span>
                            ))}
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
                  )
                })}
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
                disabled={!examName.trim() || dimensions.every(d => !d.name.trim() || d.score <= 0)}
              >
                {editingExam ? '保存修改' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
