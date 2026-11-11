import { useEffect, useState } from 'react'
import { createRoot } from 'react-dom/client'
import { ApiError, errorText, request, type Session } from './api/client'
import { Login } from './features/Login'
import { Workspace } from './features/Workspace'
import './style.css'
function App() {
  const [session, setSession] = useState<Session | null>()
  const [error, setError] = useState('')
  const [attempt, setAttempt] = useState(0)
  useEffect(() => {
    let active = true
    const expired = () => setSession(null)
    window.addEventListener('jarvis-session-expired', expired)
    request<Session>('/auth/session')
      .then((s) => {
        if (active) setSession(s)
      })
      .catch((e) => {
        if (!active) return
        if (e instanceof ApiError && e.status === 401) setSession(null)
        else setError(errorText(e))
      })
    return () => {
      active = false
      window.removeEventListener('jarvis-session-expired', expired)
    }
  }, [attempt])
  if (error)
    return (
      <main className="login-shell">
        <div className="login-card">
          <h1>Conexão indisponível</h1>
          <p role="alert">{error}</p>
          <button
            className="primary"
            onClick={() => {
              setError('')
              setAttempt((n) => n + 1)
            }}
          >
            Tentar novamente
          </button>
        </div>
      </main>
    )
  if (session === undefined)
    return (
      <main className="login-shell" role="status">
        Abrindo painel…
      </main>
    )
  return session ? (
    <Workspace session={session} onLogout={() => setSession(null)} />
  ) : (
    <Login onLogin={setSession} />
  )
}
createRoot(document.getElementById('root')!).render(<App />)
