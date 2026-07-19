import { useState, useEffect, useCallback } from 'react'
import { API_BASE } from '../config'
import { useAuth } from '../hooks/useAuth'

interface ParamDef {
  type: string
  default: unknown
  min?: number
  max?: number
  enum?: string[]
}

interface ModelDef {
  label: string
  model_name: string
  params_schema: Record<string, ParamDef>
}

interface ProviderDef {
  label: string
  base_url: string
  models: Record<string, ModelDef>
}

interface SavedModel {
  model_id: string
  provider: string
  provider_model_key: string
  model_name: string
  base_url: string
  model_api_key?: string
  display_name: string
  params: Record<string, unknown>
  last_test_result?: { success: boolean; duration_ms: number; response_preview: string }
  created_at?: string
}

export default function ModelSettings() {
  const { user } = useAuth()
  const [providers, setProviders] = useState<Record<string, ProviderDef>>({})
  const [savedModels, setSavedModels] = useState<SavedModel[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  // 表单状态
  const [providerKey, setProviderKey] = useState('')
  const [modelKey, setModelKey] = useState('')
  const [modelApiKey, setModelApiKey] = useState('')
  const [displayName, setDisplayName] = useState('')
  const [paramValues, setParamValues] = useState<Record<string, unknown>>({})

  // 操作状态
  const [testing, setTesting] = useState(false)
  const [testResult, setTestResult] = useState<{ success: boolean; message: string } | null>(null)
  const [saving, setSaving] = useState(false)

  // 当前选中供应商的详情
  const selectedProvider = providers[providerKey]
  // 当前选中模型的详情
  const selectedModel = selectedProvider?.models?.[modelKey]

  const fetchProviders = useCallback(async () => {
    try {
      const res = await fetch(`${API_BASE}/model_providers`, { credentials: 'include' })
      if (!res.ok) throw new Error(`获取供应商失败 (HTTP ${res.status})`)
      const text = await res.text()
      let json: unknown
      try { json = JSON.parse(text) } catch { throw new Error('供应商接口返回非JSON数据') }
      const data = (json as Record<string, unknown>)?.providers ?? {}
      setProviders(data as Record<string, ProviderDef>)
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取供应商失败')
    }
  }, [])

  const fetchSavedModels = useCallback(async () => {
    setLoading(true)
    try {
      const res = await fetch(`${API_BASE}/models`, { credentials: 'include' })
      if (!res.ok) throw new Error(`获取模型列表失败 (HTTP ${res.status})`)
      const text = await res.text()
      let json: unknown
      try { json = JSON.parse(text) } catch { throw new Error('模型接口返回非JSON数据') }
      const data = (json as Record<string, unknown>)?.models ?? []
      setSavedModels(data as SavedModel[])
    } catch (err) {
      setError(err instanceof Error ? err.message : '获取模型列表失败')
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => {
    fetchProviders()
    fetchSavedModels()
  }, [fetchProviders, fetchSavedModels])

  // 切换供应商：清空模型选择，用 params_schema 默认值初始化参数
  const handleProviderChange = (val: string) => {
    setProviderKey(val)
    setModelKey('')
    setParamValues({})
    setTestResult(null)
  }

  // 切换模型：用该模型 params_schema 的 default 值初始化参数
  const handleModelChange = (val: string) => {
    setModelKey(val)
    const model = providers[providerKey]?.models?.[val]
    if (model) {
      const defaults: Record<string, unknown> = {}
      for (const [k, def] of Object.entries(model.params_schema)) {
        defaults[k] = def.default
      }
      setParamValues(defaults)
    } else {
      setParamValues({})
    }
    setTestResult(null)
  }

  const resetForm = () => {
    setProviderKey('')
    setModelKey('')
    setModelApiKey('')
    setDisplayName('')
    setParamValues({})
    setTestResult(null)
  }

  const handleParamChange = (key: string, value: unknown) => {
    setParamValues(prev => ({ ...prev, [key]: value }))
  }

  const buildParams = (): Record<string, unknown> => {
    const params: Record<string, unknown> = {}
    for (const [k, v] of Object.entries(paramValues)) {
      if (v != null && v !== '') params[k] = v
    }
    return params
  }

  const handleTest = async () => {
    if (!providerKey || !modelKey) {
      setTestResult({ success: false, message: '请选择供应商和模型' })
      return
    }
    setTesting(true)
    setTestResult(null)
    try {
      const res = await fetch(`${API_BASE}/models/test`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          provider: providerKey,
          provider_model_key: selectedModel!.model_name,
          model_api_key: modelApiKey || undefined,
          params: buildParams(),
        }),
      })
      const text = await res.text()
      let data: Record<string, unknown>
      try { data = JSON.parse(text) } catch { throw new Error('接口返回非JSON数据') }
      if (res.ok) {
        setTestResult({ success: true, message: (data.message as string) || '连接测试成功' })
      } else {
        setTestResult({ success: false, message: (data.detail as string) || (data.message as string) || '连接测试失败' })
      }
    } catch (err) {
      setTestResult({ success: false, message: err instanceof Error ? err.message : '测试异常' })
    } finally {
      setTesting(false)
    }
  }

  const handleSave = async () => {
    if (!providerKey || !modelKey) {
      setError('请选择供应商和模型')
      return
    }
    setSaving(true)
    setError('')
    try {
      const res = await fetch(`${API_BASE}/models`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({
          provider: providerKey,
          provider_model_key: selectedModel!.model_name,
          model_api_key: modelApiKey || undefined,
          display_name: displayName || selectedModel!.label,
          params: buildParams(),
        }),
      })
      if (!res.ok) {
        const text = await res.text()
        let data: Record<string, unknown>
        try { data = JSON.parse(text) } catch { throw new Error('保存失败') }
        throw new Error((data.detail as string) || (data.message as string) || '保存失败')
      }
      resetForm()
      fetchSavedModels()
    } catch (err) {
      setError(err instanceof Error ? err.message : '保存失败')
    } finally {
      setSaving(false)
    }
  }

  const handleDelete = async (modelId: string) => {
    if (!confirm('确认删除该模型配置？')) return
    try {
      const res = await fetch(`${API_BASE}/models/${modelId}`, {
        method: 'DELETE',
        credentials: 'include',
      })
      if (!res.ok) {
        const text = await res.text()
        let data: Record<string, unknown>
        try { data = JSON.parse(text) } catch { throw new Error('删除失败') }
        throw new Error((data.detail as string) || (data.message as string) || '删除失败')
      }
      fetchSavedModels()
    } catch (err) {
      setError(err instanceof Error ? err.message : '删除失败')
    }
  }

  if (user?.role !== 'teacher') {
    return <div className="model-settings-page"><p>仅教师可访问此页面</p></div>
  }

  const providerOptions = Object.entries(providers)
  const modelOptions = selectedProvider ? Object.entries(selectedProvider.models) : []

  // 不需要渲染的参数
  const SKIP_PARAMS = new Set(['max_tokens', 'reasoning_effort'])

  // 渲染动态参数字段
  const renderParamFields = () => {
    if (!selectedModel) return null
    return (
      <>
        {Object.entries(selectedModel.params_schema)
          .filter(([key]) => !SKIP_PARAMS.has(key))
          .map(([key, def]) => {
          const value = paramValues[key] ?? def.default
          if (def.type === 'number') {
            const min = def.min ?? 0
            const max = def.max ?? 2
            const isTemperature = key === 'temperature'
            const label = isTemperature ? `温度: ${Number(value).toFixed(2)}` : `${key} (${def.type}): ${Number(value)}`
            return (
              <div className="model-form-group" key={key}>
                <label>{label}</label>
                <input
                  type="range"
                  min={min}
                  max={max}
                  step={isTemperature ? 0.01 : (max - min <= 1 ? 0.1 : 1)}
                  value={Number(value)}
                  onChange={e => handleParamChange(key, parseFloat(e.target.value))}
                />
                <div className="model-temp-labels">
                  <span>{min}</span>
                  <span>{max}</span>
                </div>
              </div>
            )
          }
          if (def.type === 'integer') {
            return (
              <div className="model-form-group" key={key}>
                <label>{key} ({def.type})</label>
                <input
                  type="number"
                  min={def.min ?? 1}
                  step={1}
                  value={Number(value)}
                  onChange={e => handleParamChange(key, parseInt(e.target.value, 10) || 1)}
                />
              </div>
            )
          }
          if (def.enum) {
            return (
              <div className="model-form-group" key={key}>
                <label>{key} ({def.type})</label>
                <select value={String(value)} onChange={e => handleParamChange(key, e.target.value)}>
                  {def.enum.map(v => (
                    <option key={v} value={v}>{v}</option>
                  ))}
                </select>
              </div>
            )
          }
          if (def.type === 'object') {
            // thinking 等 object 类型 — 用默认值，不渲染
            return null
          }
          return (
            <div className="model-form-group" key={key}>
              <label>{key} ({def.type})</label>
              <input
                type="text"
                value={String(value)}
                onChange={e => handleParamChange(key, e.target.value)}
              />
            </div>
          )
        })}
      </>
    )
  }

  return (
    <div className="model-settings-page">
      {error && (
        <div className="model-settings-error">
          <i className="fas fa-exclamation-circle"></i>
          <span>{error}</span>
          <button onClick={() => setError('')}><i className="fas fa-times"></i></button>
        </div>
      )}

      <div className="model-settings-grid">
        {/* 左侧：表单 */}
        <div className="model-settings-form-card">
          <h3><i className="fas fa-plus-circle"></i> 添加模型</h3>

          <div className="model-form-group">
            <label>模型供应商</label>
            <select value={providerKey} onChange={e => handleProviderChange(e.target.value)}>
              <option value="">选择供应商</option>
              {providerOptions.map(([k, v]) => (
                <option key={k} value={k}>{v.label}</option>
              ))}
            </select>
          </div>

          {selectedProvider && (
            <div className="model-form-group">
              <label>Base URL</label>
              <input
                type="text"
                value={selectedProvider.base_url}
                readOnly
                className="model-input-readonly"
              />
            </div>
          )}

          <div className="model-form-group">
            <label>模型</label>
            <select value={modelKey} onChange={e => handleModelChange(e.target.value)} disabled={!providerKey}>
              <option value="">选择模型</option>
              {modelOptions.map(([k, v]) => (
                <option key={k} value={k}>{v.label}</option>
              ))}
            </select>
          </div>

          {renderParamFields()}

          <div className="model-form-group">
            <label>API Key</label>
            <input
              type="password"
              value={modelApiKey}
              onChange={e => setModelApiKey(e.target.value)}
              placeholder="sk-..."
            />
          </div>

          <div className="model-form-group">
            <label>显示名称（可选）</label>
            <input
              type="text"
              value={displayName}
              onChange={e => setDisplayName(e.target.value)}
              placeholder={selectedModel?.label || '自定义名称'}
            />
          </div>

          {testResult && (
            <div className={`model-test-result ${testResult.success ? 'success' : 'fail'}`}>
              <i className={`fas ${testResult.success ? 'fa-check-circle' : 'fa-times-circle'}`}></i>
              <span>{testResult.message}</span>
            </div>
          )}

          <div className="model-form-actions">
            <button
              className="model-btn test"
              onClick={handleTest}
              disabled={testing || !providerKey || !modelKey}
            >
              <i className={`fas ${testing ? 'fa-spinner fa-spin' : 'fa-plug'}`}></i>
              {testing ? '测试中...' : '测试连接'}
            </button>
            <button
              className="model-btn save"
              onClick={handleSave}
              disabled={saving || !providerKey || !modelKey}
            >
              <i className={`fas ${saving ? 'fa-spinner fa-spin' : 'fa-save'}`}></i>
              {saving ? '保存中...' : '保存'}
            </button>
          </div>
        </div>

        {/* 右侧：已保存模型列表 */}
        <div className="model-settings-list-card">
          <h3><i className="fas fa-server"></i> 已配置模型</h3>
          {loading ? (
            <div className="model-list-loading">
              <i className="fas fa-spinner fa-spin"></i> 加载中...
            </div>
          ) : savedModels.length === 0 ? (
            <div className="model-list-empty">
              <i className="fas fa-inbox"></i>
              <p>暂无模型配置</p>
            </div>
          ) : (
            <div className="model-list">
              {savedModels.map(m => {
                const provLabel = providers[m.provider]?.label || m.provider
                let modelLabel = m.display_name || m.provider_model_key
                const testOk = m.last_test_result?.success
                return (
                  <div key={m.model_id} className="model-list-item">
                    <div className="model-list-item-header">
                      <span className="model-list-name">{modelLabel}</span>
                      <div className="model-list-header-right">
                        {testOk !== undefined && (
                          <span className={`model-test-badge ${testOk ? 'ok' : 'fail'}`} title={testOk ? '测试通过' : '测试失败'}>
                            <i className={`fas ${testOk ? 'fa-check-circle' : 'fa-times-circle'}`}></i>
                          </span>
                        )}
                        <span className="model-list-provider">{provLabel}</span>
                      </div>
                    </div>
                    <div className="model-list-item-detail">
                      <span><i className="fas fa-code"></i> {m.provider_model_key}</span>
                      {m.params?.temperature != null && (
                        <span><i className="fas fa-thermometer-half"></i> 温度 {Number(m.params.temperature)}</span>
                      )}
                      <span className="model-list-url" title={m.base_url}><i className="fas fa-link"></i> {m.base_url}</span>
                    </div>
                    <div className="model-list-item-actions">
                      <button className="model-btn-sm delete" onClick={() => handleDelete(m.model_id)} title="删除">
                        <i className="fas fa-trash"></i>
                      </button>
                    </div>
                  </div>
                )
              })}
            </div>
          )}
        </div>
      </div>
    </div>
  )
}
