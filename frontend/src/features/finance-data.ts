export interface Entry {
  id: string
  day: string
  kind: 'expense' | 'income'
  description: string
  amount_cents: number
  version: number
}

export interface Note {
  id: string
  day: string
  description: string
  quantity: string
}

export interface ExportStatus {
  state: 'idle' | 'ready' | 'pending' | 'error'
  detail: string | null
}

export interface FinanceView {
  month: string
  bound: boolean
  binding_token: string | null
  totals: {
    expense_cents: number
    income_cents: number
    budget_cents: number | null
    reserve_cents: number
    available_cents: number | null
  }
  entries: Entry[]
  notes: Note[]
  sheets: {
    excel: ExportStatus
    google: ExportStatus
    excel_available: boolean
    google_configured: boolean
    google_enabled: boolean
  }
}

const currency = new Intl.NumberFormat('pt-BR', {
  style: 'currency',
  currency: 'BRL',
})
export const money = (cents: number | null) =>
  cents === null ? 'Sem limite definido' : currency.format(cents / 100)

export const dayLabel = (day: string) => day.split('-').reverse().join('/')

export function parseCents(input: string, allowZero: boolean): number {
  const value = input.trim()
  if (!/^(?:\d+|\d{1,3}(?:\.\d{3})+)(?:,\d{1,2})?$/.test(value))
    throw new Error('Informe o valor em reais, por exemplo 42,50.')
  const [units, fraction = ''] = value.replaceAll('.', '').split(',')
  const cents = Number(units + fraction.padEnd(2, '0'))
  if (
    !Number.isSafeInteger(cents) ||
    cents > 99_999_999_999 ||
    (!allowZero && cents === 0)
  )
    throw new Error('Informe um valor válido para este registro.')
  return cents
}
