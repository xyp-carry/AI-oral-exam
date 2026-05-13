import { createContext, useContext, useState, useCallback, type ReactNode } from 'react'

interface ExamSessionState {
  active: boolean
  setExamActive: (v: boolean) => void
}

const ExamSessionContext = createContext<ExamSessionState | null>(null)

export function ExamSessionProvider({ children }: { children: ReactNode }) {
  const [active, setActive] = useState(false)

  const setExamActive = useCallback((v: boolean) => {
    setActive(v)
  }, [])

  return (
    <ExamSessionContext.Provider value={{ active, setExamActive }}>
      {children}
    </ExamSessionContext.Provider>
  )
}

export function useExamSession() {
  const ctx = useContext(ExamSessionContext)
  if (!ctx) throw new Error('useExamSession must be used within ExamSessionProvider')
  return ctx
}
