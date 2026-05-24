import { useEffect } from 'react'
import { Routes, Route, Navigate, useNavigate } from 'react-router-dom'
import Layout from './components/Layout'
import Login from './pages/Login'
import Dashboard from './pages/Dashboard'
import Exam from './pages/Exam'
import ExamList from './pages/ExamList'
import DataUpload from './pages/DataUpload'
import ExamHistory from './pages/ExamHistory'
import CourseManagement from './pages/CourseManagement'
import { AuthProvider, useAuth } from './hooks/useAuth'
import { ExamSessionProvider } from './hooks/useExamSession'

function RequireAuth({ children }: { children: React.ReactNode }) {
  const { user } = useAuth()
  if (!user) return <Navigate to="/login" replace />
  return <>{children}</>
}

function LoginGuard({ children }: { children: React.ReactNode }) {
  const { user, fetchMe } = useAuth()
  const navigate = useNavigate()
  useEffect(() => { fetchMe() }, [fetchMe])
  if (user) {
    navigate('/dashboard', { replace: true })
    return null
  }
  return <>{children}</>
}

export default function App() {
  return (
    <AuthProvider>
      <ExamSessionProvider>
        <Routes>
          <Route path="/login" element={<LoginGuard><Login /></LoginGuard>} />
          <Route element={<RequireAuth><Layout /></RequireAuth>}>
            <Route path="/dashboard" element={<Dashboard />} />
            <Route path="/exam" element={<ExamList />} />
            <Route path="/exam/:examId" element={<Exam />} />
            <Route path="/upload" element={<DataUpload />} />
            <Route path="/history" element={<ExamHistory />} />
            <Route path="/courses" element={<CourseManagement />} />
          </Route>
          <Route path="*" element={<Navigate to="/login" replace />} />
        </Routes>
      </ExamSessionProvider>
    </AuthProvider>
  )
}
