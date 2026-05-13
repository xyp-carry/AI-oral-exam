import { useState, useRef, type ChangeEvent, type DragEvent } from 'react'
import { RTC_SERVER } from '../config'

interface UploadFile {
  id: string
  raw: File
  name: string
  size: number
  type: string
  progress: number
  status: 'pending' | 'uploading' | 'success' | 'error'
  errorMsg?: string
}

const UPLOAD_URL = `${RTC_SERVER}/file/get_chunks`

function formatSize(bytes: number) {
  if (bytes < 1024) return bytes + ' B'
  if (bytes < 1024 * 1024) return (bytes / 1024).toFixed(1) + ' KB'
  return (bytes / (1024 * 1024)).toFixed(1) + ' MB'
}

function uploadFile(file: UploadFile, onProgress: (p: number) => void): Promise<{ status: 'success' | 'error'; errorMsg?: string }> {
  return new Promise(resolve => {
    const xhr = new XMLHttpRequest()
    const formData = new FormData()
    formData.append('files', file.raw)

    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable) onProgress(Math.round((e.loaded / e.total) * 100))
    }

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        resolve({ status: 'success' })
      } else {
        resolve({ status: 'error', errorMsg: `HTTP ${xhr.status}` })
      }
    }

    xhr.onerror = () => resolve({ status: 'error', errorMsg: '网络错误' })
    xhr.ontimeout = () => resolve({ status: 'error', errorMsg: '请求超时' })

    xhr.open('POST', UPLOAD_URL)
    xhr.withCredentials = true
    xhr.send(formData)
  })
}

export default function DataUpload() {
  const [files, setFiles] = useState<UploadFile[]>([])
  const [dragActive, setDragActive] = useState(false)
  const fileInputRef = useRef<HTMLInputElement>(null)

  const addFiles = (fileList: FileList) => {
    const newFiles: UploadFile[] = Array.from(fileList).map(f => ({
      id: Date.now().toString() + Math.random().toString(36).slice(2),
      raw: f,
      name: f.name,
      size: f.size,
      type: f.type || 'unknown',
      progress: 0,
      status: 'pending' as const,
    }))
    setFiles(prev => [...prev, ...newFiles])
  }

  const handleFileChange = (e: ChangeEvent<HTMLInputElement>) => {
    if (e.target.files) addFiles(e.target.files)
    e.target.value = ''
  }

  const handleDragOver = (e: DragEvent) => { e.preventDefault(); setDragActive(true) }
  const handleDragLeave = (e: DragEvent) => { e.preventDefault(); setDragActive(false) }
  const handleDrop = (e: DragEvent) => { e.preventDefault(); setDragActive(false); if (e.dataTransfer.files) addFiles(e.dataTransfer.files) }

  const removeFile = (id: string) => setFiles(prev => prev.filter(f => f.id !== id))

  const updateFile = (id: string, patch: Partial<UploadFile>) => {
    setFiles(prev => prev.map(f => f.id === id ? { ...f, ...patch } : f))
  }

  const handleUpload = async (fileId: string) => {
    const file = files.find(f => f.id === fileId)
    if (!file || file.status === 'uploading') return

    updateFile(fileId, { status: 'uploading', progress: 0, errorMsg: undefined })
    const result = await uploadFile(file, p => updateFile(fileId, { progress: p }))
    updateFile(fileId, { status: result.status, progress: result.status === 'success' ? 100 : file.progress, errorMsg: result.errorMsg })
  }

  const handleUploadAll = () => {
    files.filter(f => f.status === 'pending' || f.status === 'error').forEach(f => handleUpload(f.id))
  }

  const pendingCount = files.filter(f => f.status === 'pending' || f.status === 'error').length

  return (
    <div className="upload-page">
      <div className="upload-dropzone-wrapper">
        <div
          className={`upload-dropzone ${dragActive ? 'drag-active' : ''}`}
          onDragOver={handleDragOver}
          onDragLeave={handleDragLeave}
          onDrop={handleDrop}
          onClick={() => fileInputRef.current?.click()}
        >
          <input ref={fileInputRef} type="file" multiple onChange={handleFileChange} style={{ display: 'none' }} />
          <div className="upload-dropzone-content">
            <i className="fas fa-cloud-upload-alt"></i>
            <p className="upload-dropzone-title">拖拽文件到此处，或点击选择文件</p>
            <p className="upload-dropzone-hint">支持 CSV、JSON、WAV、MP3、PDF 等格式，单文件最大 100MB</p>
          </div>
        </div>

        {files.length > 0 && (
          <div className="upload-file-list">
            <div className="upload-file-list-header">
              <span>已选择 {files.length} 个文件</span>
              <div className="upload-file-actions">
                {pendingCount > 0 && (
                  <button className="upload-action-btn" onClick={handleUploadAll}>
                    <i className="fas fa-upload"></i> 全部上传
                  </button>
                )}
                <button className="upload-action-btn danger" onClick={() => setFiles([])}>
                  <i className="fas fa-trash"></i> 清空
                </button>
              </div>
            </div>

            {files.map(f => (
              <div key={f.id} className={`upload-file-item ${f.status}`}>
                <div className="upload-file-icon">
                  <i className={`fas ${f.type.includes('audio') ? 'fa-file-audio' : f.type.includes('json') ? 'fa-file-code' : f.type.includes('pdf') ? 'fa-file-pdf' : 'fa-file'}`}></i>
                </div>
                <div className="upload-file-info">
                  <span className="upload-file-name">{f.name}</span>
                  <span className="upload-file-size">{formatSize(f.size)}</span>
                </div>
                <div className="upload-file-progress">
                  {f.status === 'uploading' && (
                    <div className="upload-progress-bar">
                      <div className="upload-progress-fill" style={{ width: `${f.progress}%` }}></div>
                    </div>
                  )}
                  {f.status === 'success' && <i className="fas fa-check-circle success-icon"></i>}
                  {f.status === 'error' && <span className="error-text">{f.errorMsg || '上传失败'}</span>}
                </div>
                {f.status === 'error' && (
                  <button className="upload-file-retry" onClick={() => handleUpload(f.id)}>
                    <i className="fas fa-redo"></i>
                  </button>
                )}
                <button className="upload-file-remove" onClick={() => removeFile(f.id)}>
                  <i className="fas fa-times"></i>
                </button>
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
