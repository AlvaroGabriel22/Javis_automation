import { useEffect, useRef, type CSSProperties } from 'react'
import { formatTime, localMinute } from '../time'
import type { Item, Source, Period } from './Calendar'
import './calendar-grid.css'

const weekdays = ['Seg', 'Ter', 'Qua', 'Qui', 'Sex', 'Sáb', 'Dom']
const hourLabel = (hour: number) => String(hour).padStart(2, '0') + ':00'
const utcDay = (d: Date) => d.toISOString().slice(0, 10)
const dateOf = (s: string) => new Date(s + 'T12:00:00Z')
const fullDay = (s: string) =>
  dateOf(s).toLocaleDateString('pt-BR', { timeZone: 'UTC', dateStyle: 'full' })
const minutes = (s: string) =>
  Number(s.slice(11, 13)) * 60 + Number(s.slice(14, 16))

function daysFor(anchor: string, period: Period) {
  const first = dateOf(anchor)
  if (period === 'month') first.setUTCDate(1)
  const offset = (first.getUTCDay() + 6) % 7
  const last = new Date(first)
  last.setUTCMonth(last.getUTCMonth() + 1, 0)
  const count =
    period === 'month'
      ? 7 * Math.ceil((last.getUTCDate() + offset) / 7)
      : period === 'week'
        ? 7
        : 1
  if (period !== 'day') first.setUTCDate(first.getUTCDate() - offset)
  return Array.from({ length: count }, (_, i) =>
    utcDay(new Date(+first + i * 86400000)),
  )
}

type Block = {
  item: Item
  continuation: boolean
  start: number
  end: number
  lane: number
  columns: number
}

function blocks(items: Item[], day: string): Block[] {
  return items.flatMap((item) => {
    const first = localMinute(new Date(item.starts_at)),
      end = localMinute(new Date(item.ends_at))
    const last = localMinute(
      new Date(
        Math.max(Date.parse(item.starts_at), Date.parse(item.ends_at) - 1),
      ),
    ).slice(0, 10)
    if (first.slice(0, 10) > day || last < day) return []
    const start = first.slice(0, 10) < day ? 0 : minutes(first)
    const finish = end.slice(0, 10) > day ? 1440 : minutes(end)
    return [
      {
        item,
        continuation: first.slice(0, 10) < day,
        start,
        end: Math.min(1440, Math.max(start + 24, finish)),
        lane: 0,
        columns: 1,
      },
    ]
  })
}

function position(events: Block[]) {
  const lanes: number[] = []
  let group: Block[] = [],
    until = -1
  function finish() {
    for (const e of group) e.columns = lanes.length
    group = []
    lanes.length = 0
  }
  for (const e of events.sort((a, b) => a.start - b.start || b.end - a.end)) {
    if (e.start >= until) finish()
    let lane = lanes.findIndex((end) => end <= e.start)
    if (lane < 0) lane = lanes.length
    lanes[lane] = e.end
    e.lane = lane
    group.push(e)
    until = Math.max(until, e.end)
  }
  finish()
  return events
}

type Props = {
  anchor: string
  period: Period
  items: Item[]
  sources: Source[]
  create: (start: string, allDay?: boolean) => void
  open: (item: Item) => void
  showDay: (day: string) => void
}

export function CalendarGrid({
  anchor,
  period,
  items,
  sources,
  create,
  open,
  showDay,
}: Props) {
  const days = daysFor(anchor, period)
  const today = localMinute(new Date()).slice(0, 10)
  const scroll = useRef<HTMLDivElement>(null)
  useEffect(() => {
    if (scroll.current && period !== 'month') scroll.current.scrollTop = 7 * 52
  }, [period, anchor])
  function event(b: Block, timed = false) {
    const { item } = b
    const source = sources.find((s) => s.id === item.source_id)
    const origin =
      (item.copies?.length ?? 0) > 1
        ? 'Google e iCloud'
        : source
          ? (source.provider === 'icloud' ? 'iCloud' : 'Google') +
            ' · ' +
            source.name
          : 'Local'
    const style: CSSProperties | undefined = timed
      ? {
          top: (b.start / 1440) * 100 + '%',
          height: ((b.end - b.start) / 1440) * 100 + '%',
          left: (b.lane / b.columns) * 100 + '%',
          width: 100 / b.columns + '%',
        }
      : undefined
    const time = item.all_day
      ? 'Dia inteiro'
      : b.continuation
        ? 'Continuação'
        : formatTime(item.starts_at)
    return (
      <button
        key={item.id + item.starts_at}
        type="button"
        style={style}
        className={
          'calendar-event ' +
          ((item.copies?.length ?? 0) > 1 ? 'paired' : (source?.provider ?? 'local')) +
          (timed ? ' timed' : '')
        }
        title={time + ' · ' + item.title + ' · ' + origin}
        aria-label={time + ' · ' + item.title + ' · ' + origin}
        onClick={() => open(item)}
      >
        {!item.all_day && <b>{time}</b>} <span>{item.title}</span>
      </button>
    )
  }
  if (period === 'month')
    return (
      <div className="month-scroll">
        <div className="month-grid" aria-label="Calendário mensal">
          {weekdays.map((name) => (
            <div className="week-label" key={name}>
              {name}
            </div>
          ))}
          {days.map((day) => {
            const outside = day.slice(0, 7) !== anchor.slice(0, 7)
            const entries = outside
              ? []
              : blocks(items, day).sort(
                  (a, b) =>
                    Number(b.item.all_day) - Number(a.item.all_day) ||
                    a.start - b.start,
                )
            return (
              <div
                key={day}
                className={
                  'month-cell' +
                  (outside ? ' outside' : '') +
                  (day === today ? ' current' : '')
                }
              >
                <button
                  type="button"
                  className="cell-add"
                  aria-label={'Novo compromisso em ' + fullDay(day)}
                  onClick={() => create(day + 'T09:00')}
                />
                <button
                  type="button"
                  className="day-number"
                  aria-label={'Ver ' + fullDay(day)}
                  onClick={() => showDay(day)}
                >
                  {Number(day.slice(-2))}
                </button>
                {entries.slice(0, 3).map((b) => event(b))}
                {entries.length > 3 && (
                  <button className="more-events" onClick={() => showDay(day)}>
                    +{entries.length - 3} mais
                  </button>
                )}
              </div>
            )
          })}
        </div>
      </div>
    )
  return (
    <div
      className="schedule-scroll"
      ref={scroll}
      aria-label={
        period === 'week' ? 'Calendário semanal' : 'Calendário diário'
      }
    >
      <div
        className={'schedule ' + period}
        style={{ '--days': days.length } as CSSProperties}
      >
        <div className="schedule-sticky">
          <div className="schedule-header">
            <small>Manaus</small>
            {days.map((day) => (
              <button
                key={day}
                className={'day-heading' + (day === today ? ' current' : '')}
                onClick={() => showDay(day)}
              >
                {weekdays[(dateOf(day).getUTCDay() + 6) % 7]}{' '}
                <b>{Number(day.slice(-2))}</b>
              </button>
            ))}
          </div>
          <div className="all-day">
            <small>Dia inteiro</small>
            {days.map((day) => (
              <div key={day}>
                <button
                  className="cell-add"
                  aria-label={'Novo item de dia inteiro em ' + fullDay(day)}
                  onClick={() => create(day + 'T00:00', true)}
                />
                {blocks(items, day)
                  .filter((b) => b.item.all_day)
                  .map((b) => event(b))}
              </div>
            ))}
          </div>
        </div>
        <div className="hour-grid">
          <div className="hour-labels">
            {Array.from({ length: 24 }, (_, h) => (
              <small key={h}>{hourLabel(h)}</small>
            ))}
          </div>
          {days.map((day) => (
            <div
              key={day}
              className={'hour-column' + (day === today ? ' current' : '')}
            >
              {Array.from({ length: 24 }, (_, h) => (
                <button
                  key={h}
                  className="hour-slot"
                  aria-label={
                    'Criar em ' + fullDay(day) + ' às ' + hourLabel(h)
                  }
                  onClick={() => create(day + 'T' + hourLabel(h))}
                />
              ))}
              {position(blocks(items, day).filter((b) => !b.item.all_day)).map(
                (b) => event(b, true),
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  )
}
