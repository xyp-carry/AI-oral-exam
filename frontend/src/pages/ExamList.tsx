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

export default function ExamList() {
  const { user } = useAuth()
  const navigate = useNavigate()
  const [courses, setCourses] = useState<Course[]>([])
  const [examMap, setExamMap] = useState<Record<string, ExamItem[]>>({})
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [expandedCourse, setExpandedCourse] = useState<string | null>(null)

  const fetchData = useCallback(async () => {
    setLoading(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/courses`, { credentials: 'include' })
      if (!res.ok) throw new Error(`请求失败: ${res.status}`)
      const json = await res.json()
      const courseList: Course[] = Array.isArray(json) ? json : json.data ?? []
      setCourses(courseList)

      const map: Record<string, ExamItem[]> = {}
      await Promise.all(
        courseList.map(async (c) => {
          try {
            const r = await fetch(`${API_BASE}/courses/${c.course_id}/exam_items`, { credentials: 'include' })
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

  const handleEnterExam = (examItemId: string) => {
    navigate(`/exam/${examItemId}`)
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
                        {items.map(item => (
                          <div
                            key={item.exam_item_id}
                            className="examlist-card"
                            onClick={() => handleEnterExam(item.exam_item_id)}
                          >
                            <div className="examlist-card-header">
                              <h4>{item.exam_item_name}</h4>
                              <span className="examlist-meta-score">总分 {item.total_score}</span>
                            </div>
                            {item.description && (
                              <p className="examlist-card-desc">{item.description}</p>
                            )}
                            <div className="examlist-card-meta">
                              <span><i className="fas fa-layer-group"></i> {item.dimension_names?.length ?? 0} 维度</span>
                              {item.item_type && <span><i className="fas fa-tag"></i> {item.item_type}</span>}
                              <span><i className="fas fa-users"></i> {item.participant_count} 人</span>
                              <span><i className="fas fa-calendar-alt"></i> {item.created_at?.slice(0, 10)}</span>
                            </div>
                            {item.dimension_names && item.dimension_names.length > 0 && (
                              <div className="examlist-card-dimensions">
                                {item.dimension_names.map((name, i) => (
                                  <span key={i} className="examlist-card-dim-tag">{name}: {item.dimension_scores?.[name] ?? 0}分</span>
                                ))}
                              </div>
                            )}
                            <button className="examlist-enter-btn">
                              <i className="fas fa-arrow-right"></i> 进入考试
                            </button>
                          </div>
                        ))}
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
    </div>
  )
}
