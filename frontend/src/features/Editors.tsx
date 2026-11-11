import { useEffect, useRef, useState, type FormEvent } from 'react'
import { Dialog } from '../components/Dialog'
import { errorText, request, type Contact, type Profile, type Schedule } from '../api/client'
import { isoFromManaus, localMinute } from '../time'

interface Base {
  csrf: string
  onClose: () => void
}
function useSave() {
  const last = useRef({ data: '', key: '' })
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const inFlight = useRef(false)
  async function run(operation: () => Promise<void>) {
    if (inFlight.current) return
    inFlight.current = true
    setBusy(true)
    setError('')
    try {
      await operation()
    } catch (e) {
      setError(errorText(e))
    } finally {
      inFlight.current = false
      setBusy(false)
    }
  }
  function key(body: unknown) {
    const data = JSON.stringify(body)
    if (last.current.data !== data)
      last.current = { data, key: crypto.randomUUID() }
    return last.current.key
  }
  return { busy, error, run, key }
}
function Actions({
  busy,
  onClose,
  label,
}: {
  busy: boolean
  onClose: () => void
  label: string
}) {
  return (
    <div className="actions">
      <button type="button" className="quiet" disabled={busy} onClick={onClose}>
        Voltar
      </button>
      <button className="primary" disabled={busy}>
        {busy ? 'Salvando…' : label}
      </button>
    </div>
  )
}
export function ContactDialog({
  csrf,
  onClose,
  onSave,
}: Base & { onSave: (c: Contact) => void }) {
  const save = useSave()
  const [query, setQuery] = useState('')
  const [choices, setChoices] = useState<{ name: string; phone: string }[]>([])
  const [person, setPerson] = useState({ name: '', phone: '' })
  useEffect(() => {
    let active = true
    const timer = setTimeout(() => {
      void request<{ name: string; phone: string }[]>(
        '/whatsapp/directory?q=' + encodeURIComponent(query),
      )
        .then((rows) => {
          if (active) setChoices(rows)
        })
        .catch(() => {
          if (active) setChoices([])
        })
    }, 250)
    return () => {
      active = false
      clearTimeout(timer)
    }
  }, [query])
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const data = new FormData(e.currentTarget)
    const body = {
      name: String(data.get('name')).trim(),
      aliases: String(data.get('aliases') || '')
        .split(',')
        .map((x) => x.trim())
        .filter(Boolean),
      phone: String(data.get('phone'))
        .trim()
        .replace(/[\s()-]/g, ''),
    }
    void save.run(async () => {
      if (!/^\+[1-9]\d{6,14}$/.test(body.phone))
        throw new Error(
          'Inclua o código do país e o DDD, por exemplo +5592 seguido do número.',
        )
      onSave(
        await request<Contact>('/contacts', {
          method: 'POST',
          body,
          csrf,
          key: save.key(body),
        }),
      )
    })
  }
  return (
    <Dialog title="Novo contato" onClose={onClose} busy={save.busy}>
      <form onSubmit={submit}>
        <label htmlFor="device-search">Buscar na agenda do WhatsApp</label>
        <input
          id="device-search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Nome ou número"
        />
        {choices.length > 0 && (
          <select
            aria-label="Escolher contato do aparelho"
            value=""
            onChange={(e) => {
              const picked = choices.find((c) => c.phone === e.target.value)
              if (picked) setPerson(picked)
            }}
          >
            <option value="">Escolha na agenda ou preencha abaixo</option>
            {choices.map((c) => (
              <option key={c.phone} value={c.phone}>
                {c.name} · {c.phone}
              </option>
            ))}
          </select>
        )}
        <label htmlFor="contact-name">Nome</label>
        <input
          value={person.name}
          onChange={(e) => setPerson({ ...person, name: e.target.value })}
          id="contact-name"
          name="name"
          autoFocus
          autoComplete="off"
          maxLength={100}
          required
        />
        <label htmlFor="phone">WhatsApp com código do país</label>
        <input
          value={person.phone}
          onChange={(e) => setPerson({ ...person, phone: e.target.value })}
          id="phone"
          type="tel"
          name="phone"
          placeholder="+55 92 …"
          autoComplete="off"
          required
        />
        <label htmlFor="aliases">Apelidos (opcional)</label>
        <input name="aliases" id="aliases" placeholder="Separe por vírgula" />
        {save.error && (
          <p className="error" role="alert">
            {save.error}
          </p>
        )}
        <Actions {...{ onClose, busy: save.busy, label: 'Salvar contato' }} />
      </form>
    </Dialog>
  )
}
export function ScheduleDialog({
  csrf,
  contacts,
  onClose,
  onSave,
}: Base & { contacts: Contact[]; onSave: (s: Schedule) => void }) {
  const save = useSave()
  const [message, setMessage] = useState('')
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const data = new FormData(e.currentTarget)
    void save.run(async () => {
      const body = {
        contact_id: String(data.get('contact')),
        recurrence: String(data.get('recurrence')),
        scheduled_at: isoFromManaus(String(data.get('when'))),
        text: String(data.get('text')).trim(),
      }
      if (!body.text || new Date(body.scheduled_at).getTime() <= Date.now())
        throw new Error('Escreva a mensagem e escolha um horário futuro.')
      onSave(
        await request<Schedule>('/schedules', {
          method: 'POST',
          body,
          csrf,
          key: save.key(body),
        }),
      )
    })
  }
  return (
    <Dialog title="Agendar mensagem" onClose={onClose} busy={save.busy}>
      <form onSubmit={submit}>
        <label htmlFor="target">Destinatário</label>
        <select id="target" name="contact" required defaultValue="" autoFocus>
          <option value="" disabled>
            Selecione um contato
          </option>
          {contacts.map((c) => (
            <option key={c.id} value={c.id}>
              {c.name} · {c.phone}
            </option>
          ))}
        </select>
        <label htmlFor="when">
          Data e hora <small>Manaus</small>
        </label>
        <input
          id="when"
          name="when"
          type="datetime-local"
          min={localMinute(new Date())}
          required
        />
        <label htmlFor="recurrence">Repetição</label>
        <select name="recurrence" id="recurrence" defaultValue="none">
          <option value="none">Uma vez</option>
          <option value="daily">Todos os dias</option>
          <option value="weekly">Toda semana</option>
        </select>
        <label htmlFor="message">Mensagem</label>
        <textarea
          name="text"
          id="message"
          rows={4}
          maxLength={4000}
          required
          value={message}
          onChange={(e) => setMessage(e.target.value)}
        />
        <button
          type="button"
          className="text-button"
          disabled={save.busy || !!message.trim()}
          onClick={() =>
            void save.run(async () => {
              const transcript = await request<{ text: string }>(
                '/whatsapp/latest-transcript',
              )
              setMessage(transcript.text)
            })
          }
        >
          Usar última transcrição
        </button>
        {save.error && (
          <p className="error" role="alert">
            {save.error}
          </p>
        )}
        <Actions
          {...{ onClose, busy: save.busy, label: 'Salvar agendamento' }}
        />
      </form>
    </Dialog>
  )
}
export function CancelDialog({
  csrf,
  item,
  name,
  onClose,
  onSave,
}: Base & { item: Schedule; name: string; onSave: (s: Schedule) => void }) {
  const save = useSave()
  return (
    <Dialog title="Cancelar agendamento?" onClose={onClose} busy={save.busy}>
      <p>
        O agendamento para <strong>{name}</strong> será cancelado.
      </p>
      <blockquote>{item.text}</blockquote>
      {save.error && (
        <p className="error" role="alert">
          {save.error}
        </p>
      )}
      <div className="actions">
        <button className="quiet" disabled={save.busy} onClick={onClose}>
          Manter
        </button>
        <button
          className="danger"
          disabled={save.busy}
          onClick={() =>
            void save.run(async () =>
              onSave(
                await request<Schedule>(
                  '/schedules/' + encodeURIComponent(item.id),
                  { method: 'DELETE', csrf },
                ),
              ),
            )
          }
        >
          Cancelar agendamento
        </button>
      </div>
    </Dialog>
  )
}

export function RescheduleDialog({
  csrf,
  item,
  onClose,
  onSave,
}: Base & { item: Schedule; onSave: (s: Schedule) => void }) {
  const save = useSave()
  const title = item.armed ? 'Remarcar envio' : 'Ativar envio'
  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault()
    const when = String(new FormData(event.currentTarget).get('when'))
    void save.run(async () => {
      const scheduled_at = isoFromManaus(when)
      if (Date.parse(scheduled_at) <= Date.now())
        throw new Error('Escolha um horário futuro.')
      onSave(
        await request<Schedule>('/schedules/' + item.id, {
          method: 'PATCH',
          csrf,
          body: { scheduled_at },
        }),
      )
    })
  }
  return (
    <Dialog title={title} onClose={onClose} busy={save.busy}>
      <form onSubmit={submit}>
        <blockquote>{item.text}</blockquote>
        <label htmlFor="new-when">
          Data e hora <small>Manaus</small>
        </label>
        <input
          type="datetime-local"
          id="new-when"
          name="when"
          required
          min={localMinute(new Date())}
          defaultValue={localMinute(new Date(item.scheduled_at))}
        />
        {save.error && (
          <p role="alert" className="error">
            {save.error}
          </p>
        )}
        <Actions busy={save.busy} onClose={onClose} label={title} />
      </form>
    </Dialog>
  )
}

export function DeleteContactDialog({
  csrf,
  contact,
  onClose,
  onDelete,
}: Base & {
  contact: Contact
  onDelete: () => void
}) {
  const save = useSave()
  return (
    <Dialog
      title="Excluir contato do Jarvis?"
      onClose={onClose}
      busy={save.busy}
    >
      <p>
        <strong>{contact.name}</strong> · {contact.phone}
      </p>
      <p>
        O contato será removido da lista do Jarvis. Mensagens já agendadas
        continuam programadas.
      </p>
      {save.error && (
        <p className="error" role="alert">
          {save.error}
        </p>
      )}
      <div className="actions">
        <button
          type="button"
          className="quiet"
          disabled={save.busy}
          onClick={onClose}
        >
          Voltar
        </button>
        <button
          type="button"
          className="danger"
          disabled={save.busy}
          onClick={() =>
            void save.run(async () => {
              await request('/contacts/' + encodeURIComponent(contact.id), {
                method: 'DELETE',
                csrf,
              })
              onDelete()
            })
          }
        >
          {save.busy ? 'Excluindo…' : 'Excluir contato'}
        </button>
      </div>
    </Dialog>
  )
}

export function ProfileDialog({
  csrf, profile, onSave, onClose,
}: Base & { profile: Profile; onSave: (profile: Profile) => void }) {
  const save = useSave()
  function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    const form = new FormData(e.currentTarget)
    const body = Object.fromEntries(form)
    void save.run(async () => {
      const updated = await request<Profile>('/profile', { method: 'PUT', csrf, body })
      onSave(updated)
    })
  }
  return (
    <Dialog title="Meu perfil" busy={save.busy} onClose={onClose}>
      <form onSubmit={submit}>
        <label>Nome que o Jarvis deve usar
          <input name="preferred_name" defaultValue={profile.preferred_name} maxLength={120} required />
        </label>
        <label>Gênero
          <input name="gender" list="gender-options" defaultValue={profile.gender} maxLength={40} required />
          <datalist id="gender-options">
            {['Masculino', 'Feminino', 'Não binário', 'Não informado'].map((value) => <option key={value} value={value} />)}
          </datalist>
        </label>
        <label>Forma de tratamento
          <input name="address_as" list="address-options" defaultValue={profile.address_as} maxLength={40} required />
          <datalist id="address-options">
            {['senhor', 'senhora', 'nome'].map((value) => <option key={value} value={value} />)}
          </datalist>
          <small>Use “nome” para ser chamado apenas pelo nome ou escreva o tratamento que preferir.</small>
        </label>
        <label>Tom da conversa
          <select name="tone" defaultValue={profile.tone}>
            <option value="proximo">Próximo</option>
            <option value="neutro">Neutro</option>
          </select>
          <small>No tom próximo, o Jarvis pode usar apelidos e brincadeiras.</small>
        </label>
        {save.error && <p className="error" role="alert">{save.error}</p>}
        <Actions busy={save.busy} onClose={onClose} label="Salvar perfil" />
      </form>
    </Dialog>
  )
}
