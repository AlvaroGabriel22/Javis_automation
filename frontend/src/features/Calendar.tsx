import { useCallback, useEffect, useRef, useState } from 'react'
import { CalendarDays, Plus } from 'lucide-react'
import { request, errorText } from '../api/client'
import { GoogleCalendarLink } from './GoogleCalendarLink'
import { CalendarGrid } from './CalendarGrid'
import { ICloudCalendarLink } from './ICloudCalendarLink'
import { Dialog } from '../components/Dialog'
import { formatDay, formatTime, localMinute, isoFromManaus } from '../time'

export type Item = {
  id: string
  kind: 'event' | 'reminder'
  title: string
  starts_at: string
  ends_at: string
  master_start: string
  master_end: string
  all_day: boolean
  recurrence: string
  alert_minutes: number | null
  state: string
  detail: string | null
  version: number
  source_id: string | null
  copies?: { source_id: string; state: string; detail: string | null }[]
}
export type Source = {
  provider: 'google' | 'icloud'
  id: string
  name: string
  selected: boolean
  write_default: boolean
  chat_target: boolean
  writable: boolean
  available: boolean
  last_sync: string | null
  error: string | null
}
type Routines = {
  morning: boolean
  evening: boolean
  birthday: string | null
  leap_day: string
}
type Settings = {
  icloud: { account: string | null; error: string | null }
  sources: Source[]
  authorized: boolean
  error: string | null
  conflicts: { title: string; detail: string }[]
  routines: Routines
}
export type Period = 'day' | 'week' | 'month'
const today = () => localMinute(new Date()).slice(0, 10)
const periods: Record<Period, string> = {
  day: 'Dia',
  week: 'Semana',
  month: 'Mês',
}
function advance(value: string, period: Period, direction: number) {
  const date = new Date(value + 'T12:00:00Z')
  if (period === 'month') {
    date.setUTCDate(1)
    date.setUTCMonth(date.getUTCMonth() + direction)
  } else
    date.setUTCDate(date.getUTCDate() + direction * (period === 'week' ? 7 : 1))
  return date.toISOString().slice(0, 10)
}
function when(item: Item) {
  const start = formatDay(item.starts_at)
  const end = formatDay(
    item.all_day
      ? new Date(Date.parse(item.ends_at) - 1).toISOString()
      : item.ends_at,
  )
  if (item.all_day)
    return (start === end ? start : start + ' a ' + end) + ' · Dia inteiro'
  const first = start + ' · ' + formatTime(item.starts_at)
  if (item.kind === 'reminder') return first
  return (
    first +
    ' – ' +
    (start === end ? '' : end + ' · ') +
    formatTime(item.ends_at)
  )
}
function EventEditor({
  item,
  seed,
  initialAllDay,
  sources,
  csrf,
  close,
  saved,
}: {
  item: Item | null
  seed: string
  initialAllDay: boolean
  sources: Source[]
  csrf: string
  close: () => void
  saved: (notice: string) => void
}) {
  const [kind, setKind] = useState<Item['kind']>(item?.kind ?? 'event')
  const [title, setTitle] = useState(item?.title ?? '')
  const [start, setStart] = useState(
    item ? localMinute(new Date(item.starts_at)) : seed,
  )
  const [end, setEnd] = useState(
    item ? localMinute(new Date(item.ends_at)) : '',
  )
  const [allDay, setAllDay] = useState(item?.all_day ?? initialAllDay)
  const [destination, setDestination] = useState(
    sources.find((s) => s.write_default)?.id ?? '',
  )
  const [series, setSeries] = useState(false)
  const [repeat, setRepeat] = useState('none')
  const [alert, setAlert] = useState(item?.alert_minutes?.toString() ?? '')
  const [busy, setBusy] = useState(false),
    [error, setError] = useState(''),
    [deleting, setDeleting] = useState(false)
  const [key] = useState(() => crypto.randomUUID())
  const recurring = !!item && item.recurrence !== 'none'
  function scope(whole: boolean) {
    setSeries(whole)
    if (item) {
      setStart(
        localMinute(new Date(whole ? item.master_start : item.starts_at)),
      )
      setEnd(localMinute(new Date(whole ? item.master_end : item.ends_at)))
      setRepeat(whole ? item.recurrence : 'none')
    }
  }
  async function submit(remove: boolean) {
    if (busy) return
    setBusy(true)
    setError('')
    try {
      const params = new URLSearchParams({
        version: String(item?.version ?? 1),
      })
      if (recurring && !series && item) params.set('occurrence', item.starts_at)
      const path = item ? '/events/' + item.id + '?' + params : '/events'
      if (remove) {
        const r = await request<{ state: string }>(path, {
          method: 'DELETE',
          csrf,
        })
        saved(
          r.state === 'delete'
            ? 'Cancelamento aguardando sincronização.'
            : 'Item cancelado.',
        )
      } else {
        const instant = (s: string) =>
          isoFromManaus(
            allDay && kind === 'event' ? s.slice(0, 10) + 'T00:00' : s,
          )
        const body = {
          kind,
          title,
          ...(!item && kind === 'event'
            ? { source_id: destination || null }
            : {}),
          starts_at: instant(start),
          ends_at: kind === 'event' && end ? instant(end) : null,
          all_day: kind === 'event' && allDay,
          recurrence: kind === 'event' ? repeat : 'none',
          alert_minutes:
            kind === 'event' && alert !== '' ? Number(alert) : null,
        }
        const r = await request<Item & { conflicts: string[] }>(path, {
          method: item ? 'PATCH' : 'POST',
          csrf,
          body,
          key,
        })
        saved(
          (r.source_id
            ? 'Alteração registrada. Aguardando sincronização com a agenda.'
            : 'Salvo na agenda pessoal.') +
            (r.conflicts.length
              ? ' Conflito de horário com ' + r.conflicts.join(', ') + '.'
              : ''),
        )
      }
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <Dialog
      title={item ? 'Editar item da agenda' : 'Novo item da agenda'}
      onClose={close}
      busy={busy}
    >
      <form
        onSubmit={(e) => {
          e.preventDefault()
          void submit(false)
        }}
      >
        {recurring && (
          <label>
            Aplicar mudança a
            <select
              value={series ? 'series' : 'single'}
              onChange={(e) => scope(e.target.value === 'series')}
            >
              <option value="single">Esta ocorrência</option>
              <option value="series">Toda a série</option>
            </select>
          </label>
        )}
        {!item && (
          <label>
            Tipo
            <select
              value={kind}
              onChange={(e) => setKind(e.target.value as Item['kind'])}
            >
              <option value="event">Compromisso</option>
              <option value="reminder">Lembrete pessoal</option>
            </select>
          </label>
        )}
        <label>
          Assunto
          <input
            required
            maxLength={300}
            value={title}
            onChange={(e) => setTitle(e.target.value)}
          />
        </label>
        {!item && kind === 'event' && (
          <label>
            Agenda de destino
            <select
              value={destination}
              onChange={(e) => setDestination(e.target.value)}
            >
              <option value="">Somente neste computador</option>
              {sources
                .filter((s) => s.selected && s.available && s.writable)
                .map((s) => (
                  <option key={s.id} value={s.id}>
                    {s.provider === 'icloud' ? 'iCloud' : 'Google'} · {s.name}
                  </option>
                ))}
            </select>
          </label>
        )}
        {kind === 'event' && (
          <label className="cal-check">
            <input
              type="checkbox"
              checked={allDay}
              onChange={(e) => setAllDay(e.target.checked)}
            />
            Dia inteiro
          </label>
        )}
        <label>
          Início
          <input
            required
            type={allDay ? 'date' : 'datetime-local'}
            value={allDay ? start.slice(0, 10) : start}
            onChange={(e) =>
              setStart(allDay ? e.target.value + 'T00:00' : e.target.value)
            }
          />
        </label>
        {kind === 'event' && (
          <>
            <label>
              {allDay
                ? 'Fim exclusivo (dia seguinte ao último)'
                : 'Fim (opcional, duração padrão de 1 h)'}
              <input
                type={allDay ? 'date' : 'datetime-local'}
                value={allDay ? end.slice(0, 10) : end}
                onChange={(e) =>
                  setEnd(
                    e.target.value && allDay
                      ? e.target.value + 'T00:00'
                      : e.target.value,
                  )
                }
              />
            </label>
            {(!recurring || series) && (
              <label>
                Repetição
                <select
                  value={repeat}
                  onChange={(e) => setRepeat(e.target.value)}
                >
                  <option value="none">Uma vez</option>
                  <option value="daily">Diária</option>
                  <option value="weekly">Semanal</option>
                  <option value="monthly">Mensal</option>
                  {repeat === 'custom' && (
                    <option value="custom">Regra da agenda</option>
                  )}
                </select>
              </label>
            )}
            <label>
              Avisar quantos minutos antes?
              <input
                type="number"
                min="0"
                max="10080"
                value={alert}
                placeholder="Sem aviso"
                onChange={(e) => setAlert(e.target.value)}
              />
            </label>
          </>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {deleting ? (
          <div className="actions">
            <span>Confirmar cancelamento?</span>
            <button
              type="button"
              disabled={busy}
              onClick={() => void submit(true)}
            >
              Sim, cancelar
            </button>
            <button type="button" onClick={() => setDeleting(false)}>
              Voltar
            </button>
          </div>
        ) : (
          <div className="actions">
            {item && (
              <button
                type="button"
                disabled={busy}
                onClick={() => setDeleting(true)}
              >
                Cancelar item
              </button>
            )}
            <button className="primary" disabled={busy}>
              Salvar
            </button>
          </div>
        )}
      </form>
    </Dialog>
  )
}
function Preferences({
  settings,
  csrf,
  saved,
  openGoogle,
}: {
  settings: Settings
  openGoogle: () => void
  csrf: string
  saved: (s: string) => void
}) {
  const [prefs, setPrefs] = useState(settings.routines),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(false)
  const [read, setRead] = useState(
    settings.sources.filter((s) => s.selected && s.available).map((s) => s.id),
  )
  const [write, setWrite] = useState(
    settings.sources.find((s) => s.write_default && s.available && s.writable)
      ?.id ?? '',
  )
  const [chat, setChat] = useState({
    google:
      settings.sources.find(
        (s) => s.provider === 'google' && s.chat_target && s.available,
      )?.id ?? '',
    icloud:
      settings.sources.find(
        (s) => s.provider === 'icloud' && s.chat_target && s.available,
      )?.id ?? '',
  })
  const destinations = Object.values(chat).filter(Boolean)
  async function store(path: string, body: unknown) {
    setBusy(true)
    setError('')
    try {
      await request(path, { method: 'PUT', csrf, body })
      saved('Preferências salvas.')
    } catch (e) {
      setError(errorText(e))
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="calendar-settings">
      <section>
        <h3>Rotinas pessoais</h3>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void store('/calendar/routines', prefs)
          }}
        >
          <label className="cal-check">
            <input
              type="checkbox"
              checked={prefs.morning}
              onChange={(e) =>
                setPrefs({ ...prefs, morning: e.target.checked })
              }
            />
            Bom dia às 07h
          </label>
          <label className="cal-check">
            <input
              type="checkbox"
              checked={prefs.evening}
              onChange={(e) =>
                setPrefs({ ...prefs, evening: e.target.checked })
              }
            />
            Boa noite às 21h
          </label>
          <label>
            Aniversário (dia/mês)
            <input
              maxLength={5}
              pattern="[0-9]{2}/[0-9]{2}"
              placeholder="DD/MM"
              defaultValue={
                prefs.birthday?.split('-').reverse().join('/') ?? ''
              }
              onChange={(e) =>
                setPrefs({
                  ...prefs,
                  birthday: e.target.value
                    ? e.target.value.split('/').reverse().join('-')
                    : null,
                })
              }
            />
          </label>
          {prefs.birthday === '02-29' && (
            <label>
              Em anos sem 29/02
              <select
                value={prefs.leap_day}
                onChange={(e) =>
                  setPrefs({ ...prefs, leap_day: e.target.value })
                }
              >
                <option value="02-28">28 de fevereiro</option>
                <option value="03-01">1 de março</option>
              </select>
            </label>
          )}
          <button disabled={busy}>Salvar rotinas</button>
        </form>
      </section>
      <section>
        <h3>Agendas conectadas</h3>
        <button type="button" onClick={openGoogle}>
          {settings.authorized
            ? 'Gerenciar vínculo com Google'
            : 'Configurar vínculo com Google'}
        </button>
        {settings.authorized &&
          !settings.sources.some((s) => s.provider === 'google') && (
            <p>Aguardando a primeira sincronização.</p>
          )}
        {settings.sources.length > 0 && (
          <form
            onSubmit={(e) => {
              e.preventDefault()
              void store('/calendar/sources', {
                read,
                write: write || null,
                chat_targets: destinations,
              })
            }}
          >
            <fieldset>
              <legend>Agendas de leitura</legend>
              {settings.sources.map((s) => (
                <label key={s.id} className="cal-check">
                  <input
                    type="checkbox"
                    disabled={
                      !s.available ||
                      s.id === write ||
                      destinations.includes(s.id)
                    }
                    checked={
                      read.includes(s.id) ||
                      s.id === write ||
                      destinations.includes(s.id)
                    }
                    onChange={(e) =>
                      setRead(
                        e.target.checked
                          ? [...read, s.id]
                          : read.filter((k) => k !== s.id),
                      )
                    }
                  />
                  {s.provider === 'icloud' ? 'iCloud' : 'Google'} · {s.name}
                </label>
              ))}
            </fieldset>
            <label>
              Destino padrão no painel
              <select value={write} onChange={(e) => setWrite(e.target.value)}>
                <option value="">Somente neste computador</option>
                {settings.sources
                  .filter((s) => s.writable && s.available)
                  .map((s) => (
                    <option key={s.id} value={s.id}>
                      {s.provider === 'icloud' ? 'iCloud' : 'Google'} · {s.name}
                    </option>
                  ))}
              </select>
            </label>
            <fieldset>
              <legend>Destinos do WhatsApp</legend>
              <p>
                Sem destino no pedido, o Jarvis grava nos serviços conectados.
                Escolha a agenda de cada serviço ou decida pelo WhatsApp.
              </p>
              {(['google', 'icloud'] as const).map((provider) => {
                const options = settings.sources.filter(
                  (s) => s.provider === provider && s.available && s.writable,
                )
                if (!options.length) return null
                const readable = options.filter(
                  (s) => read.includes(s.id) || s.id === write,
                )
                const automatic = readable.length === 1 ? readable[0] : null
                return (
                  <label key={provider}>
                    {provider === 'google' ? 'Google' : 'iCloud'}
                    <select
                      value={chat[provider]}
                      onChange={(e) =>
                        setChat({ ...chat, [provider]: e.target.value })
                      }
                    >
                      <option value="">
                        {automatic
                          ? 'Automático · ' + automatic.name
                          : 'Perguntar no WhatsApp'}
                      </option>
                      {options.map((s) => (
                        <option key={s.id} value={s.id}>
                          {s.name}
                        </option>
                      ))}
                    </select>
                  </label>
                )
              })}
            </fieldset>
            <button disabled={busy}>Salvar agendas</button>
          </form>
        )}
      </section>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
    </div>
  )
}
export function Calendar({ csrf }: { csrf: string }) {
  const [period, setPeriod] = useState<Period>('month'),
    [anchor, setAnchor] = useState(today)
  const [items, setItems] = useState<Item[]>([]),
    [settings, setSettings] = useState<Settings | null>(null)
  const [range, setRange] = useState(''),
    [notice, setNotice] = useState(''),
    [error, setError] = useState(''),
    [busy, setBusy] = useState(true)
  const [editor, setEditor] = useState<Item | null | undefined>(undefined)
  const [seed, setSeed] = useState(''),
    [allDay, setAllDay] = useState(false)
  const [appleOpen, setAppleOpen] = useState(false),
    [detail, setDetail] = useState<Item | null>(null)
  const [linkOpen, setLinkOpen] = useState(false)
  const generation = useRef(0)
  const load = useCallback(
    async (silent = false) => {
      const id = ++generation.current
      if (!silent) setBusy(true)
      try {
        const [r, s] = await Promise.all([
          request<{ items: Item[]; start: string; end: string }>(
            '/events?period=' + period + '&anchor=' + anchor,
          ),
          request<Settings>('/calendar'),
        ])
        if (id === generation.current) {
          setItems(r.items)
          setSettings(s)
          setRange(
            period === 'month'
              ? new Date(r.start).toLocaleDateString('pt-BR', {
                  month: 'long',
                  year: 'numeric',
                  timeZone: 'America/Manaus',
                })
              : period === 'day'
                ? formatDay(r.start)
                : formatDay(r.start) +
                  ' – ' +
                  formatDay(new Date(Date.parse(r.end) - 1).toISOString()),
          )
          setError('')
        }
      } catch (e) {
        if (id === generation.current) setError(errorText(e))
      } finally {
        if (id === generation.current) setBusy(false)
      }
    },
    [anchor, period],
  )
  useEffect(() => {
    void load()
    const timer = setInterval(() => void load(true), 20000)
    return () => {
      ++generation.current
      clearInterval(timer)
    }
  }, [load])
  const refreshGoogle = useCallback(() => {
    void load(true)
  }, [load])
  const detailSource = settings?.sources.find((s) => s.id === detail?.source_id)
  function create(start: string, whole = false) {
    setSeed(start)
    setAllDay(whole)
    setEditor(null)
  }
  function saved(s: string) {
    setEditor(undefined)
    setNotice(s)
    void load(true)
  }
  return (
    <div className="wa-page calendar-page">
      <section className="headline">
        <h1>Agenda pessoal</h1>
        <div className="row-actions">
          <button
            className="quiet"
            type="button"
            onClick={() => setLinkOpen(true)}
          >
            {settings?.authorized
              ? 'Configurar Google Agenda'
              : 'Conectar Google Agenda'}
          </button>
          <button className="quiet" onClick={() => setAppleOpen(true)}>
            {settings?.icloud?.account
              ? 'Configurar iCloud'
              : 'Conectar iCloud'}
          </button>
          <button className="primary" onClick={() => create(anchor + 'T09:00')}>
            <Plus size={17} /> Novo item
          </button>
        </div>
      </section>
      {error && (
        <p className="error" role="alert">
          {error} <button onClick={() => void load()}>Tentar novamente</button>
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      <section className="panel wa-panel calendar-surface">
        <div className="panel-head">
          <h2>{busy ? 'Carregando agenda…' : range}</h2>
        </div>
        <div className="cal-tools">
          <div className="tabs">
            {(Object.keys(periods) as Period[]).map((p) => (
              <button
                key={p}
                aria-pressed={period === p}
                onClick={() => setPeriod(p)}
              >
                {periods[p]}
              </button>
            ))}
          </div>
          <button
            aria-label="Período anterior"
            onClick={() => setAnchor(advance(anchor, period, -1))}
          >
            ‹
          </button>
          <input
            aria-label="Data de referência"
            type="date"
            value={anchor}
            onChange={(e) => {
              if (e.target.value) setAnchor(e.target.value)
            }}
          />
          <button
            aria-label="Próximo período"
            onClick={() => setAnchor(advance(anchor, period, 1))}
          >
            ›
          </button>
          <button onClick={() => setAnchor(today())}>Hoje</button>
        </div>
        {!busy && !error && (
          <CalendarGrid
            period={period}
            anchor={anchor}
            items={items}
            sources={settings?.sources ?? []}
            create={create}
            open={(item) => setDetail(item)}
            showDay={(day) => {
              setAnchor(day)
              setPeriod('day')
            }}
          />
        )}
        <div className="cal-legend" aria-label="Origens dos compromissos">
          <span>Local</span>
          {settings?.authorized && <span className="google">Google</span>}
          {settings?.icloud?.account && <span className="icloud">iCloud</span>}
        </div>
      </section>
      {settings && (
        <section className="panel wa-panel">
          <div className="wa-title">
            <CalendarDays size={20} />
            <h2>Agendas e rotinas</h2>
          </div>
          {settings.icloud?.error && (
            <p className="error">{settings.icloud.error}</p>
          )}
          {settings.error && <p className="error">{settings.error}</p>}
          {settings.sources
            .filter((s) => s.selected)
            .map((s) => (
              <p key={s.id}>
                {s.name} ·{' '}
                {s.last_sync
                  ? 'Última sincronização em ' +
                    formatDay(s.last_sync) +
                    ' às ' +
                    formatTime(s.last_sync)
                  : 'Sem sincronização concluída'}
                {s.error ? ' · ' + s.error : ''}
              </p>
            ))}
          {settings.conflicts.map((r, i) => (
            <p key={i} className="error">
              {r.title} · {r.detail}
            </p>
          ))}
          <Preferences
            key={JSON.stringify([
              settings.routines,
              settings.sources.map((s) => [
                s.id,
                s.selected,
                s.write_default,
                s.chat_target,
              ]),
            ])}
            settings={settings}
            csrf={csrf}
            saved={saved}
            openGoogle={() => setLinkOpen(true)}
          />
        </section>
      )}
      {appleOpen && (
        <ICloudCalendarLink
          csrf={csrf}
          account={settings?.icloud?.account ?? null}
          close={() => setAppleOpen(false)}
          saved={(s) => {
            setAppleOpen(false)
            saved(s)
          }}
        />
      )}
      {detail && (
        <Dialog
          title={detail.title}
          busy={false}
          onClose={() => setDetail(null)}
        >
          <div className="google-flow">
            <p>{when(detail)} · Manaus</p>
            <p>
              {detail.kind === 'reminder' ? 'Lembrete' : 'Compromisso'}
              {detail.recurrence !== 'none' ? ' · Recorrente' : ''} ·{' '}
              {(detail.copies?.length ?? 0) > 1
                ? 'Google e iCloud'
                : detailSource
                  ? (detailSource.provider === 'icloud' ? 'iCloud' : 'Google') +
                    ' · ' +
                    detailSource.name
                  : 'Agenda local'}
            </p>
            {(detail.copies?.length ?? 0) > 1 &&
              detail.copies?.map((copy) => {
                const s = settings?.sources.find((s) => s.id === copy.source_id)
                return (
                  <p key={copy.source_id}>
                    {s?.provider === 'icloud' ? 'iCloud' : 'Google'} · {s?.name}{' '}
                    ·{' '}
                    {copy.detail ||
                      (copy.state === 'clean'
                        ? 'Sincronizado'
                        : 'Alteração pendente')}
                  </p>
                )
              })}
            {detail.source_id && (detail.copies?.length ?? 0) <= 1 && (
              <p>
                {detail.detail ??
                  (detail.state === 'clean'
                    ? 'Sincronizado'
                    : 'Alteração pendente')}
              </p>
            )}
            {(!detail.source_id ||
              settings?.sources.some(
                (s) => s.id === detail.source_id && s.writable && s.available,
              )) && (
              <button
                className="primary"
                onClick={() => {
                  setEditor(detail)
                  setDetail(null)
                }}
              >
                Editar compromisso
              </button>
            )}
          </div>
        </Dialog>
      )}
      {linkOpen && (
        <GoogleCalendarLink
          csrf={csrf}
          close={() => setLinkOpen(false)}
          changed={refreshGoogle}
        />
      )}
      {editor !== undefined && (
        <EventEditor
          item={editor}
          seed={seed}
          initialAllDay={allDay}
          sources={settings?.sources ?? []}
          csrf={csrf}
          close={() => setEditor(undefined)}
          saved={saved}
        />
      )}
    </div>
  )
}
