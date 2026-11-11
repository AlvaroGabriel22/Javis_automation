import { useRef, useState, type FormEvent } from 'react'
import { Dialog } from '../components/Dialog'
import { ApiError, errorText, request } from '../api/client'
import { localMinute } from '../time'
import { dayLabel, money, parseCents, type Entry } from './finance-data'

export type FinanceEdit =
  | { kind: 'entry'; entry: Entry }
  | { kind: 'reserve'; cents: number }
  | { kind: 'note' }

export function FinanceEditor({
  edit,
  month,
  csrf,
  binding,
  close,
  saved,
  refresh,
}: {
  edit: FinanceEdit
  month: string
  csrf: string
  binding: string
  close: () => void
  saved: (message: string) => void
  refresh: () => void
}) {
  const title = {
    entry: 'Corrigir valor',
    reserve: 'Reserva do mês',
    note: 'Nova anotação',
  }[edit.kind]
  const now = localMinute(new Date()).slice(0, 10)
  const initialDay = now.startsWith(month) ? now : month + '-01'
  const initialAmount =
    edit.kind === 'entry'
      ? edit.entry.amount_cents
      : edit.kind === 'reserve'
        ? edit.cents
        : null
  const [busy, setBusy] = useState(false)
  const [conflict, setConflict] = useState(false)
  const [error, setError] = useState('')
  const key = useRef({ fingerprint: '', value: '' })
  const saving = useRef(false)
  async function submit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault()
    if (saving.current || conflict) return
    const data = new FormData(e.currentTarget)
    saving.current = true
    setBusy(true)
    setError('')
    try {
      let message: string
      if (edit.kind === 'note') {
        const body = {
          day: String(data.get('day')),
          description: String(data.get('description')).trim(),
          quantity: String(data.get('quantity') || '').trim(),
        }
        if (!body.description) throw new Error('Informe o item ou anotação.')
        const fingerprint = JSON.stringify(body)
        if (key.current.fingerprint !== fingerprint)
          key.current = { fingerprint, value: crypto.randomUUID() }
        await request('/finance/notes', {
          method: 'POST',
          csrf,
          binding,
          body,
          key: key.current.value,
        })
        message = 'Anotação registrada em ' + dayLabel(body.day) + '.'
      } else {
        const amount_cents = parseCents(
          String(data.get('amount')),
          edit.kind === 'reserve',
        )
        if (edit.kind === 'entry') {
          await request(
            '/finance/entries/' + encodeURIComponent(edit.entry.id),
            {
              method: 'PATCH',
              csrf,
              binding,
              body: { amount_cents, expected_version: edit.entry.version },
            },
          )
          message = 'Valor corrigido.'
        } else {
          await request('/finance/reserve', {
            method: 'PUT',
            csrf,
            binding,
            body: { month, amount_cents },
          })
          message = 'Reserva do mês salva.'
        }
      }
      saved(message)
    } catch (e) {
      if (
        e instanceof ApiError &&
        e.status === 409 &&
        (edit.kind === 'entry' ||
          e.code === 'finance_binding_changed' ||
          e.code === 'finance_unbound')
      ) {
        setConflict(true)
        refresh()
      }
      setError(errorText(e))
    } finally {
      saving.current = false
      setBusy(false)
    }
  }
  return (
    <Dialog title={title} onClose={close} busy={busy}>
      <form onSubmit={submit}>
        {edit.kind === 'entry' && (
          <p>
            {edit.entry.description || 'Sem descrição'} ·{' '}
            {dayLabel(edit.entry.day)} · {money(edit.entry.amount_cents)}
          </p>
        )}
        {edit.kind === 'reserve' && (
          <p>
            O valor reservado fica fora da margem de gastos em{' '}
            {month.split('-').reverse().join('/')}.
          </p>
        )}
        {edit.kind === 'note' ? (
          <>
            <label htmlFor="note-description">Item ou anotação</label>
            <input
              id="note-description"
              name="description"
              maxLength={240}
              required
              autoFocus
            />
            <label htmlFor="note-quantity">
              Quantidade <small>opcional</small>
            </label>
            <input
              id="note-quantity"
              name="quantity"
              maxLength={40}
              placeholder="Ex. 2 caixas"
            />
            <label htmlFor="note-day">Data</label>
            <input
              id="note-day"
              name="day"
              type="date"
              defaultValue={initialDay}
              required
            />
          </>
        ) : (
          <>
            <label htmlFor="finance-amount">Valor em reais</label>
            <input
              id="finance-amount"
              name="amount"
              type="text"
              inputMode="decimal"
              required
              maxLength={24}
              defaultValue={((initialAmount ?? 0) / 100)
                .toFixed(2)
                .replace('.', ',')}
              autoFocus
            />
          </>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        {conflict && (
          <p>Confira os dados atualizados antes de tentar novamente.</p>
        )}
        <div className="actions">
          <button
            type="button"
            className="quiet"
            onClick={close}
            disabled={busy}
          >
            Voltar
          </button>
          <button className="primary" disabled={busy || conflict}>
            {busy ? 'Salvando…' : 'Salvar'}
          </button>
        </div>
      </form>
    </Dialog>
  )
}
