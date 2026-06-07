import { useState, useEffect, useCallback, useMemo } from 'react'
import {
  ReactFlow,
  Background,
  Controls,
  useNodesState,
  useEdgesState,
  type Node,
  type Edge,
  type NodeProps,
  Handle,
  Position,
  MarkerType,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { API_BASE } from '../config'

interface Course {
  course_id: string
  course_name: string
  description: string | null
  status: string
  created_at: string
}

interface ExamItem {
  exam_item_id: string
  exam_item_name: string
  description: string | null
  item_type: string | null
  total_score: number
  participant_count: number
  status: string
  created_at: string
  updated_at: string
  dimension_names: string[]
  dimension_scores: Record<string, number>
  exam_available_valid_times: number
  need_code_repository: boolean
}

interface ExamRecord {
  exam_id: string
  exam_item_id?: string
  exam_item_name?: string
  total_score: number
  dimension_count?: number
  question_count?: number
  dimension_scores?: Record<string, number>
  repository_url?: string | null
  exam_completed?: boolean
  ended_at: string | null
  created_at: string
  [key: string]: unknown
}

interface Question {
  record_index: number
  question_id: string
  question_content: string
  question_dimension: string
  question_score: number
  based_on_record_index: string
  source_detail: string
  student_answer: string
  correctness_level: string
  evaluation: string
  standard_answer: string
  is_preset_question: boolean
  created_at: string
}

type QuestionNodeData = Node & {
  data: {
    question: Question
    isActive: boolean
    onToggle: (id: string) => void
  }
}

function getCorrectnessColor(level: string) {
  switch (level) {
    case 'excellent': return '#22d3ee'
    case 'correct': return '#00e5a0'
    case 'average': return '#f59e0b'
    case 'wrong': return '#ef4444'
    case 'absurd': return '#a855f7'
    default: return '#64748b'
  }
}

function getCorrectnessLabel(level: string) {
  switch (level) {
    case 'excellent': return '优秀'
    case 'correct': return '正确'
    case 'average': return '一般'
    case 'wrong': return '错误'
    case 'absurd': return '离谱'
    default: return '未知'
  }
}

function QuestionNode({ data }: NodeProps<QuestionNodeData>) {
  const { question: q, isActive, onToggle } = data
  return (
    <div
      className={`qfnode ${q.is_preset_question ? 'qfnode-preset' : 'qfnode-ai'} ${isActive ? 'qfnode-active' : ''}`}
    >
      <Handle type="target" position={Position.Top} className="qfnode-handle" />
      <div className="qfnode-card" onClick={() => onToggle(q.question_id)}>
        <div className="qfnode-head">
          <span className="qfnode-index">#{q.record_index}</span>
          <span className="qfnode-badge">{q.is_preset_question ? '预设' : 'AI'}</span>
          <span className="qfnode-correctness" style={{ color: getCorrectnessColor(q.correctness_level) }}>
            {getCorrectnessLabel(q.correctness_level)}
          </span>
          <span className="qfnode-score">{q.question_score}分</span>
          <span className="qfnode-expand">
            <i className={`fas fa-chevron-${isActive ? 'up' : 'down'}`}></i>
          </span>
        </div>
        <div className="qfnode-content">
          {q.question_content.length > 80
            ? q.question_content.slice(0, 80) + '...'
            : q.question_content}
        </div>
      </div>
      {isActive && (
        <div className="qfnode-detail">
          <div className="qfnode-detail-row">
            <div className="qfnode-detail-label">题目</div>
            <div className="qfnode-detail-text">{q.question_content}</div>
          </div>
          <div className="qfnode-detail-row">
            <div className="qfnode-detail-label">学生回答</div>
            <div className="qfnode-detail-text">{q.student_answer}</div>
          </div>
          <div className="qfnode-detail-row">
            <div className="qfnode-detail-label">
              评价
              <span style={{ color: getCorrectnessColor(q.correctness_level), marginLeft: 8, fontSize: 12, fontWeight: 600 }}>
                {getCorrectnessLabel(q.correctness_level)}
              </span>
              <span style={{ marginLeft: 8, fontSize: 12, color: '#64748b' }}>{q.question_score}分</span>
            </div>
            <div className="qfnode-detail-text">{q.evaluation}</div>
          </div>
          <div className="qfnode-detail-row">
            <div className="qfnode-detail-label">标准答案</div>
            <div className="qfnode-detail-text">{q.standard_answer}</div>
          </div>
          {q.source_detail && (
            <div className="qfnode-detail-row">
              <div className="qfnode-detail-label">出题依据</div>
              <div className="qfnode-detail-text">{q.source_detail}</div>
            </div>
          )}
        </div>
      )}
      <Handle type="source" position={Position.Bottom} className="qfnode-handle" />
    </div>
  )
}

const nodeTypes = { questionNode: QuestionNode }

// 按 question_dimension 分组，每个维度内构建树并自动布局
function buildFlowData(questions: Question[]) {
  const dimGroups = questions.reduce<Record<string, Question[]>>((acc, q) => {
    const dim = q.question_dimension || '未分类'
    if (!acc[dim]) acc[dim] = []
    acc[dim].push(q)
    return acc
  }, {})

  const nodes: Node[] = []
  const edges: Edge[] = []
  const NODE_WIDTH = 280
  const NODE_HEIGHT = 120
  const H_GAP = 60
  const V_GAP = 200
  const DIM_GAP = 400

  let dimIdx = 0
  for (const [, dimQuestions] of Object.entries(dimGroups)) {
    const baseY = dimIdx * DIM_GAP

    // 构建父子关系
    const nodeChildren = new Map<string, Question[]>()
    dimQuestions.forEach(q => {
      if (q.based_on_record_index !== '-1') {
        const parentIdx = parseInt(q.based_on_record_index, 10)
        const parent = dimQuestions.find(p => p.record_index === parentIdx)
        if (parent) {
          if (!nodeChildren.has(parent.question_id)) nodeChildren.set(parent.question_id, [])
          nodeChildren.get(parent.question_id)!.push(q)
        }
      }
    })

    // 计算每个子树的宽度
    const subtreeWidth = new Map<string, number>()
    const qMap = new Map(dimQuestions.map(q => [q.question_id, q]))

    const calcWidth = (qid: string): number => {
      if (subtreeWidth.has(qid)) return subtreeWidth.get(qid)!
      const children = nodeChildren.get(qid) || []
      if (children.length === 0) {
        subtreeWidth.set(qid, NODE_WIDTH)
        return NODE_WIDTH
      }
      let total = 0
      children.forEach((c, i) => {
        total += calcWidth(c.question_id)
        if (i > 0) total += H_GAP
      })
      const w = Math.max(NODE_WIDTH, total)
      subtreeWidth.set(qid, w)
      return w
    }

    // 分配位置
    const assignPos = (qid: string, cx: number, y: number) => {
      const q = qMap.get(qid)!
      nodes.push({
        id: qid,
        type: 'questionNode',
        position: { x: cx - NODE_WIDTH / 2, y },
        data: { question: q },
      })
      const children = nodeChildren.get(qid) || []
      if (children.length === 0) return
      let totalW = 0
      children.forEach((c, i) => {
        totalW += subtreeWidth.get(c.question_id)!
        if (i > 0) totalW += H_GAP
      })
      let startX = cx - totalW / 2
      children.forEach(c => {
        const cw = subtreeWidth.get(c.question_id)!
        const childCx = startX + cw / 2
        edges.push({
          id: `${qid}->${c.question_id}`,
          source: qid,
          target: c.question_id,
          type: 'smoothstep',
          animated: true,
          markerEnd: { type: MarkerType.ArrowClosed, color: '#00e5a0' },
          style: { stroke: '#1e2d4a', strokeWidth: 2 },
        })
        assignPos(c.question_id, childCx, y + V_GAP)
        startX += cw + H_GAP
      })
    }

    // 找根节点
    const roots = dimQuestions.filter(q => q.based_on_record_index === '-1')
    // 计算总宽度
    let totalRootWidth = 0
    roots.forEach((r, i) => {
      totalRootWidth += calcWidth(r.question_id)
      if (i > 0) totalRootWidth += H_GAP * 2
    })

    let rootStartX = -totalRootWidth / 2
    roots.forEach(r => {
      const rw = subtreeWidth.get(r.question_id)!
      const cx = rootStartX + rw / 2
      assignPos(r.question_id, cx, baseY)
      rootStartX += rw + H_GAP * 2
    })

    // 孤立节点
    const visited = new Set(nodes.map(n => n.id))
    let orphanX = -totalRootWidth / 2
    dimQuestions.forEach(q => {
      if (!visited.has(q.question_id)) {
        nodes.push({
          id: q.question_id,
          type: 'questionNode',
          position: { x: orphanX, y: baseY },
          data: { question: q },
        })
        orphanX += NODE_WIDTH + H_GAP
      }
    })

    dimIdx++
  }

  return { nodes, edges }
}

function QuestionFlowModal({
  questions,
  record,
  onClose,
}: {
  questions: Question[]
  record: ExamRecord
  onClose: () => void
}) {
  const [activeId, setActiveId] = useState<string | null>(null)

  const { nodes: initNodes, edges: initEdges } = useMemo(() => buildFlowData(questions), [questions])

  const [nodes, setNodes, onNodesChange] = useNodesState(initNodes)
  const [edges, , onEdgesChange] = useEdgesState(initEdges)

  // 注入交互回调到 node data
  const enrichedNodes = useMemo(() => {
    return nodes.map(n => ({
      ...n,
      data: {
        ...n.data,
        isActive: activeId === n.id,
        onToggle: (id: string) => setActiveId(prev => prev === id ? null : id),
      },
    }))
  }, [nodes, activeId])

  return (
    <div className="qflow-overlay" onClick={onClose}>
      <div className="qflow-modal" onClick={e => e.stopPropagation()}>
        <div className="qflow-header">
          <div className="qflow-header-info">
            <h3>答题流程详情</h3>
            <span className="qflow-header-meta">
              {record.exam_item_name && `${record.exam_item_name} · `}
              总分 {record.total_score} · {questions.length} 题
            </span>
          </div>
          <button className="qflow-close-btn" onClick={onClose}>
            <i className="fas fa-times"></i>
          </button>
        </div>
        <div className="qflow-flow-container">
          {questions.length === 0 ? (
            <div className="history-empty">
              <i className="fas fa-clipboard"></i>
              <p>暂无题目数据</p>
            </div>
          ) : (
            <ReactFlow
              nodes={enrichedNodes}
              edges={edges}
              onNodesChange={onNodesChange}
              onEdgesChange={onEdgesChange}
              nodeTypes={nodeTypes}
              fitView
              fitViewOptions={{ padding: 0.3 }}
              minZoom={0.2}
              maxZoom={1.5}
              proOptions={{ hideAttribution: true }}
            >
              <Background color="#1e2d4a" gap={20} size={1} />
              <Controls position="bottom-right" />
            </ReactFlow>
          )}
        </div>
      </div>
    </div>
  )
}

export default function ExamHistory() {
  const [courses, setCourses] = useState<Course[]>([])
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState('')
  const [selectedCourse, setSelectedCourse] = useState<string | null>(null)
  const [examItems, setExamItems] = useState<ExamItem[]>([])
  const [selectedExamItem, setSelectedExamItem] = useState<string | null>(null)
  const [records, setRecords] = useState<ExamRecord[]>([])
  const [recordsLoading, setRecordsLoading] = useState(false)
  const [recordsError, setRecordsError] = useState('')
  const [selectedRecord, setSelectedRecord] = useState<ExamRecord | null>(null)
  const [questions, setQuestions] = useState<Question[]>([])
  const [questionsLoading, setQuestionsLoading] = useState(false)

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

  useEffect(() => {
    fetchCourses()
  }, [fetchCourses])

  const fetchExamItems = async (courseId: string) => {
    try {
      const res = await fetch(`${API_BASE}/courses/${courseId}/exam_items`, { credentials: 'include' })
      if (!res.ok) throw new Error('获取考试项失败')
      const json = await res.json()
      setExamItems(Array.isArray(json) ? json : json.data ?? [])
    } catch {
      setExamItems([])
    }
  }

  const fetchRecords = async (courseId: string, examItemId?: string | null) => {
    setRecordsLoading(true)
    setRecordsError('')
    setRecords([])
    try {
      const url = examItemId
        ? `${API_BASE}/exam_history/${courseId}?exam_item_id=${examItemId}`
        : `${API_BASE}/exam_history/${courseId}`
      const res = await fetch(url, { credentials: 'include' })
      if (!res.ok) throw new Error(`请求失败: ${res.status}`)
      const json = await res.json()
      setRecords(Array.isArray(json) ? json : json.data ?? [])
    } catch (err) {
      setRecordsError(err instanceof Error ? err.message : '获取考试记录失败')
    } finally {
      setRecordsLoading(false)
    }
  }

  const handleSelectCourse = async (courseId: string) => {
    if (selectedCourse === courseId) {
      setSelectedCourse(null)
      setRecords([])
      setExamItems([])
      setSelectedExamItem(null)
      return
    }
    setSelectedCourse(courseId)
    setSelectedExamItem(null)
    setRecords([])
    await fetchExamItems(courseId)
    fetchRecords(courseId)
  }

  const handleSelectExamItem = (examItemId: string | null) => {
    if (!selectedCourse) return
    setSelectedExamItem(examItemId)
    setSelectedRecord(null)
    setQuestions([])
    fetchRecords(selectedCourse, examItemId)
  }

  const handleSelectRecord = async (record: ExamRecord) => {
    setSelectedRecord(record)
    setQuestions([])
    setQuestionsLoading(true)
    try {
      const examItemId = record.exam_item_id || selectedExamItem
      if (!examItemId) return
      const res = await fetch(`${API_BASE}/exam_items/${examItemId}/questions`, { credentials: 'include' })
      if (res.ok) {
        const json = await res.json()
        const recs = Array.isArray(json) ? json : json.data ?? []
        const match = recs.find((r: { exam_id?: string }) => r.exam_id === record.exam_id)
        if (match?.questions) {
          setQuestions(match.questions)
        } else if (recs.length > 0 && recs[0].questions) {
          setQuestions(recs[0].questions)
        }
      }
    } catch {
      setQuestions([])
    } finally {
      setQuestionsLoading(false)
    }
  }

  const handleCloseModal = () => {
    setSelectedRecord(null)
    setQuestions([])
  }

  const formatTime = (t: string | null | undefined) => {
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

  const avgScore = records.length > 0
    ? (records.reduce((a, b) => a + (b.total_score ?? 0), 0) / records.length).toFixed(1)
    : '0'

  return (
    <div className="history-page">
      <div className="history-filters">
        <h2 className="history-title"><i className="fas fa-history"></i> 考试历史管理</h2>
        <button className="history-refresh-btn" onClick={fetchCourses} disabled={loading}>
          <i className={`fas ${loading ? 'fa-spinner fa-spin' : 'fa-sync-alt'}`}></i>
          刷新
        </button>
      </div>

      {error && (
        <div className="history-error">
          <i className="fas fa-exclamation-circle"></i>
          <span>{error}</span>
          <button onClick={fetchCourses}>重试</button>
        </div>
      )}

      {loading && (
        <div className="history-loading">
          <i className="fas fa-spinner fa-spin"></i>
          <span>加载课程列表...</span>
        </div>
      )}

      {!loading && !error && (
        <div className="history-course-list">
          {courses.length === 0 ? (
            <div className="history-empty">
              <i className="fas fa-folder-open"></i>
              <p>暂无课程数据</p>
            </div>
          ) : courses.map(course => (
            <div key={course.course_id} className={`history-course-card ${selectedCourse === course.course_id ? 'expanded' : ''}`}>
              <div className="history-course-header" onClick={() => handleSelectCourse(course.course_id)}>
                <div className="history-course-info">
                  <span className="history-course-name"><i className="fas fa-book"></i> {course.course_name}</span>
                  {course.description && <span className="history-course-desc">{course.description}</span>}
                </div>
                <div className="history-course-right">
                  <span className="history-course-date">{course.created_at?.slice(0, 10)}</span>
                  <i className={`fas ${selectedCourse === course.course_id ? 'fa-chevron-up' : 'fa-chevron-down'} history-expand-icon`}></i>
                </div>
              </div>

              {selectedCourse === course.course_id && (
                <div className="history-records-section">
                  {examItems.length > 0 && (
                    <div className="history-exam-filter-bar">
                      <button
                        className={`history-exam-filter-btn ${selectedExamItem === null ? 'active' : ''}`}
                        onClick={() => handleSelectExamItem(null)}
                      >
                        全部
                      </button>
                      {examItems.map(item => (
                        <button
                          key={item.exam_item_id}
                          className={`history-exam-filter-btn ${selectedExamItem === item.exam_item_id ? 'active' : ''}`}
                          onClick={() => handleSelectExamItem(item.exam_item_id)}
                        >
                          {item.exam_item_name}
                        </button>
                      ))}
                    </div>
                  )}

                  {recordsLoading && (
                    <div className="history-loading"><i className="fas fa-spinner fa-spin"></i> 加载考试记录...</div>
                  )}
                  {recordsError && (
                    <div className="history-error">
                      <i className="fas fa-exclamation-circle"></i>
                      <span>{recordsError}</span>
                      <button onClick={() => fetchRecords(course.course_id, selectedExamItem)}>重试</button>
                    </div>
                  )}
                  {!recordsLoading && !recordsError && records.length === 0 && (
                    <div className="history-empty"><i className="fas fa-clipboard"></i><p>暂无考试记录</p></div>
                  )}
                  {!recordsLoading && records.length > 0 && (
                    <>
                      <div className="history-summary">
                        <div className="history-summary-item">
                          <span className="history-summary-value">{records.length}</span>
                          <span className="history-summary-label">考试记录</span>
                        </div>
                        <div className="history-summary-item">
                          <span className="history-summary-value">{avgScore}</span>
                          <span className="history-summary-label">平均分</span>
                        </div>
                      </div>
                      <div className="history-table-wrapper">
                        <table className="history-table">
                          <thead>
                            <tr>
                              {records[0]?.exam_item_name !== undefined && <th>考试名称</th>}
                              <th>总分</th>
                              <th>开始时间</th>
                              <th>结束时间</th>
                              {records[0]?.question_count !== undefined && <th>题目数</th>}
                            </tr>
                          </thead>
                          <tbody>
                            {records.map((r, idx) => (
                              <tr
                                key={r.exam_id || idx}
                                onClick={() => handleSelectRecord(r)}
                                className={selectedRecord?.exam_id === r.exam_id ? 'selected' : ''}
                                style={{ cursor: 'pointer' }}
                              >
                                {r.exam_item_name !== undefined && <td>{r.exam_item_name}</td>}
                                <td className={`score ${(r.total_score ?? 0) >= 60 ? 'pass' : 'fail'}`}>
                                  {r.total_score ?? '-'}
                                </td>
                                <td className="time">{formatTime(r.created_at)}</td>
                                <td className="time">{formatTime(r.ended_at)}</td>
                                {r.question_count !== undefined && <td>{r.question_count}</td>}
                              </tr>
                            ))}
                          </tbody>
                        </table>
                      </div>
                    </>
                  )}
                </div>
              )}
            </div>
          ))}
        </div>
      )}

      {/* 题目流程弹窗 */}
      {selectedRecord && questionsLoading && (
        <div className="qflow-overlay" onClick={handleCloseModal}>
          <div className="qflow-modal" onClick={e => e.stopPropagation()}>
            <div className="qflow-header">
              <h3>加载中...</h3>
              <button className="qflow-close-btn" onClick={handleCloseModal}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="history-loading" style={{ padding: '60px' }}>
              <i className="fas fa-spinner fa-spin"></i>
              <span>加载题目数据...</span>
            </div>
          </div>
        </div>
      )}
      {selectedRecord && !questionsLoading && questions.length > 0 && (
        <QuestionFlowModal
          questions={questions}
          record={selectedRecord}
          onClose={handleCloseModal}
        />
      )}
    </div>
  )
}
