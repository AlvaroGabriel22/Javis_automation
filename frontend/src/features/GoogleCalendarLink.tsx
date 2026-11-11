import { useEffect, useState } from 'react'
import { Dialog } from '../components/Dialog'
import { errorText, request } from '../api/client'

type Phase = 'idle' | 'waiting' | 'exchanging' | 'complete' | 'failed'
type Status = {
  configured: boolean
  account: string | null
  phase: Phase
  message: string | null
  sync_running: boolean
  demo: boolean
}
const path = '/calendar/google'
const audienceUrl = 'https://console.cloud.google.com/auth/audience'

function CredentialHelp() {
  return (
    <details className="google-help">
      <summary>Como obter o arquivo no Google Cloud?</summary>
      <ol>
        <li>
          Selecione ou crie um projeto e ative a{' '}
          <a
            href="https://console.cloud.google.com/apis/library/calendar-json.googleapis.com"
            target="_blank"
            rel="noreferrer"
          >
            Google Calendar API
          </a>
          .
        </li>
        <li>
          Em{' '}
          <a
            href="https://console.cloud.google.com/auth/branding"
            target="_blank"
            rel="noreferrer"
          >
            Google Auth Platform
          </a>
          , configure o consentimento com o nome Jarvis e seu e-mail. Para Gmail
          pessoal, escolha público externo. Em modo de teste, adicione seu Gmail
          em{' '}
          <a
            href={audienceUrl}
            target="_blank"
            rel="noreferrer"
          >
            Público → Usuários de teste
          </a>
          .
        </li>
        <li>
          Em{' '}
          <a
            href="https://console.cloud.google.com/auth/clients"
            target="_blank"
            rel="noreferrer"
          >
            Clientes
          </a>
          , clique em <strong>Criar cliente</strong>, escolha{' '}
          <strong>Aplicativo para computador</strong>, crie e baixe o JSON.
          Volte aqui com esse arquivo.
        </li>
      </ol>
    </details>
  )
}

export function GoogleCalendarLink({
  csrf,
  close,
  changed,
}: {
  csrf: string
  close: () => void
  changed: () => void
}) {
  const [state, setState] = useState<Status | null>(null)
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [replace, setReplace] = useState(false)
  const [url, setUrl] = useState(''),
    [attempt, setAttempt] = useState(0)
  const pending = state?.phase === 'waiting' || state?.phase === 'exchanging'
  useEffect(() => {
    let current = true
    let timer: ReturnType<typeof setTimeout>
    async function check() {
      try {
        const next = await request<Status>(path)
        if (!current) return
        setState(next)
        setError('')
        if (next.phase === 'waiting' || next.phase === 'exchanging') {
          timer = setTimeout(() => void check(), 1500)
        } else {
          setUrl('')
          if (next.phase === 'complete') changed()
        }
      } catch (e) {
        if (current) setError(errorText(e))
      }
    }
    void check()
    return () => {
      current = false
      clearTimeout(timer)
    }
  }, [attempt, changed])

  async function importFile(file?: File) {
    if (!file) return
    setError('')
    setBusy(true)
    try {
      if (file.size > 32768)
        throw new Error('Selecione um arquivo JSON de até 32 KiB.')
      let data: unknown
      try {
        data = JSON.parse(await file.text())
      } catch {
        throw new Error('Selecione o arquivo JSON baixado do Google Cloud.')
      }
      const next = await request<Status>(path + '/client', {
        csrf,
        method: 'PUT',
        body: data,
      })
      setReplace(false)
      setState(next)
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }

  async function authorize() {
    setError('')
    setBusy(true)
    // Reserva a aba durante o clique; bloqueadores recebem um link manual na mesma tela.
    const tab = window.open('about:blank', '_blank')
    if (tab) tab.opener = null
    try {
      const auth = await request<{ url: string | null; phase: Phase }>(
        path + '/connect',
        { method: 'POST', csrf },
      )
      if (auth.url) {
        setUrl(auth.url)
        if (tab) tab.location.replace(auth.url)
      } else tab?.close()
      setState((s) => s && { ...s, phase: auth.phase, message: null })
      setAttempt((n) => n + 1)
    } catch (e) {
      tab?.close()
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog title="Conectar Google Agenda" busy={busy} onClose={close}>
      <div className="google-flow">
        {!state && !error && <p role="status">Verificando a configuração…</p>}
        {state && (
          <>
            {state.account && (
              <p className="google-account" role="status">
                Conta vinculada <strong>{state.account}</strong>
              </p>
            )}
            <section>
              <h3>Credencial do aplicativo</h3>
              {state.configured && !replace ? (
                <div className="google-inline">
                  <span>Credencial pronta para uso.</span>
                  {!pending && (
                    <button
                      type="button"
                      className="text-button"
                      onClick={() => setReplace(true)}
                    >
                      Trocar arquivo
                    </button>
                  )}
                </div>
              ) : (
                <>
                  <p>
                    Importe o JSON do aplicativo Jarvis. Ele fica salvo neste
                    computador.
                  </p>
                  <CredentialHelp />
                  <label>
                    Importar arquivo JSON
                    <input
                      type="file"
                      accept=".json,application/json"
                      disabled={busy || pending}
                      onChange={(e) => {
                        const file = e.target.files?.[0]
                        e.target.value = ''
                        void importFile(file)
                      }}
                    />
                  </label>
                  {replace && (
                    <button
                      className="text-button"
                      type="button"
                      onClick={() => setReplace(false)}
                    >
                      Manter credencial atual
                    </button>
                  )}
                </>
              )}
            </section>
            {state.configured && !replace && (
              <section>
                <h3>Autorização da conta</h3>
                {state.phase === 'waiting' ? (
                  <p role="status">
                    Aguardando você escolher a conta e permitir o acesso no
                    Google. Depois, volte a esta aba.
                  </p>
                ) : state.phase === 'exchanging' ? (
                  <p role="status">Conferindo a autorização com o Google…</p>
                ) : (
                  <p>
                    O Jarvis pede acesso para listar suas agendas e consultar,
                    criar e editar compromissos.
                  </p>
                )}
                {state.message && (
                  <p className="error" role="alert">
                    {state.message}
                  </p>
                )}
                {(!state.account || state.phase === 'failed' || pending) && (
                  <details className="google-help">
                    <summary>O Google mostrou “Acesso bloqueado” (403)?</summary>
                    <ol>
                      <li>
                        Abra o{' '}
                        <a href={audienceUrl} target="_blank" rel="noreferrer">
                          Público-alvo no Google Cloud
                        </a>{' '}
                        e selecione o projeto que gerou o JSON importado.
                      </li>
                      <li>
                        Em <strong>Usuários de teste → Adicionar usuários</strong>,
                        informe o Gmail que vai usar no Jarvis e clique em{' '}
                        <strong>Salvar</strong>.
                      </li>
                      <li>
                        Volte a este painel, abra a autorização novamente e
                        escolha o mesmo Gmail.
                      </li>
                    </ol>
                  </details>
                )}
                {state.phase === 'waiting' && url ? (
                  <a href={url} target="_blank" rel="noreferrer">
                    Abrir autorização no Google
                  </a>
                ) : (
                  state.phase !== 'exchanging' && (
                    <button
                      type="button"
                      disabled={busy}
                      className={state.account ? '' : 'primary'}
                      onClick={() => void authorize()}
                    >
                      {state.phase === 'waiting'
                        ? 'Reabrir autorização no Google'
                        : state.account
                          ? 'Autorizar novamente'
                          : 'Autorizar no Google'}
                    </button>
                  )
                )}
              </section>
            )}
            {state.account && !pending && (
              <section>
                {state.sync_running ? (
                  <p>
                    As agendas aparecem no painel após a sincronização. O Jarvis
                    verifica o Google a cada minuto.
                  </p>
                ) : (
                  <p>
                    O sincronizador está desligado. Na pasta do Jarvis, encerre
                    o painel isolado e inicie{' '}
                    <code>{state.demo ? 'make demo-chat' : 'make chat'}</code>{' '}
                    para carregar suas agendas.
                  </p>
                )}
                <button type="button" className="primary" onClick={close}>
                  Voltar à agenda
                </button>
              </section>
            )}
          </>
        )}
        {error && (
          <p className="error" role="alert">
            {error}{' '}
            <button type="button" onClick={() => setAttempt((n) => n + 1)}>
              Verificar configuração
            </button>
          </p>
        )}
      </div>
    </Dialog>
  )
}
