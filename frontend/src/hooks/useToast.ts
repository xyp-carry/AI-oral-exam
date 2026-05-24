import { useState, useCallback } from 'react'
import type { ToastItem, ShowToastFn } from '../types'

export function useToast(): { toasts: ToastItem[]; showToast: ShowToastFn } {
  const [toasts, setToasts] = useState<ToastItem[]>([])

  const showToast: ShowToastFn = useCallback((type, message) => {
    const id = Date.now() + Math.random()
    setToasts(prev => [...prev, { id, type, message }])
    setTimeout(() => {
      setToasts(prev => prev.filter(t => t.id !== id))
    }, 3000)
  }, [])

  return { toasts, showToast }
}
