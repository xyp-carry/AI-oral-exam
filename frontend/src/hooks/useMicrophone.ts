import { useState, useRef, useCallback } from 'react'
import type { ShowToastFn, MicStatus } from '../types'

export function useMicrophone(showToast: ShowToastFn) {
  const [micStream, setMicStream] = useState<MediaStream | null>(null)
  const [micReady, setMicReady] = useState(false)
  const [micDevices, setMicDevices] = useState<MediaDeviceInfo[]>([])
  const [selectedDeviceId, setSelectedDeviceId] = useState('')
  const [micStatus, setMicStatus] = useState<MicStatus>({ state: 'idle', text: '麦克风：未选择' })
  const [micLevel, setMicLevel] = useState(0)
  const analyserRef = useRef<AnalyserNode | null>(null)
  const audioCtxRef = useRef<AudioContext | null>(null)
  const animFrameRef = useRef<number>(0)
  const permissionGrantedRef = useRef(false)
  const onTrackChangeRef = useRef<((track: MediaStreamTrack) => void) | null>(null)

  const enumerateDevices = useCallback(async () => {
    try {
      if (!navigator.mediaDevices?.enumerateDevices) return

      if (!permissionGrantedRef.current) {
        try {
          const tempStream = await navigator.mediaDevices.getUserMedia({ audio: true })
          tempStream.getTracks().forEach(t => t.stop())
          permissionGrantedRef.current = true
        } catch {
          const devices = await navigator.mediaDevices.enumerateDevices()
          setMicDevices(devices.filter(d => d.kind === 'audioinput'))
          return
        }
      }

      const devices = await navigator.mediaDevices.enumerateDevices()
      setMicDevices(devices.filter(d => d.kind === 'audioinput'))
    } catch (err) {
      console.error('设备枚举失败:', err)
    }
  }, [])

  const stopLevelMonitor = useCallback(() => {
    if (animFrameRef.current) {
      cancelAnimationFrame(animFrameRef.current)
      animFrameRef.current = 0
    }
    if (audioCtxRef.current) {
      audioCtxRef.current.close().catch(() => {})
      audioCtxRef.current = null
    }
    analyserRef.current = null
  }, [])

  const startLevelMonitor = useCallback((stream: MediaStream) => {
    stopLevelMonitor()
    try {
      const audioCtx = new AudioContext()
      audioCtxRef.current = audioCtx
      const source = audioCtx.createMediaStreamSource(stream)
      const analyser = audioCtx.createAnalyser()
      analyser.fftSize = 256
      analyser.smoothingTimeConstant = 0.8
      source.connect(analyser)
      analyserRef.current = analyser

      const dataArray = new Uint8Array(analyser.frequencyBinCount)
      function updateLevel() {
        analyser.getByteFrequencyData(dataArray)
        let sum = 0
        for (let i = 0; i < dataArray.length; i++) sum += dataArray[i]
        setMicLevel(Math.min(100, (sum / dataArray.length / 128) * 100))
        animFrameRef.current = requestAnimationFrame(updateLevel)
      }
      updateLevel()
    } catch (e) {
      console.warn('音频分析器创建失败:', e)
    }
  }, [stopLevelMonitor])

  const selectDevice = useCallback(async (deviceId: string) => {
    setSelectedDeviceId(deviceId)

    if (!deviceId) {
      if (micStream) micStream.getTracks().forEach(t => t.stop())
      stopLevelMonitor()
      setMicStream(null)
      setMicReady(false)
      setMicStatus({ state: 'idle', text: '麦克风：未选择' })
      setMicLevel(0)
      return
    }

    setMicStatus({ state: 'idle', text: '麦克风：连接中...' })

    try {
      if (micStream) {
        micStream.getTracks().forEach(t => t.stop())
        stopLevelMonitor()
      }

      const stream = await navigator.mediaDevices.getUserMedia({
        audio: { deviceId: { exact: deviceId } }
      })
      stream.getAudioTracks().forEach(t => { t.enabled = false })

      permissionGrantedRef.current = true

      const track = stream.getAudioTracks()[0]
      setMicStream(stream)
      setMicReady(true)
      setMicStatus({ state: 'granted', text: '麦克风：' + (track.label || '麦克风') })
      startLevelMonitor(stream)
      showToast('success', '麦克风已连接：' + (track.label || '麦克风'))

      if (onTrackChangeRef.current && track) {
        onTrackChangeRef.current(track)
      }

      const devices = await navigator.mediaDevices.enumerateDevices()
      setMicDevices(devices.filter(d => d.kind === 'audioinput'))
    } catch (err: unknown) {
      const error = err as DOMException
      setMicStream(null)
      setMicReady(false)
      if (error.name === 'NotAllowedError' || error.name === 'PermissionDeniedError') {
        setMicStatus({ state: 'denied', text: '麦克风：权限被拒绝' })
        showToast('error', '麦克风权限被拒绝')
      } else if (error.name === 'NotFoundError') {
        setMicStatus({ state: 'denied', text: '麦克风：设备未找到' })
        showToast('error', '未找到所选麦克风设备')
      } else {
        setMicStatus({ state: 'denied', text: '麦克风：获取失败' })
        showToast('error', '麦克风获取失败: ' + error.message)
      }
    }
  }, [micStream, showToast, startLevelMonitor, stopLevelMonitor])

  ;(selectDevice as ((deviceId: string) => void) & { __enumerate?: () => void }).__enumerate = enumerateDevices

  const setMicEnabled = useCallback((enabled: boolean) => {
    micStream?.getAudioTracks().forEach(t => { t.enabled = enabled })
  }, [micStream])

  const setOnTrackChange = useCallback((handler: ((track: MediaStreamTrack) => void) | null) => {
    onTrackChangeRef.current = handler
  }, [])

  return { micStream, micReady, micDevices, selectedDeviceId, micStatus, micLevel, enumerateDevices, selectDevice, setMicEnabled, setOnTrackChange }
}
