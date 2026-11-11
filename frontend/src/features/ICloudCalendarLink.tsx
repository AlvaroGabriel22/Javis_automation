import { useState } from 'react'
import { Dialog } from '../components/Dialog'
import { request, errorText } from '../api/client'

export function ICloudCalendarLink({
  account,
  csrf,
  close,
  saved,
}: {
  account: string | null
  csrf: string
  close: () => void
  saved: (message: string) => void
}) {
  const [email, setEmail] = useState(account ?? ''),
    [password, setPassword] = useState('')
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [removing, setRemoving] = useState(false)
  async function submit(remove: boolean) {
    setBusy(true)
    setError('')
    try {
      await request(
        '/calendar/icloud',
        remove
          ? { method: 'DELETE', csrf }
          : { method: 'PUT', csrf, body: { email: email.trim(), password } },
      )
      setPassword('')
      saved(
        remove
          ? 'Conta Apple desconectada.'
          : 'Calendários do iCloud vinculados. Aguardando a sincronização.',
      )
    } catch (err) {
      setError(errorText(err))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog title="Calendário do iCloud" onClose={close} busy={busy}>
      <form
        className="google-flow"
        onSubmit={(e) => {
          e.preventDefault()
          void submit(false)
        }}
      >
        {account && (
          <p className="google-account">
            Conta vinculada <strong>{account}</strong>
          </p>
        )}
        <p>
          Na sua Conta Apple, abra{' '}
          <strong>
            Início de sessão e segurança → Senhas específicas de apps
          </strong>{' '}
          e gere uma senha para o Jarvis. Essa opção exige autenticação de dois
          fatores.
        </p>
        <a href="https://account.apple.com/" target="_blank" rel="noreferrer">
          Abrir Conta Apple
        </a>
        <label>
          E-mail da Conta Apple
          <input
            required
            type="email"
            value={email}
            autoComplete="username"
            readOnly={!!account}
            onChange={(e) => setEmail(e.target.value)}
          />
        </label>
        <label>
          Senha específica do app
          <input
            required
            type="password"
            value={password}
            autoComplete="off"
            placeholder="Senha gerada para o Jarvis"
            onChange={(e) => setPassword(e.target.value)}
          />
        </label>
        {error && (
          <p role="alert" className="error">
            {error}
          </p>
        )}
        <div className="actions">
          {account && (
            <button
              type="button"
              disabled={busy}
              onClick={() => (removing ? void submit(true) : setRemoving(true))}
            >
              {removing ? 'Confirmar desconexão' : 'Desconectar iCloud'}
            </button>
          )}
          <button className="primary" disabled={busy}>
            {busy
              ? 'Aguarde…'
              : account
                ? 'Renovar conexão'
                : 'Vincular iCloud'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
