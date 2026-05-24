export interface ToastItem {
  id: number
  type: 'success' | 'error' | 'info'
  message: string
}

export interface MicDeviceInfo {
  deviceId: string
  label: string
}

export interface MicStatus {
  state: 'idle' | 'granted' | 'denied'
  text: string
}

export interface ChatStatus {
  ready: boolean
  text: string
}

export interface ChatMessage {
  role: 'user' | 'ai'
  text: string
  streaming?: boolean
  thinking?: boolean
}

export interface StartResponse {
  sessionId: string
  iceConfig?: {
    iceServers: Array<{ urls: string[] }>
  }
}

export interface OfferResponse {
  pc_id: string
  type?: string
  sdp: string
}

export interface IceCandidateData {
  candidate: string
  sdp_mid: string
  sdp_mline_index: number
}

export type ShowToastFn = (type: 'success' | 'error' | 'info', message: string) => void
