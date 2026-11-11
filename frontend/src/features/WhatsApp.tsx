import { useCallback, useEffect, useRef, useState, type FormEvent } from 'react'
import { Link2, MessageCircle, QrCode, ShieldCheck } from 'lucide-react'
import { errorText, request } from '../api/client'

type Status = {
  state:
    'offline' | 'idle' | 'connecting' | 'pairing' | 'connected' | 'disconnected'
  account: string | null
  owner_phone: string | null
  pending_phone: string | null
  binding_status:
    | 'waiting'
    | 'wrong_code'
    | 'different_number'
    | 'unverified_chat'
    | 'expired'
    | 'save_failed'
    | 'interrupted'
    | null
  observed_phone: string | null
  qr: string | null
  queues: { inbox: Record<string, number>; outbox: Record<string, number> }
}
const labels: Record<Status['state'], string> = {
  offline: 'Gateway desligado',
  idle: 'Aguardando pareamento',
  connecting: 'Conectando',
  pairing: 'Aguardando leitura do QR',
  connected: 'Aparelho conectado',
  disconnected: 'Conexão interrompida',
}

function bindingNotice(status: Status) {
  switch (status.binding_status) {
    case 'waiting':
      return 'Aguardando a frase no WhatsApp do Jarvis.'
    case 'wrong_code':
      return 'A frase chegou do seu número, mas o código está incorreto. Envie a frase do código mais recente.'
    case 'different_number':
      return `A frase chegou de ${status.observed_phone}. Você cadastrou ${status.pending_phone}. Confira os números. Se reconhece o remetente como seu, gere um código para esse número.`
    case 'unverified_chat':
      return 'A frase chegou, mas o Jarvis não conseguiu verificar o remetente. Envie uma nova mensagem diretamente do seu WhatsApp pessoal para o número do Jarvis.'
    case 'expired':
      return 'O código expirou. Gere outro e envie a nova frase.'
    case 'interrupted':
      return 'A conexão com o WhatsApp foi interrompida durante o vínculo. Gere um novo código.'
    case 'save_failed':
      return 'A frase e o número conferem, mas o Jarvis não conseguiu salvar o vínculo. Verifique o espaço e a permissão de escrita da pasta do WhatsApp e reenvie a frase.'
  }
}

export function WhatsApp({ csrf }: { csrf: string }) {
  const [status, setStatus] = useState<Status | null>(null)
  const [error, setError] = useState('')
  const [readError, setReadError] = useState('')
  const [phone, setPhone] = useState('')
  const [proof, setProof] = useState<{ phrase: string; until: number } | null>(
    null,
  )
  const [busy, setBusy] = useState(false)
  const generation = useRef(0)
  const refresh = useCallback(async () => {
    const id = ++generation.current
    try {
      const result = await request<Status>('/whatsapp')
      if (id !== generation.current) return
      setStatus(result)
      setReadError('')
      setProof((old) =>
        old &&
        result.pending_phone &&
        !result.owner_phone &&
        Date.now() < old.until
          ? old
          : null,
      )
    } catch (e) {
      if (id === generation.current) {
        setReadError(errorText(e))
        setStatus(null)
        setProof(null)
      }
    }
  }, [])
  useEffect(() => {
    let active = true
    let timer: ReturnType<typeof setTimeout>
    async function poll() {
      await refresh()
      if (active) timer = setTimeout(() => void poll(), 3000)
    }
    void poll()
    return () => {
      active = false
      clearTimeout(timer)
      generation.current++
    }
  }, [refresh])
  async function mutate(action: () => Promise<void>) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      await action()
      await refresh()
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  function bind(e: FormEvent) {
    e.preventDefault()
    void mutate(async () => {
      const result = await request<{
        phrase: string
        valid_for_seconds: number
      }>('/whatsapp/binding', {
        method: 'POST',
        body: { phone: phone.replace(/[\s()-]/g, '') },
        csrf,
      })
      setProof({
        phrase: result.phrase,
        until: Date.now() + result.valid_for_seconds * 1000,
      })
    })
  }
  return (
    <section className="wa-page">
      <h1>WhatsApp</h1>
      {(error || readError) && (
        <p className="error" role="alert">
          {error || readError}
        </p>
      )}
      {!status ? (
        <p role="status">
          {readError
            ? 'Tentando reconectar ao painel…'
            : 'Consultando o gateway…'}
        </p>
      ) : (
        <>
          <section className="panel wa-panel">
            <div className="wa-title">
              <QrCode size={22} />
              <h2>Aparelho do Jarvis</h2>
            </div>
            <p>
              <strong>{labels[status.state]}</strong>
              {status.account && ' · ' + status.account}
            </p>
            {status.state === 'offline' && (
              <p>
                Inicie o conjunto de serviços com <code>make demo-chat</code> ou{' '}
                <code>make chat</code> no perfil PostgreSQL.
              </p>
            )}
            {status.qr ? (
              <div className="wa-pair">
                <img
                  width="220"
                  height="220"
                  alt="QR Code para vincular o WhatsApp do Jarvis"
                  src={status.qr}
                />
                <p>
                  No celular do número dedicado ao Jarvis, abra{' '}
                  <strong>Dispositivos conectados</strong>, escolha conectar um
                  dispositivo e leia este código.
                </p>
              </div>
            ) : (
              status.state === 'pairing' && (
                <p>Aguardando um novo QR do WhatsApp…</p>
              )
            )}
            {(['idle', 'disconnected'].includes(status.state) ||
              (status.state === 'pairing' && !status.qr)) && (
              <button
                className="primary"
                disabled={busy}
                onClick={() =>
                  void mutate(async () => {
                    await request('/whatsapp/pairing', { method: 'POST', csrf })
                  })
                }
              >
                <Link2 size={16} />
                {status.state === 'pairing'
                  ? 'Renovar QR'
                  : 'Conectar aparelho'}
              </button>
            )}
          </section>
          {status.state === 'connected' && (
            <section className="panel wa-panel">
              <div className="wa-title">
                <ShieldCheck size={22} />
                <h2>Seu número autorizado</h2>
              </div>
              {status.owner_phone ? (
                <>
                  <p role="status">
                    <strong>Vínculo confirmado.</strong> {status.owner_phone}{' '}
                    pode conversar com o Jarvis pelo WhatsApp.
                  </p>
                  <button
                    className="quiet"
                    disabled={busy}
                    onClick={() =>
                      void mutate(async () => {
                        await request('/whatsapp/binding', {
                          method: 'DELETE',
                          csrf,
                        })
                        setProof(null)
                      })
                    }
                  >
                    Remover vínculo
                  </button>
                </>
              ) : (
                <form onSubmit={bind}>
                  <label htmlFor="owner-phone">
                    Seu WhatsApp pessoal, com código do país
                  </label>
                  <input
                    id="owner-phone"
                    type="tel"
                    value={phone}
                    onChange={(e) => setPhone(e.target.value)}
                    required
                    placeholder="+55 92 …"
                  />
                  <button className="primary" disabled={busy || !phone.trim()}>
                    Gerar código de vínculo
                  </button>
                </form>
              )}
              {proof && !status.owner_phone && (
                <div className="proof" role="status">
                  <p>
                    Do seu número pessoal, envie esta frase para{' '}
                    <strong>{status.account}</strong> em até cinco minutos.
                  </p>
                  <code>{proof.phrase}</code>
                </div>
              )}
              {!status.owner_phone && status.binding_status && (
                <p role="status">{bindingNotice(status)}</p>
              )}
            </section>
          )}
          <section className="panel wa-panel">
            <div className="wa-title">
              <MessageCircle size={22} />
              <h2>Conversa</h2>
            </div>
            <p>
              Envie texto ou áudio do seu número vinculado. O Jarvis responde por escrito.
            </p>
            <dl className="wa-stats">
              <div>
                <dt>Para processar</dt>
                <dd>
                  {(status.queues.inbox.pending || 0) +
                    (status.queues.inbox.processing || 0)}
                </dd>
              </div>
              <div>
                <dt>Respostas aceitas</dt>
                <dd>{status.queues.outbox.accepted || 0}</dd>
              </div>
              <div>
                <dt>Falhas de IA</dt>
                <dd>{status.queues.inbox.failed || 0}</dd>
              </div>
              <div>
                <dt>Envios incertos</dt>
                <dd>{status.queues.outbox.uncertain || 0}</dd>
              </div>
            </dl>
          </section>
        </>
      )}
    </section>
  )
}
