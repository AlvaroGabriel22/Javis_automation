import { useState, type FormEvent } from 'react'
import { ArrowRight, KeyRound, Terminal } from 'lucide-react'
import { request, errorText, type Session } from '../api/client'
import { Brand } from '../components/Brand'
export function Login({ onLogin }: { onLogin: (session: Session) => void }) {
  const [code, setCode] = useState('')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  async function submit(e: FormEvent) {
    e.preventDefault()
    setBusy(true)
    setError('')
    try {
      onLogin(
        await request('/auth/login', {
          method: 'POST',
          body: { code: code.trim() },
        }),
      )
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <main className="login-shell">
      <section className="login-card">
        <Brand />
        <div className="login-intro">
          <span className="key-icon">
            <KeyRound size={22} />
          </span>
          <h1>Acesso ao painel</h1>
          <p>
            Entre com o código temporário gerado na máquina que hospeda o
            Jarvis.
          </p>
        </div>
        <form onSubmit={submit}>
          <label htmlFor="access">Código de acesso</label>
          <input
            id="access"
            type="password"
            autoFocus
            autoComplete="one-time-code"
            value={code}
            onChange={(e) => setCode(e.target.value)}
            required
          />
          {error && (
            <p role="alert" className="error">
              {error}
            </p>
          )}
          <button className="primary full" disabled={busy || !code.trim()}>
            {busy ? 'Entrando…' : 'Entrar no painel'}
            <ArrowRight size={17} />
          </button>
        </form>
        <details>
          <summary>
            <Terminal size={15} /> Como gerar o código
          </summary>
          <p>Na raiz do projeto, execute:</p>
          <code>make login-code</code>
          <p>
            Se iniciou com <strong>make demo</strong>, use{' '}
            <strong>make demo-code</strong>.
          </p>
        </details>
      </section>
    </main>
  )
}
