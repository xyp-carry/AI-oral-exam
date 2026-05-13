import { useState, useRef, useCallback } from 'react'
import type { ShowToastFn, ChatStatus, StartResponse, OfferResponse, IceCandidateData } from '../types'
import { RTC_SERVER, ICE_GATHER_TIMEOUT } from '../config'

type DCMessageHandler = (data: Record<string, unknown>) => void

const VOLUME_THRESHOLD = 0.005

function computeRms(samples: Float32Array): number {
  let sum = 0
  for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i]
  return Math.sqrt(sum / samples.length)
}

function disconnectAndNullify<T extends { disconnect(): void }>(ref: React.MutableRefObject<T | null>) {
  ref.current?.disconnect()
  ref.current = null
}

export function useWebRTC(showToast: ShowToastFn) {
  const pcRef = useRef<RTCPeerConnection | null>(null)
  const dcRef = useRef<RTCDataChannel | null>(null)
  const [sessionId, setSessionId] = useState<string | null>(null)
  const [chatStatus, setChatStatus] = useState<ChatStatus>({ ready: false, text: '未连接' })
  const [starting, setStarting] = useState(false)
  const dcHandlersRef = useRef<Record<string, DCMessageHandler>>({})

  const monitorCtxRef = useRef<AudioContext | null>(null)
  const monitorSourceRef = useRef<MediaStreamAudioSourceNode | null>(null)
  const monitorProcessorRef = useRef<ScriptProcessorNode | null>(null)
  const monitorSilentGainRef = useRef<GainNode | null>(null)
  const monitorTotalSamplesRef = useRef(0)

  const onDCMessage = useCallback((type: string, handler: DCMessageHandler) => {
    dcHandlersRef.current[type] = handler
  }, [])

  const stopMonitor = useCallback(() => {
    if (monitorProcessorRef.current) monitorProcessorRef.current.onaudioprocess = null
    disconnectAndNullify(monitorProcessorRef)
    disconnectAndNullify(monitorSilentGainRef)
    disconnectAndNullify(monitorSourceRef)
    monitorCtxRef.current?.close().catch(() => {})
    monitorCtxRef.current = null
    monitorTotalSamplesRef.current = 0
  }, [])

  const startMonitor = useCallback(async (stream: MediaStream) => {
    stopMonitor()

    const ctx = new AudioContext()
    if (ctx.state === 'suspended') await ctx.resume()
    if (ctx.state !== 'running') {
      console.error('[Monitor] AudioContext 启动失败, state:', ctx.state)
      ctx.close().catch(() => {})
      return
    }

    const source = ctx.createMediaStreamSource(stream)
    const processor = ctx.createScriptProcessor(2048, 1, 1)
    const silentGain = ctx.createGain()
    silentGain.gain.value = 0

    monitorCtxRef.current = ctx
    monitorSourceRef.current = source
    monitorProcessorRef.current = processor
    monitorSilentGainRef.current = silentGain
    monitorTotalSamplesRef.current = 0

    source.connect(processor)
    processor.connect(silentGain)
    silentGain.connect(ctx.destination)

    processor.onaudioprocess = (e) => {
      const pcm = e.inputBuffer.getChannelData(0)
      const rms = computeRms(pcm)
      monitorTotalSamplesRef.current += pcm.length

      if (rms < VOLUME_THRESHOLD) return

      const total = monitorTotalSamplesRef.current
      const peak = pcm.reduce((max, v) => Math.max(max, Math.abs(v)), 0)

      console.log(
        `[Monitor] samples: ${pcm.length} | total: ${total} (${((total * 4) / 1024).toFixed(1)}KB)` +
        ` | duration: ${(total / ctx.sampleRate).toFixed(2)}s` +
        ` | rate: ${ctx.sampleRate}Hz | rms: ${rms.toFixed(4)} | peak: ${peak.toFixed(4)}`
      )
    }

    console.log('[Monitor] started, sampleRate:', ctx.sampleRate, 'Hz')
  }, [stopMonitor])

  const cleanupConnection = useCallback(() => {
    stopMonitor()

    const dc = dcRef.current
    if (dc) {
      dc.onopen = dc.onclose = dc.onerror = dc.onmessage = null
      if (dc.readyState === 'open') dc.close()
      dcRef.current = null
    }

    const pc = pcRef.current
    if (pc) {
      pc.ontrack = pc.oniceconnectionstatechange = pc.onconnectionstatechange = pc.onicecandidate = null
      pc.close()
      pcRef.current = null
    }

    const audioEl = document.getElementById('remoteAudio') as HTMLAudioElement | null
    if (audioEl) audioEl.srcObject = null
  }, [stopMonitor])

  const replaceAudioTrack = useCallback((newTrack: MediaStreamTrack) => {
    const sender = pcRef.current?.getSenders().find(s => s.track?.kind === 'audio')
    if (!sender) return console.warn('[ReplaceTrack] no audio sender')
    sender.replaceTrack(newTrack).then(
      () => console.log('[ReplaceTrack] replaced, label:', newTrack.label),
      err => console.error('[ReplaceTrack] failed:', err)
    )
  }, [])

  const startSession = useCallback(async (micStream: MediaStream | null) => {
    setStarting(true)
    cleanupConnection()

    try {
      const startRes = await fetch(`${RTC_SERVER}/start`, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ createDailyRoom: false, enableDefaultIceServers: true, transport: 'webrtc' }),
      })
      if (!startRes.ok) throw new Error(`/start failed: ${startRes.status}`)

      const { sessionId: sid, iceConfig } = (await startRes.json()) as StartResponse
      if (!sid) throw new Error('missing sessionId')
      setSessionId(sid)
      showToast('success', '会话已创建: ' + sid)

      const pc = new RTCPeerConnection({
        iceServers: (iceConfig?.iceServers ?? [{ urls: ['stun:stun.l.google.com:19302'] }]).map(s => ({ urls: s.urls })),
      })
      pcRef.current = pc

      pc.ontrack = (event) => {
        if (event.track.kind !== 'audio') return
        const audioEl = document.getElementById('remoteAudio') as HTMLAudioElement | null
        if (!audioEl) return
        const stream = event.streams[0] ?? new MediaStream([event.track])
        audioEl.srcObject = stream
        audioEl.play().catch(() => {})
        startMonitor(stream).catch(e => console.error('[Monitor] failed:', e))
      }

      pc.oniceconnectionstatechange = () => {
        const s = pc.iceConnectionState
        if (s === 'connected' || s === 'completed') {
          setChatStatus({ ready: true, text: 'P2P 已连接' })
          showToast('success', 'P2P 连接已建立')
        } else if (s === 'disconnected' || s === 'failed') {
          setChatStatus({ ready: false, text: s === 'failed' ? 'P2P 失败' : 'P2P 断开' })
          if (s === 'failed') showToast('error', 'P2P 连接失败')
          stopMonitor()
        }
      }

      pc.onconnectionstatechange = () => console.log('[PC] state:', pc.connectionState)

      const audioTrack = micStream?.getAudioTracks()[0]
      if (audioTrack) {
        pc.addTrack(audioTrack, micStream!)
      } else {
        pc.addTransceiver('audio', { direction: 'sendrecv' })
      }
      pc.addTransceiver('video', { direction: 'recvonly' })

      const dc = pc.createDataChannel('messages')
      dcRef.current = dc

      dc.onopen = () => console.log('[DC] open, label:', dc.label)
      dc.onclose = () => console.log('[DC] closed')
      dc.onerror = (ev) => console.error('[DC] error:', ev)
      dc.onmessage = (event) => {
        const ts = new Date().toISOString()
        let data: Record<string, unknown>
        try { data = JSON.parse(event.data as string) } catch { return console.log(`[DC][${ts}] non-JSON:`, event.data) }

        console.log(`[DC][${ts}] ← type: "${data.type}"`, data)

        if (data.type === 'user-llm-text') {
          const text = (data.data as Record<string, unknown>)?.text
          if (text) console.log(`[DC][${ts}] ← User: "${text}"`)
        } else if (data.type === 'bot-llm-text') {
          const text = (data.data as Record<string, unknown>)?.text
          if (text) console.log(`[DC][${ts}] ← AI: "${text}"`)
        }

        const handler = dcHandlersRef.current[data.type as string]
        handler ? handler(data) : console.warn(`[DC][${ts}] unhandled type: "${data.type}"`)
      }

      const candidates: RTCIceCandidate[] = []
      let resolveGathering: (() => void) | null = null
      let candidateTimer: ReturnType<typeof setTimeout> | null = null

      const gatheringDone = new Promise<void>(resolve => { resolveGathering = resolve })

      const finishGathering = () => {
        if (candidateTimer) { clearTimeout(candidateTimer); candidateTimer = null }
        resolveGathering?.()
        resolveGathering = null
        pc.onicecandidate = null
      }

      pc.onicecandidate = (event) => {
        if (candidateTimer) clearTimeout(candidateTimer)
        if (event.candidate) {
          candidates.push(event.candidate)
          candidateTimer = setTimeout(finishGathering, ICE_GATHER_TIMEOUT)
        } else {
          finishGathering()
        }
      }

      const offer = await pc.createOffer()
      await pc.setLocalDescription(offer)
      await gatheringDone

      const offerUrl = `${RTC_SERVER}/sessions/${sid}/api/offer`
      const offerRes = await fetch(offerUrl, {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ sdp: pc.localDescription!.sdp, type: 'offer', pc_id: null, restart_pc: false }),
      })
      if (!offerRes.ok) throw new Error('/offer failed')

      const offerData: OfferResponse = await offerRes.json()

      if (offerData.sdp) {
        await pc.setRemoteDescription(new RTCSessionDescription({
          type: (offerData.type as RTCSdpType) || 'answer',
          sdp: offerData.sdp,
        }))
      }

      const icePayload: IceCandidateData[] = candidates.map(c => ({
        candidate: c.candidate,
        sdp_mid: c.sdpMid!,
        sdp_mline_index: c.sdpMLineIndex!,
      }))

      await fetch(offerUrl, {
        method: 'PATCH',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'include',
        body: JSON.stringify({ candidates: icePayload, pc_id: offerData.pc_id }),
      })

      setChatStatus({ ready: true, text: '已连接' })
      showToast('success', `会话建立完成，已发送 ${icePayload.length} 条 ICE`)
    } catch (err) {
      const msg = err instanceof Error ? err.message : String(err)
      console.error('[Session] failed:', msg)
      showToast('error', '启动失败: ' + msg)
    } finally {
      setStarting(false)
    }
  }, [showToast, startMonitor, stopMonitor, cleanupConnection])

  const closeSession = useCallback(async () => {
    const sid = sessionId
    cleanupConnection()
    setSessionId(null)
    setChatStatus({ ready: false, text: '未连接' })

    if (sid) {
      try {
        await fetch(`${RTC_SERVER}/close`, {
          method: 'POST',
          credentials: 'include',
        })
        showToast('success', '会话已关闭: ' + sid)
      } catch (err) {
        const msg = err instanceof Error ? err.message : String(err)
        console.warn('[CloseSession] server close failed:', msg)
        showToast('info', '连接已断开')
      }
    }
  }, [sessionId, cleanupConnection, showToast])

  const sendDCMessage = useCallback((data: Record<string, unknown>) => {
    const dc = dcRef.current
    if (dc?.readyState === 'open') {
      dc.send(JSON.stringify(data))
    } else {
      console.warn('[DC] send failed, state:', dc?.readyState)
    }
  }, [])

  return { sessionId, chatStatus, starting, startSession, closeSession, sendDCMessage, onDCMessage, replaceAudioTrack, cleanupConnection }
}
