import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import {
  ArrowUpRight,
  CalendarClock,
  CalendarDays,
  Clock3,
  ContactRound,
  LogOut,
  MessageCircle,
  Plus,
  RefreshCw,
  Search,
  SendHorizontal,
  Users,
  UserRound,
  Wallet,
  X,
} from 'lucide-react'
import { Brand } from '../components/Brand'
import {
  errorText,
  readList,
  request,
  type Contact,
  type Schedule,
  type Session,
} from '../api/client'
import { formatDay, formatTime } from '../time'
import {
  CancelDialog,
  ContactDialog,
  DeleteContactDialog,
  ProfileDialog,
  ScheduleDialog,
  RescheduleDialog,
} from './Editors'
import { Calendar } from './Calendar'
import { Finance } from './Finance'
import { WhatsApp } from './WhatsApp'
type Screen = 'schedules' | 'calendar' | 'contacts' | 'whatsapp' | 'finance'
type Editor =
  | { kind: 'profile' }
  | { kind: 'contact' }
  | { kind: 'delete-contact'; contact: Contact }
  | { kind: 'schedule' }
  | { kind: 'cancel' | 'reschedule'; item: Schedule }
  | null
const states: Record<Schedule['status'], string> = {
  scheduled: 'Agendada',
  sending: 'Em transmissão',
  accepted: 'Aceita pelo WhatsApp',
  cancelled: 'Cancelada',
  uncertain: 'Resultado incerto',
  failed: 'Falha',
  needs_review: 'Horário vencido',
}
const tabs = {
  scheduled: 'Agendadas',
  history: 'Histórico',
  review: 'Revisar',
  cancelled: 'Canceladas',
} as const
export function Workspace({
  session,
  onLogout,
}: {
  session: Session
  onLogout: () => void
}) {
  const [profile, setProfile] = useState(session.profile)
  const [screen, setScreen] = useState<Screen>('schedules')
  const [contacts, setContacts] = useState<Contact[]>([])
  const [items, setItems] = useState<Schedule[]>([])
  const [busy, setBusy] = useState(true)
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [query, setQuery] = useState('')
  const [filter, setFilter] = useState<keyof typeof tabs>('scheduled')
  const [online, setOnline] = useState(false)
  const [editor, setEditor] = useState<Editor>(null)
  const [startingSchedule, setStartingSchedule] = useState(false)
  const listGeneration = useRef(0)
  const load = useCallback(async (silent = false) => {
    const generation = ++listGeneration.current
    if (!silent) setBusy(true)
    setError('')
    try {
      const [people, schedules, health] = await Promise.all([
        readList<Contact>('/contacts'),
        readList<Schedule>('/schedules'),
        request<{ delivery_enabled: boolean }>('/status'),
      ])
      if (generation !== listGeneration.current) return
      setOnline(health.delivery_enabled)
      setContacts(people)
      setItems(schedules)
    } catch (e) {
      if (generation === listGeneration.current) setError(errorText(e))
    } finally {
      if (generation === listGeneration.current) setBusy(false)
    }
  }, [])
  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(true), 10000)
    return () => clearInterval(timer)
  }, [load])
  const queue = useMemo(
    () =>
      [...items].sort((a, b) => a.scheduled_at.localeCompare(b.scheduled_at)),
    [items],
  )
  const count = queue.filter((s) => s.status === 'scheduled').length
  const visible = queue.filter(
    (s) =>
      (filter === 'review'
        ? ['uncertain', 'failed', 'needs_review'].includes(s.status)
        : filter === 'history'
          ? ['accepted', 'sending'].includes(s.status)
          : s.status === filter) &&
      (s.recipient.name + ' ' + s.text + ' #' + s.id.slice(0, 8))
        .toLocaleLowerCase('pt-BR')
        .includes(query.toLocaleLowerCase('pt-BR')),
  )
  const filteredContacts = contacts.filter((c) =>
    (c.name + ' ' + c.phone)
      .toLocaleLowerCase('pt-BR')
      .includes(query.toLocaleLowerCase('pt-BR')),
  )
  const isAgenda = screen === 'schedules'
  function navigate(page: Screen) {
    setScreen(page)
    setQuery('')
    setNotice('')
  }
  function primaryAction() {
    if (!isAgenda) {
      setStartingSchedule(false)
      setEditor({ kind: 'contact' })
      return
    }
    if (!contacts.length) {
      setStartingSchedule(true)
      setNotice('Cadastre o destinatário para continuar o agendamento.')
      setEditor({ kind: 'contact' })
      return
    }
    setEditor({ kind: 'schedule' })
  }
  function saveSchedule(item: Schedule) {
    setItems((list) => [...list.filter((s) => s.id !== item.id), item])
    setEditor(null)
    setNotice(
      item.status === 'cancelled'
        ? 'Agendamento cancelado.'
        : 'Agendamento salvo.',
    )
  }
  async function logout() {
    try {
      await request('/auth/logout', {
        method: 'POST',
        csrf: session.csrf_token,
      })
      onLogout()
    } catch (e) {
      setError(errorText(e))
    }
  }
  return (
    <div className="workspace">
      <aside>
        <Brand />
        <nav aria-label="Navegação principal">
          <button
            aria-current={screen === 'calendar' ? 'page' : undefined}
            onClick={() => navigate('calendar')}
          >
            <CalendarDays size={19} />
            Agenda pessoal
          </button>
          <button
            aria-current={isAgenda ? 'page' : undefined}
            onClick={() => navigate('schedules')}
          >
            <CalendarClock size={19} />
            Agenda de envios
          </button>
          <button
            aria-current={screen === 'finance' ? 'page' : undefined}
            onClick={() => navigate('finance')}
          >
            <Wallet size={19} />
            Finanças e planilhas
          </button>
          <button
            aria-current={screen === 'contacts' ? 'page' : undefined}
            onClick={() => navigate('contacts')}
          >
            <Users size={19} />
            Contatos
          </button>
          <button
            aria-current={screen === 'whatsapp' ? 'page' : undefined}
            onClick={() => navigate('whatsapp')}
          >
            <MessageCircle size={19} />
            WhatsApp
          </button>
          <button onClick={() => setEditor({ kind: 'profile' })}>
            <UserRound size={19} /> Meu perfil
          </button>
        </nav>
        <div className="profile">
          <span className="avatar">
            {profile.preferred_name.slice(0, 1).toUpperCase()}
          </span>
          <strong>{profile.preferred_name}</strong>
          <button
            className="logout"
            onClick={() => void logout()}
            aria-label="Sair"
          >
            <LogOut size={18} />
          </button>
        </div>
      </aside>
      <main className="desk">
        <header className="topline">
          <span>Painel pessoal</span>
          <span>
            <Clock3 size={15} /> Horário de Manaus
          </span>
        </header>
        <div className="content">
          {screen === 'calendar' ? (
            <Calendar csrf={session.csrf_token} />
          ) : screen === 'finance' ? (
            <Finance
              csrf={session.csrf_token}
              openWhatsApp={() => navigate('whatsapp')}
            />
          ) : screen === 'whatsapp' ? (
            <WhatsApp csrf={session.csrf_token} />
          ) : (
            <>
              <section className="headline">
                <div>
                  <h1>{isAgenda ? 'Agenda de envios' : 'Contatos'}</h1>
                </div>
                <button
                  className="primary"
                  disabled={busy || !!error}
                  onClick={primaryAction}
                >
                  <Plus size={17} />
                  {isAgenda ? 'Agendar mensagem' : 'Novo contato'}
                </button>
              </section>
              {error && (
                <div className="error" role="alert">
                  {error}{' '}
                  <button className="text-button" onClick={() => void load()}>
                    Tentar novamente
                  </button>
                </div>
              )}
              {notice && (
                <div className="notice" role="status">
                  {notice}
                  <button
                    className="icon"
                    aria-label="Fechar aviso"
                    onClick={() => setNotice('')}
                  >
                    <X size={15} />
                  </button>
                </div>
              )}
              {isAgenda && (
                <section className="overview" aria-label="Resumo">
                  <div className="metric">
                    <span className="metric-icon">
                      <SendHorizontal size={21} />
                    </span>
                    <div>
                      <p>Agendamentos ativos</p>
                      <strong>{busy || error ? '—' : count}</strong>
                    </div>
                  </div>
                  <div className="channel">
                    <MessageCircle size={23} />
                    <div>
                      <strong>
                        {online
                          ? 'WhatsApp pronto para envio'
                          : 'WhatsApp aguardando vínculo'}
                      </strong>
                      <p>
                        {online
                          ? 'Os envios usam o horário de Manaus.'
                          : 'Vincule seu número para ativar envios.'}
                      </p>
                    </div>
                  </div>
                </section>
              )}
              <section
                className="panel"
                aria-label={
                  isAgenda ? 'Mensagens agendadas' : 'Lista de contatos'
                }
              >
                <div className="panel-head">
                  <h2>{isAgenda ? 'Mensagens' : 'Lista de contatos'}</h2>
                  <button
                    type="button"
                    className="icon"
                    title="Atualizar"
                    disabled={busy}
                    aria-label="Atualizar lista"
                    onClick={() => {
                      setNotice('')
                      void load()
                    }}
                  >
                    <RefreshCw size={17} />
                  </button>
                </div>
                <div className="tools">
                  {isAgenda && (
                    <div className="tabs" aria-label="Filtrar por situação">
                      {(Object.keys(tabs) as (keyof typeof tabs)[]).map((f) => (
                        <button
                          key={f}
                          aria-pressed={filter === f}
                          onClick={() => setFilter(f)}
                        >
                          {tabs[f]}
                        </button>
                      ))}
                    </div>
                  )}
                  <label className="search">
                    <Search size={16} />
                    <input
                      aria-label="Buscar"
                      placeholder={
                        isAgenda ? 'Buscar mensagem' : 'Buscar contato'
                      }
                      value={query}
                      onChange={(e) => setQuery(e.target.value)}
                    />
                  </label>
                </div>
                {busy ? (
                  <p role="status" className="blank">
                    Carregando…
                  </p>
                ) : error ? (
                  <p className="blank">Lista indisponível.</p>
                ) : isAgenda && visible.length ? (
                  <ul className="rows">
                    {visible.map((s) => (
                      <li key={s.id} className="schedule-row">
                        <div className="when">
                          <strong>{formatTime(s.scheduled_at)}</strong>
                          <span>{formatDay(s.scheduled_at)}</span>
                          <span>#{s.id.slice(0, 8)}</span>
                        </div>
                        <div className="message">
                          <strong>{s.recipient.name}</strong>
                          {filter === 'review' && (
                            <small>{s.recipient.phone}</small>
                          )}
                          <p>{s.text}</p>
                          <small>
                            {s.status === 'scheduled' && !s.armed
                              ? 'Salva · precisa ativação'
                              : states[s.status]}
                            {s.recurrence === 'daily'
                              ? ' · Diária'
                              : s.recurrence === 'weekly'
                                ? ' · Semanal'
                                : ''}
                          </small>
                          {s.result_detail && <p>{s.result_detail}</p>}
                        </div>
                        {['scheduled', 'failed', 'needs_review'].includes(
                          s.status,
                        ) && (
                          <div className="row-actions">
                            <button
                              className="text-button"
                              onClick={() =>
                                setEditor({ kind: 'reschedule', item: s })
                              }
                            >
                              {s.armed ? 'Remarcar' : 'Ativar envio'}
                            </button>
                            <button
                              className="text-button"
                              onClick={() =>
                                setEditor({ kind: 'cancel', item: s })
                              }
                            >
                              Cancelar
                            </button>
                          </div>
                        )}
                      </li>
                    ))}
                  </ul>
                ) : !isAgenda && filteredContacts.length ? (
                  <ul className="rows">
                    {filteredContacts.map((c) => (
                      <li className="person" key={c.id}>
                        <span className="person-mark">
                          <ContactRound size={18} />
                        </span>
                        <div>
                          <strong>{c.name}</strong>
                          <p>{c.phone}</p>
                        </div>
                        <button
                          type="button"
                          className="text-button remove-person"
                          aria-label={`Excluir contato ${c.name}, ${c.phone}`}
                          onClick={() =>
                            setEditor({ kind: 'delete-contact', contact: c })
                          }
                        >
                          Excluir contato
                        </button>
                      </li>
                    ))}
                  </ul>
                ) : (
                  <div className="empty">
                    <span className="empty-icon">
                      {isAgenda ? (
                        <CalendarClock size={26} />
                      ) : (
                        <Users size={26} />
                      )}
                    </span>
                    <h3>
                      {query
                        ? 'Nenhum resultado'
                        : isAgenda
                          ? filter === 'scheduled'
                            ? 'Nenhuma mensagem agendada'
                            : filter === 'cancelled'
                              ? 'Nenhuma mensagem cancelada'
                              : 'Nenhuma mensagem nesta situação'
                          : 'Nenhum contato cadastrado'}
                    </h3>
                    {!query && (!isAgenda || filter === 'scheduled') && (
                      <button className="text-button" onClick={primaryAction}>
                        {isAgenda ? 'Agendar mensagem' : 'Adicionar contato'}
                        <ArrowUpRight size={15} />
                      </button>
                    )}
                  </div>
                )}
              </section>
            </>
          )}
        </div>
      </main>
      {editor?.kind === 'profile' && (
        <ProfileDialog csrf={session.csrf_token} profile={profile} onClose={() => setEditor(null)}
          onSave={(updated) => { setProfile(updated); setEditor(null); setNotice('Perfil salvo.') }} />
      )}
      {editor?.kind === 'contact' && (
        <ContactDialog
          csrf={session.csrf_token}
          onClose={() => {
            setEditor(null)
            setStartingSchedule(false)
          }}
          onSave={(c) => {
            setContacts((list) => [...list.filter((x) => x.id !== c.id), c])
            setNotice('Contato salvo.')
            setEditor(startingSchedule ? { kind: 'schedule' } : null)
            setStartingSchedule(false)
          }}
        />
      )}
      {editor?.kind === 'delete-contact' && (
        <DeleteContactDialog
          contact={editor.contact}
          csrf={session.csrf_token}
          onClose={() => setEditor(null)}
          onDelete={() => {
            setContacts((list) =>
              list.filter((c) => c.id !== editor.contact.id),
            )
            setNotice('Contato excluído.')
            setEditor(null)
            void load(true)
          }}
        />
      )}
      {editor?.kind === 'schedule' && (
        <ScheduleDialog
          contacts={contacts}
          csrf={session.csrf_token}
          onClose={() => setEditor(null)}
          onSave={saveSchedule}
        />
      )}
      {editor?.kind === 'reschedule' && (
        <RescheduleDialog
          csrf={session.csrf_token}
          item={editor.item}
          onClose={() => setEditor(null)}
          onSave={saveSchedule}
        />
      )}
      {editor?.kind === 'cancel' && (
        <CancelDialog
          csrf={session.csrf_token}
          item={editor.item}
          name={editor.item.recipient.name}
          onClose={() => setEditor(null)}
          onSave={saveSchedule}
        />
      )}
    </div>
  )
}
