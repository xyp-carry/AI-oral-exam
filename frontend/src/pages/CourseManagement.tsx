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
  judge_model_ids?: string[]
  main_judger_model_id?: string | null
  tts_model_id?: string | null
  report_judger_model_id?: string | null
  enable_report_analysis?: boolean
  report_total_score?: number | null
  report_judge_rule?: string | null
  embedding_model_id?: string | null
  mineru_model_id?: string | null
  file_model_id?: string | null
  mineru_config?: { base_url?: string } | string | null
}

interface SimpleModel {
  model_id: string
  display_name: string
  provider: string
  provider_model_key: string
  base_url?: string
}

/** 知识库检索测试结果项（POST course_documents/search 响应 data.documents 元素） */
interface KbSearchResult {
  rank: number
  document_name: string
  chunk_order: number | null
  content: string
  rank_score: number | null
  semantic_score: number | null
}

interface ReportTemplateModule {
  module_key: string
  template_body: string
  template_prompt: string
  provides_questions: boolean
  sort_order: number
}

/** B 模式题目：问题/答案/维度必填，分数限定 5/10/15；preset_question_id 仅编辑已有考试时存在 */
interface BQuestion {
  preset_question_id?: string
  content: string
  answer: string
  dimension: string
  score: 5 | 10 | 15
}

/** B 模式可选分数 */
const B_QUESTION_SCORES = [5, 10, 15] as const

/**
 * 前端生成考试项 ID：时间戳 + 课程 ID 混合后做多轮 FNV-1a 风格散列，
 * 输出 32 位十六进制字符串（同步实现，不依赖 crypto.subtle 的 secure context）
 */
function generateExamItemId(courseId: string): string {
  const input = `${Date.now()}-${courseId}`
  let h1 = 0x811c9dc5, h2 = 0x01000193, h3 = 0xdeadbeef, h4 = 0x41c6ce57
  for (let i = 0; i < input.length; i++) {
    const c = input.charCodeAt(i)
    h1 = Math.imul(h1 ^ c, 16777619) >>> 0
    h2 = Math.imul(h2 + c + i, 2246822519) >>> 0
    h3 = Math.imul(h3 ^ (c << (i % 7)), 3266489917) >>> 0
    h4 = Math.imul(h4 + c, 668265263) >>> 0
  }
  const hex = (n: number) => n.toString(16).padStart(8, '0')
  return hex(h1) + hex(h2) + hex(h3) + hex(h4)
}

function formatDuration(totalSeconds: number): string {
  if (totalSeconds <= 0) return '已过期'
  const d = Math.floor(totalSeconds / 86400)
  const h = Math.floor((totalSeconds % 86400) / 3600)
  const m = Math.floor((totalSeconds % 3600) / 60)
  const s = Math.floor(totalSeconds % 60)
  const parts: string[] = []
  if (d > 0) parts.push(`${d}天`)
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
  const [clientExamId, setClientExamId] = useState<string | null>(null)
  const [examName, setExamName] = useState('')
  const [examDesc, setExamDesc] = useState('')
  const [examItemType, setExamItemType] = useState('')
  const [availD, setAvailD] = useState(30)
  const [availH, setAvailH] = useState(0)
  const [usePresetQuestions, setUsePresetQuestions] = useState(false)
  const [showDimensionModal, setShowDimensionModal] = useState(false)
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

  // 模型选择
  const [availableModels, setAvailableModels] = useState<SimpleModel[]>([])
  const [judgeModelIds, setJudgeModelIds] = useState<string[]>([])
  const [mainJudgerModelId, setMainJudgerModelId] = useState<string>('')
  // 仓库分析模型（report_judger_model_id）：仅 C 模式绑定
  const [reportJudgerModelId, setReportJudgerModelId] = useState<string>('')
  // 语音模型（tts 类型）列表与当前选中
  const [voiceModels, setVoiceModels] = useState<SimpleModel[]>([])
  const [voiceModelId, setVoiceModelId] = useState<string>('')
  // B 模式题目列表（每题必填问题/答案/维度，分数 5/10/15）：独立弹窗配置
  const [bQuestions, setBQuestions] = useState<BQuestion[]>([])
  const [showBQuestionsModal, setShowBQuestionsModal] = useState(false)
  /** 题目弹窗中当前展开编辑的题目下标 */
  const [bExpandedIdx, setBExpandedIdx] = useState<number | null>(null)

  // 报告模板
  const [reportTemplateName, setReportTemplateName] = useState('')
  const [reportTemplateModules, setReportTemplateModules] = useState<ReportTemplateModule[]>([])
  const [showReportTemplateModal, setShowReportTemplateModal] = useState(false)
  const [editingModuleIdx, setEditingModuleIdx] = useState<number | null>(null)
  const [reportTemplateSaving, setReportTemplateSaving] = useState(false)
  const [reportTemplateMsg, setReportTemplateMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  // 加载默认模板前的前一状态快照：关闭弹窗回滚，保存模板才提交本轮加载
  const [reportTemplateSnapshot, setReportTemplateSnapshot] = useState<{ name: string; modules: ReportTemplateModule[] } | null>(null)

  // 知识库（额外文档）配置：独立弹窗，挂在考试卡片入口上
  const [showKbModal, setShowKbModal] = useState(false)
  const [kbExamItem, setKbExamItem] = useState<ExamItem | null>(null)
  const [kbView, setKbView] = useState<'docs' | 'settings'>('docs')
  const [kbEmbeddingModels, setKbEmbeddingModels] = useState<SimpleModel[]>([])
  const [kbEmbeddingModelId, setKbEmbeddingModelId] = useState('')
  const [kbFileModels, setKbFileModels] = useState<SimpleModel[]>([])
  const [kbFileModelId, setKbFileModelId] = useState('')
  const [kbName, setKbName] = useState('')
  const [kbFiles, setKbFiles] = useState<File[]>([])
  const [kbList, setKbList] = useState<string[]>([])
  const [kbListLoading, setKbListLoading] = useState(false)
  const [kbUploading, setKbUploading] = useState(false)
  const [kbRegistering, setKbRegistering] = useState(false)
  const [kbMsg, setKbMsg] = useState<{ type: 'success' | 'error'; text: string } | null>(null)
  // 检索测试
  const [kbSearchQuery, setKbSearchQuery] = useState('')
  const [kbSearchTopN, setKbSearchTopN] = useState(5)
  const [kbSearching, setKbSearching] = useState(false)
  const [kbSearchResults, setKbSearchResults] = useState<KbSearchResult[]>([])

  const totalScore = useMemo(() => dimensions.reduce((sum, d) => sum + (Number(d.score) || 0), 0), [dimensions])

  /** 知识库当前操作的考试项 ID（独立弹窗，仅来自考试卡片入口） */
  const currentExamId = kbExamItem?.exam_item_id ?? null

  /** 当前选中的文件解析器（file 模型） */
  const kbSelectedFileModel = useMemo(
    () => kbFileModels.find(m => m.model_id === kbFileModelId) ?? null,
    [kbFileModels, kbFileModelId]
  )

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
    setAvailD(30)
    setAvailH(0)
    setUsePresetQuestions(false)
    setJudgeModelIds([])
    setDimensions([{ name: '', score: 0 }])
    setMainJudgerModelId('')
    setReportJudgerModelId('')
    setVoiceModelId('')
    setClientExamId(null)
    setBQuestions([])
    setShowBQuestionsModal(false)
    setBExpandedIdx(null)
    setReportTemplateName('')
    setReportTemplateModules([])
    setEditingExam(null)
  }

  const fetchAvailableModels = useCallback(async () => {
    /** 统一解析 { models: [...] } 响应为 SimpleModel 列表 */
    const toModels = (json: unknown): SimpleModel[] => {
      const data = (json as Record<string, unknown>)?.models ?? []
      return (data as Array<Record<string, unknown>>).map(m => ({
        model_id: m.model_id as string,
        display_name: (m.display_name || m.provider_model_key) as string,
        provider: m.provider as string,
        provider_model_key: m.provider_model_key as string,
      }))
    }
    try {
      // 出题/评判模型为 chat 类型，语音模型为 tts 类型，分别拉取列表
      const [chat, tts] = await Promise.all([
        fetch(`${API_BASE}/chat_model`, { credentials: 'include' })
          .then(r => (r.ok ? r.json().catch(() => null) : null)),
        fetch(`${API_BASE}/tts_model`, { credentials: 'include' })
          .then(r => (r.ok ? r.json().catch(() => null) : null)),
      ])
      if (chat) setAvailableModels(toModels(chat))
      if (tts) setVoiceModels(toModels(tts))
    } catch { /* ignore */ }
  }, [])

  /** 打开创建考试弹窗：前端生成 exam_item_id（check-id 查重，重复则重新生成），
   *  仅保存在本地；考试实体在点「创建」时随 POST /exam_items（携带 ID）一起提交 */
  const openCreateExam = async () => {
    if (!selectedCourse) return
    resetExamForm()
    fetchAvailableModels()
    setShowExamModal(true)
    /** 查重：返回 true 表示 ID 已存在（需重新生成）；接口异常返回 false 不阻塞 */
    const checkIdExists = async (courseId: string, id: string): Promise<boolean> => {
      try {
        const res = await fetch(`${API_BASE}/courses/${courseId}/exam_items/check-id`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ exam_item_id: id }),
        })
        if (!res.ok) return false
        const json = await res.json().catch(() => null)
        const data = ((json?.data ?? json) ?? {}) as { exists?: boolean }
        return data.exists === true
      } catch {
        return false
      }
    }
    try {
      let clientExamId = generateExamItemId(selectedCourse)
      for (let i = 0; i < 5 && await checkIdExists(selectedCourse, clientExamId); i++) {
        clientExamId = generateExamItemId(selectedCourse)
      }
      setClientExamId(clientExamId)
    } catch (err) {
      setError(err instanceof Error ? err.message : '生成考试ID失败')
    }
  }

  // 编辑考试时获取已有报告模板并回填
  const fetchReportTemplate = useCallback(async (examItemId: string) => {
    setReportTemplateName('')
    setReportTemplateModules([])
    try {
      const res = await fetch(`${API_BASE}/exam_items/${examItemId}/report_template`, { credentials: 'include' })
      if (!res.ok) return
      const json = await res.json().catch(() => null)
      const template = (json?.data ?? json) as
        | { template_name?: string; modules?: Array<Record<string, unknown>> }
        | null
      if (!template) return
      setReportTemplateName(template.template_name || '')
      const modules = Array.isArray(template.modules) ? template.modules : []
      setReportTemplateModules(modules.map(m => ({
        module_key: (m.module_key as string) || '',
        template_body: (m.template_body as string) || '',
        template_prompt: (m.template_prompt as string) || '',
        provides_questions: m.provides_questions === true,
        sort_order: typeof m.sort_order === 'number' ? m.sort_order : 0,
      })))
    } catch { /* ignore */ }
  }, [])

  /** 加载后端默认模板（git=仓库模板 / general=通用模板），当前无模板时使用；加载前快照，关闭可回滚 */
  const applyDefaultReportTemplate = async (templateType: 'git' | 'general') => {
    setReportTemplateSaving(true)
    try {
      const res = await fetch(`${API_BASE}/report_templates/local-default?template_type=${templateType}`, { credentials: 'include' })
      const json = await res.json().catch(() => null)
      const template = (json?.data ?? json) as
        | { template_name?: string; modules?: Array<Record<string, unknown>> }
        | null
      if (!res.ok || !template || !Array.isArray(template.modules)) {
        setReportTemplateMsg({ type: 'error', text: '获取默认模板失败' })
        setTimeout(() => setReportTemplateMsg(null), 2500)
        return
      }
      // 记录加载前的状态（多次加载只保留最初快照），关闭弹窗时回滚
      setReportTemplateSnapshot(prev => prev ?? { name: reportTemplateName, modules: reportTemplateModules })
      setReportTemplateName(template.template_name || (templateType === 'git' ? '仓库报告模板' : '通用报告模板'))
      setReportTemplateModules(template.modules.map(m => ({
        module_key: (m.module_key as string) || '',
        template_body: (m.template_body as string) || '',
        template_prompt: (m.template_prompt as string) || '',
        provides_questions: m.provides_questions === true,
        sort_order: typeof m.sort_order === 'number' ? m.sort_order : 0,
      })))
      setReportTemplateMsg({ type: 'success', text: '已载入默认模板，可继续编辑' })
      setTimeout(() => setReportTemplateMsg(null), 2500)
    } catch {
      setReportTemplateMsg({ type: 'error', text: '获取默认模板失败' })
      setTimeout(() => setReportTemplateMsg(null), 2500)
    } finally {
      setReportTemplateSaving(false)
    }
  }

  /** 用 GET 考试详细信息回填表单（字段解析不到时保留列表数据的初始回填） */
  const applyDetailToForm = (detail: Record<string, unknown> | null | undefined) => {
    if (!detail) return
    if (typeof detail.exam_item_name === 'string') setExamName(detail.exam_item_name)
    if (typeof detail.description === 'string') setExamDesc(detail.description)
    if (typeof detail.item_type === 'string') setExamItemType(detail.item_type)
    if (typeof detail.exam_available_valid_times === 'number') {
      const totalSec = detail.exam_available_valid_times
      setAvailD(Math.floor(totalSec / 86400))
      setAvailH(Math.floor((totalSec % 86400) / 3600))
    }
    if (typeof detail.use_preset_questions === 'boolean') setUsePresetQuestions(detail.use_preset_questions)
    if (Array.isArray(detail.dimension_names)) {
      const names = detail.dimension_names as string[]
      if (names.length > 0) {
        const scores = (detail.dimension_scores ?? {}) as Record<string, number>
        setDimensions(names.map(name => ({ name, score: scores[name] ?? 0 })))
      }
    }
    // Agent 配置：优先嵌套 agents 对象，其次平铺字段（embedding/MinerU 由知识库弹窗自行回填）
    const agents = (detail.agents ?? detail.judge_config ?? detail) as Record<string, unknown>
    if (Array.isArray(agents.judge_model_ids)) setJudgeModelIds(agents.judge_model_ids as string[])
    if (typeof agents.main_judger_model_id === 'string') setMainJudgerModelId(agents.main_judger_model_id)
    if (typeof agents.report_judger_model_id === 'string') setReportJudgerModelId(agents.report_judger_model_id)
    if (typeof agents.tts_model_id === 'string') setVoiceModelId(agents.tts_model_id)
  }

  /** 编辑已有考试：GET 考试详细信息回填表单（本地副本，考试保持 active，不调用状态切换接口） */
  const openEditExam = async (item: ExamItem) => {
    if (!selectedCourse) return
    setEditingExam(item)
    setClientExamId(null)
    // 先用列表数据快速填充，详情加载成功后覆盖
    setExamName(item.exam_item_name)
    setExamDesc(item.description || '')
    setExamItemType(item.item_type || '')
    const totalSec = item.exam_available_valid_times ?? 2592000
    setAvailD(Math.floor(totalSec / 86400))
    setAvailH(Math.floor((totalSec % 86400) / 3600))
    setUsePresetQuestions(item.use_preset_questions === true)
    const dims = item.dimension_names && item.dimension_names.length > 0
      ? item.dimension_names.map(name => ({ name, score: item.dimension_scores?.[name] ?? 0 }))
      : [{ name: '', score: 0 }]
    setDimensions(dims)
    setJudgeModelIds(item.judge_model_ids ?? [])
    setMainJudgerModelId(item.main_judger_model_id ?? '')
    setReportJudgerModelId(item.report_judger_model_id ?? '')
    setVoiceModelId(item.tts_model_id ?? '')
    fetchAvailableModels()
    setShowExamModal(true)
    try {
      // 加载考试详细信息并覆盖回填
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${item.exam_item_id}`, { credentials: 'include' })
      if (!res.ok) throw new Error('获取考试详细信息失败')
      const json = await res.json().catch(() => null)
      const detail = ((json?.data ?? json) ?? {}) as Record<string, unknown>
      applyDetailToForm(detail)
      // B 模式：加载已有题目列表回填
      if (String(detail.item_type ?? item.item_type ?? '') === 'B') {
        fetchBQuestions(item.exam_item_id)
      }
      // 报告模板走独立接口加载
      fetchReportTemplate(item.exam_item_id)
    } catch (err) {
      setError(err instanceof Error ? err.message : '打开编辑失败')
      setShowExamModal(false)
    }
  }

  /** 保存考试：基本信息（PUT 或 POST）→ PUT /agents 绑定模型 → 报告模板 → readiness 提示缺失配置 */
  const handleSaveExam = async () => {
    if (!examName.trim() || !selectedCourse) return
    const availSeconds = availD * 86400 + availH * 3600
    if (availSeconds <= 0) return

    // B 模式：题目列表校验（至少一题，每题问题/答案/维度必填）
    if (examItemType === 'B') {
      if (bQuestions.length === 0) {
        setError('B 模式请至少添加一道题目')
        return
      }
      if (bQuestions.some(q => !q.content.trim() || !q.answer.trim() || !q.dimension.trim())) {
        setError('B 模式每道题目必须填写问题、答案和维度')
        return
      }
    }

    // 评分维度：B 模式按题目汇总（维度分值 = 该维度题目分数之和）；其余保留原值
    let dimensionsPayload = editingExam && editingExam.dimension_names?.length
      ? editingExam.dimension_names.map(name => ({ name, score: editingExam.dimension_scores?.[name] ?? 0 }))
      : []
    if (examItemType === 'B') {
      const dimScores = new Map<string, number>()
      for (const q of bQuestions) {
        const dim = q.dimension.trim()
        dimScores.set(dim, (dimScores.get(dim) ?? 0) + q.score)
      }
      dimensionsPayload = [...dimScores.entries()].map(([name, score]) => ({ name, score }))
    }

    const body = {
      exam_item_name: examName.trim(),
      dimensions: dimensionsPayload,
      exam_available_valid_times: availSeconds,
      // 代码仓库仅 C 模式需要，随考试类型自动决定
      need_code_repository: examItemType === 'C',
      use_preset_questions: examItemType === 'B' ? true : usePresetQuestions,
      enable_report_analysis: false,
      report_total_score: null,
      report_judge_rule: null,
      description: examDesc.trim() || null,
      // 编辑时类型锁定为创建时的原值，不接受修改
      item_type: (editingExam?.item_type ?? null) || (examItemType.trim() || null),
    }

    try {
      // 1. 保存基本信息：编辑走 PUT；创建走 POST 并携带前端生成的 exam_item_id
      let examId: string | null = null
      if (editingExam) {
        examId = editingExam.exam_item_id
        const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${examId}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify(body),
        })
        if (!res.ok) throw new Error('修改考试失败')
      } else {
        const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items`, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({ ...body, exam_item_id: clientExamId ?? undefined }),
        })
        if (!res.ok) throw new Error('创建考试失败')
        const json = await res.json().catch(() => null)
        examId = json?.data?.exam_item_id ?? json?.exam_item_id ?? clientExamId
        if (!examId) throw new Error('创建考试失败：未返回考试 ID')
      }
      const base = `${API_BASE}/courses/${selectedCourse}/exam_items/${examId}`

      // 2. 绑定 Agent：评分/主评/语音一起提交；仓库分析模型仅 C 模式提交（B 模式不传保持原值）；
      //    embedding 和 MinerU 由知识库弹窗单独绑定，此处不传（保持原值）
      const agentsRes = await fetch(`${base}/agents`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          judge_model_ids: judgeModelIds.length > 0 ? judgeModelIds : null,
          main_judger_model_id: mainJudgerModelId || null,
          tts_model_id: voiceModelId || null,
          ...(examItemType === 'C' ? { report_judger_model_id: reportJudgerModelId || null } : {}),
        }),
      })
      if (!agentsRes.ok) throw new Error('模型绑定失败')

      // 3. B 模式：重建题目列表（先删除原有题目，再按当前列表逐题提交）
      if (examItemType === 'B') {
        const oldIds = bQuestions
          .map(q => q.preset_question_id)
          .filter((id): id is string => !!id)
        await Promise.all(oldIds.map(id =>
          fetch(`${base}/preset_questions/${id}`, { method: 'DELETE', credentials: 'include' })
        ))
        for (let i = 0; i < bQuestions.length; i++) {
          const q = bQuestions[i]
          const res = await fetch(`${base}/preset_questions`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
              question_dimension: q.dimension.trim(),
              question_content: q.content.trim(),
              standard_answer: q.answer.trim(),
              score: q.score,
              sort_order: i,
            }),
          })
          if (!res.ok) throw new Error(`保存第 ${i + 1} 题失败`)
        }
      }

      // 4. 创建且 C 模式下，若配置了报告模板则自动保存（编辑模式由模板弹窗独立保存）
      if (!editingExam && examItemType === 'C' && reportTemplateName.trim() && reportTemplateModules.some(m => m.module_key.trim())) {
        try {
          await fetch(`${API_BASE}/exam_items/${examId}/report_template`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            credentials: 'include',
            body: JSON.stringify({
              template_name: reportTemplateName.trim(),
              modules: reportTemplateModules
                .filter(m => m.module_key.trim())
                .map((m, i) => ({
                  module_key: m.module_key.trim(),
                  template_body: m.template_body,
                  template_prompt: m.template_prompt,
                  provides_questions: m.provides_questions,
                  sort_order: m.sort_order ?? i,
                })),
            }),
          })
        } catch {
          console.error('保存报告模板失败')
        }
      }

      // 4. 考试保持 active，读取 readiness 提示缺失配置（后端会禁止学生开始不完整的考试）
      try {
        const rr = await fetch(`${base}/readiness`, { credentials: 'include' })
        const rj = await rr.ok ? await rr.json().catch(() => null) : null
        const rd = ((rj?.data ?? rj) ?? {}) as Record<string, unknown>
        const missing = rd.missing_config ?? rd.missing ?? rd.issues
        if (Array.isArray(missing) && missing.length > 0) {
          setError(`考试已保存，但仍缺少配置：${missing.map(String).join('、')}（学生暂不能开始考试）`)
        }
      } catch { /* ignore */ }

      setShowExamModal(false)
      resetExamForm()
      fetchExamItems(selectedCourse)
    } catch (err) {
      setError(err instanceof Error ? err.message : '操作失败')
    }
  }

  /** 取消/关闭考试弹窗：本地表单副本直接丢弃，不调用任何状态接口，已有考试保持 active */
  const closeExamModal = () => {
    setShowExamModal(false)
    resetExamForm()
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

  // B 模式题目列表操作
  const addBQuestion = () => {
    setBQuestions(prev => [...prev, { content: '', answer: '', dimension: '', score: 10 }])
    setBExpandedIdx(bQuestions.length)
  }

  const removeBQuestion = (index: number) => {
    setBQuestions(prev => prev.filter((_, i) => i !== index))
    if (bExpandedIdx === index) setBExpandedIdx(null)
    else if (bExpandedIdx !== null && bExpandedIdx > index) setBExpandedIdx(bExpandedIdx - 1)
  }

  const updateBQuestion = (index: number, field: keyof BQuestion, value: string | 5 | 10 | 15) => {
    setBQuestions(prev => prev.map((q, i) => i === index ? { ...q, [field]: value } : q))
  }

  /** 编辑 B 模式考试时加载已有题目列表（preset_questions 接口）回填 */
  const fetchBQuestions = useCallback(async (examItemId: string) => {
    if (!selectedCourse) return
    try {
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${examItemId}/preset_questions`, { credentials: 'include' })
      if (!res.ok) return
      const json = await res.json().catch(() => null)
      const list = (Array.isArray(json) ? json : json?.data ?? []) as Array<Record<string, unknown>>
      setBQuestions(list.map(q => ({
        preset_question_id: (q.question_id || q.id || q.preset_question_id) as string | undefined,
        content: (q.question_content as string) || '',
        answer: (q.standard_answer as string) || '',
        dimension: (q.question_dimension as string) || '',
        score: (B_QUESTION_SCORES as readonly number[]).includes(Number(q.score)) ? (Number(q.score) as 5 | 10 | 15) : 10,
      })))
    } catch { /* ignore */ }
  }, [selectedCourse])

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

  // 知识库（额外文档）
  /** 拉取嵌入模型列表（GET /embedding_model） */
  const fetchEmbeddingModels = async () => {
    try {
      const res = await fetch(`${API_BASE}/embedding_model`, { credentials: 'include' })
      if (!res.ok) return
      const text = await res.text()
      let json: unknown
      try { json = JSON.parse(text) } catch { return }
      const data = (json as Record<string, unknown>)?.models ?? []
      setKbEmbeddingModels((data as Array<Record<string, unknown>>).map(m => ({
        model_id: m.model_id as string,
        display_name: (m.display_name || m.provider_model_key) as string,
        provider: m.provider as string,
        provider_model_key: m.provider_model_key as string,
      })))
    } catch { /* ignore */ }
  }

  /** 拉取文件模型列表（GET /file_model）作为文件解析器候选，返回列表用于回填匹配 */
  const fetchFileModels = async (): Promise<SimpleModel[]> => {
    try {
      const res = await fetch(`${API_BASE}/file_model`, { credentials: 'include' })
      if (!res.ok) return []
      const text = await res.text()
      let json: unknown
      try { json = JSON.parse(text) } catch { return [] }
      const data = (json as Record<string, unknown>)?.models ?? []
      const list = (data as Array<Record<string, unknown>>).map(m => ({
        model_id: m.model_id as string,
        display_name: (m.display_name || m.provider_model_key) as string,
        provider: m.provider as string,
        provider_model_key: m.provider_model_key as string,
        base_url: m.base_url as string | undefined,
      }))
      setKbFileModels(list)
      return list
    } catch {
      return []
    }
  }

  /** 拉取知识库文档列表（GET /courses/{course_id}/exam_items/{exam_item_id}/course_documents） */
  const fetchKbList = async (courseId: string, examItemId: string): Promise<string[]> => {
    const res = await fetch(`${API_BASE}/courses/${courseId}/exam_items/${examItemId}/course_documents`, { credentials: 'include' })
    if (!res.ok) return []
    const text = await res.text()
    let json: unknown
    try { json = JSON.parse(text) } catch { return [] }
    const obj = json as Record<string, unknown>
    const data = (obj?.data ?? obj) as Record<string, unknown>
    for (const key of ['course_document_sources', 'knowledge_document_sources', 'documents', 'document_names']) {
      const arr = data?.[key]
      if (Array.isArray(arr)) return arr.map(String)
    }
    return []
  }

  /** 打开知识库弹窗（考试卡片入口）：回填该考试已绑定的嵌入/解析模型并加载文档列表 */
  const openKbModal = async (item: ExamItem) => {
    setKbExamItem(item)
    setKbView('docs')
    setKbName('')
    setKbFiles([])
    setKbSearchQuery('')
    setKbSearchResults([])
    setKbMsg(null)
    setShowKbModal(true)
    fetchEmbeddingModels()
    // 回填已绑定模型：优先 embedding_model_id / mineru_model_id
    setKbEmbeddingModelId(item.embedding_model_id ?? '')
    const initialFileId = item.mineru_model_id ?? item.file_model_id ?? ''
    setKbFileModelId(initialFileId)
    // 兜底：未匹配到模型 ID 时按 mineru_config.base_url 匹配
    const fileModels = await fetchFileModels()
    if (!fileModels.some(m => m.model_id === initialFileId)) {
      const mineru = item.mineru_config
      const baseUrl = typeof mineru === 'string' ? mineru : mineru?.base_url
      const matched = baseUrl ? fileModels.find(m => m.base_url === baseUrl) : null
      setKbFileModelId(matched ? matched.model_id : '')
    }
    if (selectedCourse) {
      setKbListLoading(true)
      try {
        setKbList(await fetchKbList(selectedCourse, item.exam_item_id))
      } catch {
        setKbList([])
      } finally {
        setKbListLoading(false)
      }
    }
  }

  /** 需要文件解析器（如 MinerU）解析的文件扩展名 */
  const MINERU_FILE_RE = /\.(pdf|docx?|wps)$/i

  /** 设置页「保存设置」：校验后把嵌入/解析模型注册到考试项，成功后回到文档页 */
  const saveKbSettings = async () => {
    if (!kbEmbeddingModelId) {
      setKbMsg({ type: 'error', text: '请先选择嵌入模型' })
      return
    }
    setKbRegistering(true)
    setKbMsg(null)
    try {
      await registerKbModels()
      setKbMsg({ type: 'success', text: '设置已保存' })
      setTimeout(() => setKbMsg(null), 2000)
      setKbView('docs')
    } catch (err) {
      setKbMsg({ type: 'error', text: err instanceof Error ? err.message : '模型注册失败' })
    } finally {
      setKbRegistering(false)
    }
  }

  /** 检索测试：POST course_documents/search，返回最相关的 N 条 */
  const handleKbSearch = async () => {
    if (!currentExamId || !selectedCourse || !kbSearchQuery.trim()) return
    setKbSearching(true)
    setKbMsg(null)
    try {
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${currentExamId}/course_documents/search`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ query: kbSearchQuery.trim(), top_n: Math.max(1, Math.min(50, kbSearchTopN || 5)) }),
      })
      if (!res.ok) throw new Error('检索失败')
      const json = await res.json().catch(() => null)
      const data = ((json?.data ?? json) ?? {}) as Record<string, unknown>
      const docs = (Array.isArray(data.documents) ? data.documents : []) as Array<Record<string, unknown>>
      const items: KbSearchResult[] = docs.map(d => ({
        rank: typeof d.rank === 'number' ? d.rank : 0,
        document_name: (d.document_name as string) || '',
        chunk_order: typeof d.chunk_order === 'number' ? d.chunk_order : null,
        content: (d.content as string) || '',
        rank_score: typeof d.rank_score === 'number' ? d.rank_score : null,
        semantic_score: typeof d.semantic_score === 'number' ? d.semantic_score : null,
      }))
      setKbSearchResults(items)
      if (items.length === 0) setKbMsg({ type: 'error', text: '未检索到相关内容' })
    } catch (err) {
      setKbMsg({ type: 'error', text: err instanceof Error ? err.message : '检索失败' })
      setKbSearchResults([])
    } finally {
      setKbSearching(false)
    }
  }

  /** 把文档嵌入模型与文件解析器（MinerU）绑定到考试项
   *  PUT /courses/{course_id}/exam_items/{exam_item_id}/agents
   *  绑定前先 check-id：考试不存在（ID 未被占用）则不绑定；
   *  embedding_model_id 与 mineru_model_id 可单独提交（null 解绑、缺省保持原值） */
  const registerKbModels = async () => {
    if (!currentExamId || !selectedCourse) throw new Error('缺少考试项上下文')
    // 前置查重：exists=false 说明考试未创建或已被删除，跳过绑定
    const check = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/check-id`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify({ exam_item_id: currentExamId }),
    }).catch(() => null)
    if (check && check.ok) {
      const cj = await check.json().catch(() => null)
      const cd = ((cj?.data ?? cj) ?? {}) as { exists?: boolean }
      if (cd.exists !== true) throw new Error('考试项尚未创建或已被删除，请先保存考试后再绑定模型')
    }
    // check 接口本身异常时不阻塞，继续尝试绑定
    const body = {
      embedding_model_id: kbEmbeddingModelId || null,
      mineru_model_id: kbFileModelId || null,
    }
    const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${currentExamId}/agents`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      credentials: 'include',
      body: JSON.stringify(body),
    })
    if (!res.ok) throw new Error('模型注册失败，请重试')
    return await res.json().catch(() => null)
  }

  /** 上传知识库文档；包含 PDF/Word 时必须已在设置中绑定文件解析器 */
  const handleUploadKbDoc = async () => {
    if (!currentExamId || !selectedCourse || !kbName.trim() || kbFiles.length === 0) return
    if (kbFiles.some(f => MINERU_FILE_RE.test(f.name)) && !kbSelectedFileModel) {
      setKbMsg({ type: 'error', text: '所选文件包含 PDF/Word 格式，请先在「设置」中绑定文件解析器并保存后再上传' })
      setKbView('settings')
      return
    }
    setKbUploading(true)
    setKbMsg(null)
    try {
      const formData = new FormData()
      formData.append('document_name', kbName.trim())
      kbFiles.forEach(f => formData.append('files', f))
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${currentExamId}/course_documents`, {
        method: 'POST',
        credentials: 'include',
        body: formData,
      })
      if (!res.ok) throw new Error('上传失败')
      setKbName('')
      setKbFiles([])
      setKbList(await fetchKbList(selectedCourse, currentExamId))
      setKbMsg({ type: 'success', text: '上传成功' })
      setTimeout(() => setKbMsg(null), 2000)
    } catch (err) {
      setKbMsg({ type: 'error', text: err instanceof Error ? err.message : '上传失败' })
    } finally {
      setKbUploading(false)
    }
  }

  const handleDeleteKbDoc = async (name: string) => {
    if (!currentExamId) return
    if (!confirm(`确认删除知识库文档「${name}」？`)) return
    try {
      const res = await fetch(`${API_BASE}/courses/${selectedCourse}/exam_items/${currentExamId}/course_documents/${encodeURIComponent(name)}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!res.ok) throw new Error('删除失败')
      setKbList(prev => prev.filter(d => d !== name))
    } catch (err) {
      setKbMsg({ type: 'error', text: err instanceof Error ? err.message : '删除失败' })
    }
  }

  // 报告模板模块管理
  /** 校验模板表单，通过返回有效模块列表，否则提示并返回 null */
  const validateReportTemplate = (): ReportTemplateModule[] | null => {
    if (!reportTemplateName.trim()) {
      setReportTemplateMsg({ type: 'error', text: '请填写模板名称' })
      return null
    }
    const validModules = reportTemplateModules.filter(m => m.module_key.trim())
    if (validModules.length === 0) {
      setReportTemplateMsg({ type: 'error', text: '请至少添加一个模块' })
      return null
    }
    return validModules
  }

  /** 关闭模板弹窗：若存在未保存的默认模板加载（快照），回滚到加载前状态 */
  const closeReportTemplateModal = () => {
    if (reportTemplateSnapshot) {
      setReportTemplateName(reportTemplateSnapshot.name)
      setReportTemplateModules(reportTemplateSnapshot.modules)
      setReportTemplateSnapshot(null)
      setEditingModuleIdx(null)
    }
    setReportTemplateMsg(null)
    setShowReportTemplateModal(false)
  }

  /** 保存模板：编辑模式提交到后端；创建模式校验通过后关闭弹窗（随创建考试一并保存） */
  const saveReportTemplate = async () => {
    if (reportTemplateSaving) return
    const validModules = validateReportTemplate()
    if (!validModules) return
    if (!editingExam || !selectedCourse) {
      // 创建考试：模板已就绪，提交本轮状态并关闭弹窗，等待创建时提交后端
      setReportTemplateSnapshot(null)
      setShowReportTemplateModal(false)
      setReportTemplateMsg(null)
      return
    }
    setReportTemplateSaving(true)
    setReportTemplateMsg(null)
    try {
      const res = await fetch(
        `${API_BASE}/exam_items/${editingExam.exam_item_id}/report_template`,
        {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          credentials: 'include',
          body: JSON.stringify({
            template_name: reportTemplateName.trim(),
            modules: validModules.map((m, i) => ({
              module_key: m.module_key.trim(),
              template_body: m.template_body,
              template_prompt: m.template_prompt,
              provides_questions: m.provides_questions,
              sort_order: m.sort_order ?? i,
            })),
          }),
        },
      )
      if (!res.ok) {
        const data = await res.json().catch(() => ({}))
        throw new Error(data.detail || data.message || '保存失败')
      }
      // 保存成功：提交本轮状态，关闭时不再回滚
      setReportTemplateSnapshot(null)
      setReportTemplateMsg({ type: 'success', text: '模板保存成功' })
      setTimeout(() => setReportTemplateMsg(null), 2000)
    } catch (err) {
      setReportTemplateMsg({ type: 'error', text: err instanceof Error ? err.message : '保存失败' })
    } finally {
      setReportTemplateSaving(false)
    }
  }

  const addReportTemplateModule = () => {
    setReportTemplateModules(prev => [...prev, {
      module_key: '',
      template_body: '',
      template_prompt: '',
      provides_questions: false,
      sort_order: prev.length,
    }])
    setEditingModuleIdx(reportTemplateModules.length)
  }

  const removeReportTemplateModule = (index: number) => {
    setReportTemplateModules(prev => prev.filter((_, i) => i !== index))
    if (editingModuleIdx === index) setEditingModuleIdx(null)
    else if (editingModuleIdx !== null && editingModuleIdx > index) setEditingModuleIdx(editingModuleIdx - 1)
  }

  const updateReportTemplateModule = (index: number, field: keyof ReportTemplateModule, value: string | number | boolean) => {
    setReportTemplateModules(prev => prev.map((m, i) => i === index ? { ...m, [field]: value } : m))
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
                        <button className="course-exam-action-btn docs" onClick={() => openKbModal(item)} title="知识库（额外文档）">
                          <i className="fas fa-book"></i>
                        </button>
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
        <div className="course-modal-overlay">
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
        <div className="course-modal-overlay">
          <div className="course-modal exam-modal-wide" onClick={e => e.stopPropagation()}>
            <div className="course-modal-header">
              <h3>{editingExam ? '编辑考试' : '新增考试'}</h3>
              <button className="course-modal-close" onClick={closeExamModal}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="course-modal-body exam-modal-two-col">
              {/* 左列：基础配置 */}
              <div className="exam-modal-col">
                <div className="course-modal-field">
                  <label>考试名称</label>
                  <input type="text" placeholder="请输入考试名称" value={examName} onChange={e => setExamName(e.target.value)} />
                </div>
                <div className="course-modal-field">
                  <label>考试描述</label>
                  <textarea placeholder="请输入考试描述（选填）" value={examDesc} onChange={e => setExamDesc(e.target.value)} rows={2} />
                </div>
                <div className="course-modal-field">
                  <label>考试类型{editingExam ? '（创建后不可修改）' : ''}</label>
                  <div className="course-exam-toggle-row">
                    {(['A', 'B', 'C'] as const).map(t => {
                      // A 模式暂不可选；编辑已有考试时类型锁定不可更改
                      const disabled = t === 'A' || !!editingExam
                      const lockTip = !!editingExam
                        ? (examItemType === t ? '考试类型创建后不可修改' : undefined)
                        : undefined
                      return (
                        <button
                          key={t}
                          type="button"
                          className={`course-exam-toggle-btn ${examItemType === t ? 'active' : ''}`}
                          onClick={() => setExamItemType(examItemType === t ? '' : t)}
                          disabled={disabled}
                          title={t === 'A' && !editingExam ? '暂不支持该模式' : lockTip}
                        >
                          {t}
                        </button>
                      )
                    })}
                  </div>
                </div>

                {/* B 模式：题目列表（每题必填问题/答案/维度，分数 5/10/15），独立弹窗配置 */}
                {examItemType === 'B' && (
                  <div className="course-modal-field">
                    <label><i className="fas fa-list-ol"></i> 题目列表</label>
                    <button type="button" className="dimension-open-btn" onClick={() => setShowBQuestionsModal(true)}>
                      <i className="fas fa-list-ul"></i>
                      <span>已添加 {bQuestions.length} 道题目</span>
                      <i className="fas fa-chevron-right dimension-open-arrow"></i>
                    </button>
                  </div>
                )}

                {/* 报告模板：仅 C 模式需要 */}
                {examItemType === 'C' && (
                  <div className="course-modal-field">
                    <label><i className="fas fa-file-alt"></i> 报告模板</label>
                    <button type="button" className="dimension-open-btn" onClick={() => setShowReportTemplateModal(true)}>
                      <i className="fas fa-list-ul"></i>
                      <span>已配置 {reportTemplateModules.length} 个模块</span>
                      <i className="fas fa-chevron-right dimension-open-arrow"></i>
                    </button>
                  </div>
                )}

                {/* 考试有效时间：创建与编辑均需设置并随保存上传 */}
                <div className="course-modal-field">
                  <label>考试有效时间 <span className="invite-duration-preview">{formatDuration(availD * 86400 + availH * 3600)}</span></label>
                <div className="invite-duration-row">
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={365} value={availD || ''} onChange={e => setAvailD(Math.max(0, Number(e.target.value)))} placeholder="30" />
                    <span>天</span>
                  </div>
                  <div className="invite-duration-input-group">
                    <input type="number" min={0} max={23} value={availH || ''} onChange={e => setAvailH(Math.max(0, Number(e.target.value)))} placeholder="0" />
                    <span>时</span>
                  </div>
                </div>
              </div>
              </div>

              {/* 右列：Agent 模型（评分维度仅 A/B 模式需要，当前仅 C 模式故不提供配置） */}
              <div className="exam-modal-col">
              {/* Agent 模型配置 */}
              <div className="course-modal-field">
                <label><i className="fas fa-robot"></i> Agent 模型配置</label>
                {availableModels.length === 0 ? (
                  <div className="exam-model-empty">
                    <i className="fas fa-robot"></i>
                    <p>暂无可用模型，请先在「模型设置」中添加模型</p>
                  </div>
                ) : (
                  <div className="exam-model-section">
                    {/* 仓库分析模型：仅 C 模式需要（report_judger_model_id） */}
                    {examItemType === 'C' && (
                      <div className="exam-model-card">
                        <div className="exam-model-card-header">
                          <span className="exam-model-card-icon setter"><i className="fas fa-code-branch"></i></span>
                          <div className="exam-model-card-info">
                            <span className="exam-model-card-title">仓库分析模型</span>
                            <span className="exam-model-card-desc">负责分析已上传的代码仓库（仅 C 模式）</span>
                          </div>
                        </div>
                        <select value={reportJudgerModelId} onChange={e => setReportJudgerModelId(e.target.value)}>
                          <option value="">不使用</option>
                          {availableModels.map(m => (
                            <option key={m.model_id} value={m.model_id}>{m.display_name} ({m.provider})</option>
                          ))}
                        </select>
                      </div>
                    )}

                    {/* 主评判模型 */}
                    <div className="exam-model-card">
                      <div className="exam-model-card-header">
                        <span className="exam-model-card-icon judger"><i className="fas fa-gavel"></i></span>
                        <div className="exam-model-card-info">
                          <span className="exam-model-card-title">主评判模型</span>
                          <span className="exam-model-card-desc">负责综合评判与最终评分</span>
                        </div>
                      </div>
                      <select value={mainJudgerModelId} onChange={e => setMainJudgerModelId(e.target.value)}>
                        <option value="">不使用</option>
                        {availableModels.map(m => (
                          <option key={m.model_id} value={m.model_id}>{m.display_name} ({m.provider})</option>
                        ))}
                      </select>
                    </div>

                    {/* 语音模型 */}
                    <div className="exam-model-card">
                      <div className="exam-model-card-header">
                        <span className="exam-model-card-icon judger"><i className="fas fa-volume-up"></i></span>
                        <div className="exam-model-card-info">
                          <span className="exam-model-card-title">语音模型</span>
                          <span className="exam-model-card-desc">负责考试语音合成（TTS），需在「模型设置」中添加 tts 类型模型</span>
                        </div>
                      </div>
                      <select value={voiceModelId} onChange={e => setVoiceModelId(e.target.value)}>
                        <option value="">{voiceModels.length === 0 ? '暂无可用语音模型' : '不使用'}</option>
                        {voiceModels.map(m => (
                          <option key={m.model_id} value={m.model_id}>{m.display_name} ({m.provider})</option>
                        ))}
                      </select>
                    </div>

                    {/* 评判模型列表 */}
                    <div className="exam-model-card">
                      <div className="exam-model-card-header">
                        <span className="exam-model-card-icon panel"><i className="fas fa-users"></i></span>
                        <div className="exam-model-card-info">
                          <span className="exam-model-card-title">评判模型组 <span className="exam-model-card-badge">{judgeModelIds.length}</span></span>
                          <span className="exam-model-card-desc">多模型协作评分，可多选</span>
                        </div>
                      </div>
                      <div className="exam-model-judger-list">
                        {availableModels.map(m => {
                          const selected = judgeModelIds.includes(m.model_id)
                          return (
                            <label key={m.model_id} className={`exam-model-checkbox ${selected ? 'checked' : ''}`}>
                              <input
                                type="checkbox"
                                checked={selected}
                                onChange={() => {
                                  setJudgeModelIds(prev =>
                                    selected ? prev.filter(id => id !== m.model_id) : [...prev, m.model_id]
                                  )
                                }}
                              />
                              <span>{m.display_name}</span>
                              <span className="exam-model-checkbox-provider">{m.provider}</span>
                            </label>
                          )
                        })}
                      </div>
                    </div>
                  </div>
                )}
              </div>
              </div>
            </div>
            <div className="course-modal-footer">
              <button className="course-modal-btn cancel" onClick={closeExamModal}>取消</button>
              <button
                className="course-modal-btn confirm"
                onClick={handleSaveExam}
                disabled={!examName.trim() || (availD * 86400 + availH * 3600) <= 0}
              >
                {editingExam ? '保存修改' : '创建'}
              </button>
            </div>
          </div>
        </div>
      )}

      {showDimensionModal && (
        <div className="course-modal-overlay dimension-modal-overlay">
          <div className="course-modal dimension-modal" onClick={e => e.stopPropagation()}>
            <div className="course-modal-header">
              <h3><i className="fas fa-list-ul"></i> 评分维度配置 <span className="dimension-total">总分: {totalScore}</span></h3>
              <button className="course-modal-close" onClick={() => setShowDimensionModal(false)}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="course-modal-body">
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
            <div className="course-modal-footer">
              <button className="course-modal-btn confirm" onClick={() => setShowDimensionModal(false)}>
                <i className="fas fa-check"></i> 完成
              </button>
            </div>
          </div>
        </div>
      )}

      {showResetInvite && (
        <div className="course-modal-overlay">
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
        <div className="course-modal-overlay">
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
        <div className="course-modal-overlay">
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

      {/* B 模式题目列表弹窗（与报告模板同款折叠卡片形式） */}
      {showBQuestionsModal && (
        <div className="course-modal-overlay dimension-modal-overlay">
          <div className="course-modal dimension-modal report-template-modal" onClick={e => e.stopPropagation()}>
            <div className="course-modal-header">
              <h3><i className="fas fa-list-ol"></i> 题目列表配置</h3>
              <button className="course-modal-close" onClick={() => setShowBQuestionsModal(false)}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="course-modal-body">
              <div className="report-template-modules">
                <div className="report-template-modules-header">
                  <label><i className="fas fa-th-list"></i> 题目列表 <span className="report-template-modules-count">{bQuestions.length} 道</span></label>
                  <button type="button" className="dimension-add-btn" onClick={addBQuestion}>
                    <i className="fas fa-plus"></i> 添加题目
                  </button>
                </div>

                {bQuestions.length === 0 && (
                  <div className="report-template-empty">
                    <i className="fas fa-question-circle"></i>
                    <p>暂无题目，点击上方按钮添加</p>
                  </div>
                )}

                {bQuestions.map((q, idx) => {
                  const isExpanded = bExpandedIdx === idx
                  return (
                  <div key={idx} className={`report-template-module-card ${isExpanded ? 'expanded' : ''}`}>
                    <div
                      className="report-template-module-card-header"
                      onClick={() => setBExpandedIdx(isExpanded ? null : idx)}
                    >
                      <span className="report-template-module-index">#{idx + 1}</span>
                      <span className="report-template-module-title">
                        {q.content.trim() ? (q.content.trim().length > 18 ? q.content.trim().slice(0, 18) + '…' : q.content.trim()) : '新题目'}
                      </span>
                      <span className="report-template-module-badge"><i className="fas fa-star"></i> {q.score} 分</span>
                      {q.dimension.trim() && <span className="report-template-module-badge"><i className="fas fa-layer-group"></i> {q.dimension.trim()}</span>}
                      <i className={`fas fa-chevron-${isExpanded ? 'up' : 'down'} report-template-module-arrow`}></i>
                      <button
                        className="report-template-module-del"
                        onClick={(e) => { e.stopPropagation(); removeBQuestion(idx) }}
                        title="删除此题"
                      >
                        <i className="fas fa-trash-alt"></i>
                      </button>
                    </div>
                    {isExpanded && (
                    <div className="report-template-module-body">
                      <div className="bq-field">
                        <label>问题 <em className="bq-required">必填</em></label>
                        <textarea
                          rows={2}
                          placeholder="输入问题内容"
                          value={q.content}
                          onChange={e => updateBQuestion(idx, 'content', e.target.value)}
                        />
                      </div>
                      <div className="bq-field">
                        <label>答案 <em className="bq-required">必填</em></label>
                        <textarea
                          rows={2}
                          placeholder="输入标准答案"
                          value={q.answer}
                          onChange={e => updateBQuestion(idx, 'answer', e.target.value)}
                        />
                      </div>
                      <div className="bq-field-row">
                        <div className="bq-field bq-field-dim">
                          <label>维度 <em className="bq-required">必填</em></label>
                          <input
                            type="text"
                            placeholder="如：语法、流利度"
                            value={q.dimension}
                            onChange={e => updateBQuestion(idx, 'dimension', e.target.value)}
                          />
                        </div>
                        <div className="bq-field bq-field-score">
                          <label>分数</label>
                          <div className="course-exam-toggle-row">
                            {B_QUESTION_SCORES.map(s => (
                              <button
                                key={s}
                                type="button"
                                className={`course-exam-toggle-btn ${q.score === s ? 'active' : ''}`}
                                onClick={() => updateBQuestion(idx, 'score', s)}
                              >
                                {s}
                              </button>
                            ))}
                          </div>
                        </div>
                      </div>
                    </div>
                    )}
                  </div>
                  )
                })}
              </div>
            </div>
            <div className="course-modal-footer">
              <button
                className="course-modal-btn confirm"
                onClick={() => setShowBQuestionsModal(false)}
              >
                <i className="fas fa-check"></i> 完成
              </button>
            </div>
          </div>
        </div>
      )}

      {/* 报告模板弹窗 */}
      {showReportTemplateModal && (
        <div className="course-modal-overlay dimension-modal-overlay">
          <div className="course-modal dimension-modal report-template-modal" onClick={e => e.stopPropagation()}>
            <div className="course-modal-header">
              <h3><i className="fas fa-file-alt"></i> 报告模板配置</h3>
              <button className="course-modal-close" onClick={closeReportTemplateModal}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="course-modal-body">
              <div className="course-modal-field">
                <label>模板名称</label>
                <input
                  type="text"
                  placeholder="请输入报告模板名称"
                  value={reportTemplateName}
                  onChange={e => setReportTemplateName(e.target.value)}
                />
              </div>

              <div className="report-template-modules">
                <div className="report-template-modules-header">
                  <label><i className="fas fa-th-list"></i> 模块列表 <span className="report-template-modules-count">{reportTemplateModules.length} 个</span></label>
                  <button type="button" className="dimension-add-btn" onClick={addReportTemplateModule}>
                    <i className="fas fa-plus"></i> 添加模块
                  </button>
                </div>

                {reportTemplateModules.length === 0 && (
                  <div className="report-template-empty">
                    <i className="fas fa-puzzle-piece"></i>
                    <p>暂无模块，点击上方按钮添加</p>
                    <div className="report-template-default-btns">
                      <button
                        type="button"
                        className="dimension-add-btn"
                        onClick={() => applyDefaultReportTemplate('git')}
                        disabled={reportTemplateSaving}
                      >
                        <i className={`fas ${reportTemplateSaving ? 'fa-spinner fa-spin' : 'fa-code-branch'}`}></i> 仓库模板
                      </button>
                      <button
                        type="button"
                        className="dimension-add-btn"
                        onClick={() => applyDefaultReportTemplate('general')}
                        disabled={reportTemplateSaving}
                      >
                        <i className={`fas ${reportTemplateSaving ? 'fa-spinner fa-spin' : 'fa-file-alt'}`}></i> 通用模板
                      </button>
                    </div>
                  </div>
                )}

                {reportTemplateModules.map((mod, idx) => {
                  const isExpanded = editingModuleIdx === idx
                  return (
                  <div key={idx} className={`report-template-module-card ${isExpanded ? 'expanded' : ''}`}>
                    <div
                      className="report-template-module-card-header"
                      onClick={() => setEditingModuleIdx(isExpanded ? null : idx)}
                    >
                      <span className="report-template-module-index">#{idx + 1}</span>
                      <span className="report-template-module-title">{mod.module_key || '新模块'}</span>
                      {mod.provides_questions && <span className="report-template-module-badge"><i className="fas fa-question-circle"></i> 可出题</span>}
                      <i className={`fas fa-chevron-${isExpanded ? 'up' : 'down'} report-template-module-arrow`}></i>
                      <button
                        className="report-template-module-del"
                        onClick={(e) => { e.stopPropagation(); removeReportTemplateModule(idx) }}
                        title="删除模块"
                      >
                        <i className="fas fa-trash-alt"></i>
                      </button>
                    </div>
                    {isExpanded && (
                    <div className="report-template-module-body">
                      <div className="course-modal-field">
                          <label>模块标识 (key)</label>
                          <input
                            type="text"
                            placeholder="唯一标识，如 introduction"
                            value={mod.module_key}
                            onChange={e => updateReportTemplateModule(idx, 'module_key', e.target.value)}
                          />
                        </div>
                      <div className="course-modal-field">
                        <label>模板内容</label>
                        <textarea
                          placeholder="定义此模块的报告内容模板，支持使用 {{变量}} 占位符"
                          value={mod.template_body}
                          onChange={e => updateReportTemplateModule(idx, 'template_body', e.target.value)}
                          rows={3}
                        />
                      </div>
                      <div className="course-modal-field">
                        <label>模板提示词 (Prompt)</label>
                        <textarea
                          placeholder="AI 生成此模块内容时使用的提示词"
                          value={mod.template_prompt}
                          onChange={e => updateReportTemplateModule(idx, 'template_prompt', e.target.value)}
                          rows={3}
                        />
                      </div>
                      <div className="report-template-module-row">
                        <div className="course-modal-field">
                          <label>是否可出题</label>
                          <div className="course-exam-toggle-row">
                            <button
                              type="button"
                              className={`course-exam-toggle-btn ${mod.provides_questions ? 'active' : ''}`}
                              onClick={() => updateReportTemplateModule(idx, 'provides_questions', true)}
                            >
                              <i className="fas fa-check-circle"></i> 是
                            </button>
                            <button
                              type="button"
                              className={`course-exam-toggle-btn ${!mod.provides_questions ? 'active' : ''}`}
                              onClick={() => updateReportTemplateModule(idx, 'provides_questions', false)}
                            >
                              <i className="fas fa-times-circle"></i> 否
                            </button>
                          </div>
                        </div>
                        <div className="course-modal-field">
                          <label>排序</label>
                          <input
                            type="number"
                            min={0}
                            value={mod.sort_order || ''}
                            onChange={e => updateReportTemplateModule(idx, 'sort_order', Number(e.target.value))}
                            placeholder={String(idx)}
                          />
                        </div>
                      </div>
                    </div>
                    )}
                  </div>
                  )
                })}
              </div>
            </div>
            <div className="course-modal-footer">
              {reportTemplateMsg && (
                <span className={`report-template-msg ${reportTemplateMsg.type}`}>
                  <i className={`fas fa-${reportTemplateMsg.type === 'success' ? 'check-circle' : 'exclamation-circle'}`}></i>
                  {reportTemplateMsg.text}
                </span>
              )}
              <button
                className="course-modal-btn confirm"
                onClick={saveReportTemplate}
                disabled={reportTemplateSaving}
              >
                {reportTemplateSaving ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-save"></i>}
                {reportTemplateSaving ? '保存中...' : '保存模板'}
              </button>
              <button className="course-modal-btn cancel" onClick={closeReportTemplateModal}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}

      {showKbModal && kbExamItem && (
        <div className="course-modal-overlay dimension-modal-overlay">
          <div className="course-modal dimension-modal kb-modal" onClick={e => e.stopPropagation()}>
            <div className="course-modal-header">
              <h3>
                <i className="fas fa-book"></i> 知识库（额外文档） — {kbExamItem.exam_item_name}
                {kbView === 'settings' && <span className="kb-header-view-tag">设置</span>}
              </h3>
              {kbView === 'docs' && (
                <button className="kb-header-settings-btn" onClick={() => setKbView('settings')} title="知识库设置（嵌入 / 解析模型）">
                  <i className="fas fa-cog"></i> 设置
                </button>
              )}
              <button className="course-modal-close" onClick={() => setShowKbModal(false)}>
                <i className="fas fa-times"></i>
              </button>
            </div>
            <div className="course-modal-body">
              {kbView === 'settings' ? (
                <>
                  <div className="kb-step-intro">
                    配置知识库的嵌入模型与文件解析器。保存后即绑定到该考试，用于文档向量化与复杂格式解析。
                  </div>
                  <div className="kb-field-grid">
                    <div className="course-modal-field">
                      <label><i className="fas fa-cubes"></i> 嵌入模型 <em className="kb-required">必选</em></label>
                      <select value={kbEmbeddingModelId} onChange={e => setKbEmbeddingModelId(e.target.value)}>
                        <option value="">
                          {kbEmbeddingModels.length === 0 ? '暂无可用模型' : '请选择嵌入模型'}
                        </option>
                        {kbEmbeddingModels.map(m => (
                          <option key={m.model_id} value={m.model_id}>
                            {m.display_name || m.provider_model_key}{m.provider ? ` · ${m.provider}` : ''}
                          </option>
                        ))}
                      </select>
                      <div className="kb-field-hint">
                        <i className="fas fa-info-circle"></i>
                        用于将文档内容向量化，是知识库检索的基础。
                      </div>
                      {kbEmbeddingModels.length === 0 && (
                        <div className="kb-status warn">
                          <i className="fas fa-exclamation-triangle"></i>
                          <span>暂无可用嵌入模型，请先在「模型设置」中添加 model_type 为 embedding 的模型</span>
                        </div>
                      )}
                    </div>
                    <div className="course-modal-field">
                      <label><i className="fas fa-file-export"></i> 文件解析器 <em className="kb-optional">可选</em></label>
                      <select value={kbFileModelId} onChange={e => setKbFileModelId(e.target.value)}>
                        <option value="">
                          {kbFileModels.length === 0 ? '暂无可用模型' : '不使用（仅限纯文本文档）'}
                        </option>
                        {kbFileModels.map(m => (
                          <option key={m.model_id} value={m.model_id}>
                            {m.display_name || m.provider_model_key}{m.provider ? ` · ${m.provider}` : ''}
                          </option>
                        ))}
                      </select>
                      <div className="kb-field-hint">
                        <i className="fas fa-info-circle"></i>
                        用于解析 <b>PDF / Word（.doc / .docx / .wps）</b> 等复杂格式文档（如 MinerU）。
                      </div>
                      {kbFileModels.length === 0 && (
                        <div className="kb-status warn">
                          <i className="fas fa-exclamation-triangle"></i>
                          <span>暂无可用文件解析模型，请先在「模型设置」中添加 model_type 为 file 的模型</span>
                        </div>
                      )}
                    </div>
                  </div>
                </>
              ) : (
                <>
                  {/* 当前配置摘要 */}
                  <div className="kb-config-summary">
                    <span>
                      <i className="fas fa-cubes"></i> 嵌入模型：
                      {(() => {
                        const m = kbEmbeddingModels.find(x => x.model_id === kbEmbeddingModelId)
                        return m ? (m.display_name || m.provider_model_key) : (kbEmbeddingModelId || '未选择')
                      })()}
                    </span>
                    <span>
                      <i className="fas fa-file-export"></i> 文件解析器：
                      {kbSelectedFileModel
                        ? (kbSelectedFileModel.display_name || kbSelectedFileModel.provider_model_key)
                        : '未选择（不可上传 PDF / Word）'}
                    </span>
                  </div>

                  {/* 已有文档列表 */}
                  <div className="course-modal-field">
                    <label><i className="fas fa-folder-open"></i> 已有文档</label>
                    {kbListLoading ? (
                      <div className="course-loading"><i className="fas fa-spinner fa-spin"></i></div>
                    ) : kbList.length === 0 ? (
                      <div className="doc-list-empty">暂无文档</div>
                    ) : (
                      <div className="doc-list">
                        {kbList.map(d => (
                          <div key={d} className="doc-list-item">
                            <span className="doc-item-name"><i className="fas fa-file"></i> {d}</span>
                            <button className="doc-item-del" onClick={() => handleDeleteKbDoc(d)} title="删除">
                              <i className="fas fa-trash-alt"></i>
                            </button>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>

                  {/* 上传新文档 */}
                  <div className="course-modal-field">
                    <label><i className="fas fa-upload"></i> 上传新文档</label>
                    <div className="doc-upload-section">
                      <div className="course-modal-field">
                        <label>文档名称</label>
                        <input type="text" placeholder="请输入文档名称" value={kbName} onChange={e => setKbName(e.target.value)} />
                      </div>
                      <div className="course-modal-field">
                        <label>选择文件</label>
                        <div className="doc-file-drop">
                          <input
                            type="file"
                            multiple
                            onChange={e => {
                              const files = e.target.files ? Array.from(e.target.files) : []
                              setKbFiles(files)
                              // 名称输入框为空时，用首个文件名（去后缀）自动填充
                              if (!kbName.trim() && files.length > 0) {
                                setKbName(files[0].name.replace(/\.[^.]+$/, ''))
                              }
                            }}
                            id="kb-file-input"
                            className="doc-file-input"
                          />
                          <label htmlFor="kb-file-input" className="doc-file-label">
                            <i className="fas fa-cloud-upload-alt"></i>
                            <span>{kbFiles.length > 0 ? `已选 ${kbFiles.length} 个文件` : '点击选择文件'}</span>
                          </label>
                        </div>
                        {kbFiles.length > 0 && (
                          <div className="doc-file-list">
                            {kbFiles.map((f, i) => (
                              <div key={i} className={`doc-file-chip ${MINERU_FILE_RE.test(f.name) && !kbSelectedFileModel ? 'warn' : ''}`}>
                                <span>
                                  {f.name}
                                  {MINERU_FILE_RE.test(f.name) && !kbSelectedFileModel && (
                                    <em className="doc-file-chip-warn"><i className="fas fa-exclamation-triangle"></i> 需文件解析器</em>
                                  )}
                                </span>
                                <button onClick={() => setKbFiles(prev => prev.filter((_, idx) => idx !== i))}>
                                  <i className="fas fa-times"></i>
                                </button>
                              </div>
                            ))}
                          </div>
                        )}
                      </div>
                      <div className="preset-form-actions">
                        <button
                          className="course-modal-btn confirm"
                          onClick={handleUploadKbDoc}
                          disabled={kbUploading || !kbName.trim() || kbFiles.length === 0}
                        >
                          {kbUploading ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-upload"></i>}
                          {kbUploading ? '上传中...' : '上传'}
                        </button>
                      </div>
                    </div>
                  </div>

                  {/* 检索测试 */}
                  <div className="course-modal-field">
                    <label><i className="fas fa-search"></i> 检索测试</label>
                    <div className="kb-search-box">
                      <input
                        type="text"
                        placeholder="输入查询内容，测试知识库返回最相关的结果"
                        value={kbSearchQuery}
                        onChange={e => setKbSearchQuery(e.target.value)}
                        onKeyDown={e => { if (e.key === 'Enter') handleKbSearch() }}
                      />
                      <div className="kb-search-topn">
                        <span>Top</span>
                        <input
                          type="number"
                          min={1}
                          max={50}
                          value={kbSearchTopN}
                          onChange={e => setKbSearchTopN(Math.max(1, Math.min(50, Number(e.target.value) || 5)))}
                        />
                        <span>条</span>
                      </div>
                      <button
                        className="course-modal-btn confirm"
                        onClick={handleKbSearch}
                        disabled={kbSearching || !kbSearchQuery.trim()}
                      >
                        {kbSearching ? <i className="fas fa-spinner fa-spin"></i> : <i className="fas fa-search"></i>}
                        {kbSearching ? '检索中...' : '检索'}
                      </button>
                    </div>
                    {kbSearchResults.length > 0 && (
                      <div className="kb-search-results">
                        {kbSearchResults.map((r, i) => (
                          <div key={i} className="kb-search-result-item">
                            <span className="kb-search-result-rank">#{r.rank || i + 1}</span>
                            <div className="kb-search-result-main">
                              <div className="kb-search-result-meta">
                                {r.document_name && (
                                  <span className="kb-search-result-doc"><i className="fas fa-file"></i> {r.document_name}</span>
                                )}
                                {r.chunk_order != null && <span>第 {r.chunk_order} 段</span>}
                                {r.semantic_score != null && <span className="kb-search-result-score">语义 {(r.semantic_score * 100).toFixed(1)}%</span>}
                                {r.rank_score != null && <span>权重 {r.rank_score.toFixed(4)}</span>}
                              </div>
                              <div className="kb-search-result-content">{r.content}</div>
                            </div>
                          </div>
                        ))}
                      </div>
                    )}
                  </div>
                </>
              )}
            </div>
            <div className="course-modal-footer">
              {kbMsg && (
                <span className={`report-template-msg ${kbMsg.type}`}>
                  <i className={`fas fa-${kbMsg.type === 'success' ? 'check-circle' : 'exclamation-circle'}`}></i>
                  {kbMsg.text}
                </span>
              )}
              {kbView === 'settings' && (
                <>
                  <button className="course-modal-btn cancel" onClick={() => setKbView('docs')}>
                    <i className="fas fa-arrow-left"></i> 返回
                  </button>
                  <button
                    className="course-modal-btn confirm"
                    onClick={saveKbSettings}
                    disabled={kbEmbeddingModels.length === 0 || kbRegistering}
                  >
                    {kbRegistering
                      ? <><i className="fas fa-spinner fa-spin"></i> 保存中...</>
                      : <><i className="fas fa-save"></i> 保存设置</>}
                  </button>
                </>
              )}
              <button className="course-modal-btn cancel" onClick={() => setShowKbModal(false)}>
                关闭
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
