import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Download,
  FileSpreadsheet,
  Pencil,
  Plus,
  RefreshCw,
} from 'lucide-react'
import { ApiError, downloadFile, errorText, request } from '../api/client'
import { localMinute } from '../time'
import { FinanceEditor, type FinanceEdit } from './FinanceEditor'
import {
  dayLabel,
  money,
  type ExportStatus,
  type FinanceView,
} from './finance-data'
import './finance.css'

type Target = 'excel' | 'google'
const stateLabels: Record<ExportStatus['state'], string> = {
  idle: 'Ainda sem exportação',
  error: 'Precisa de atenção',
  pending: 'Aguardando sincronização',
  ready: 'Atualizada',
}
const xlsx = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'

export function Finance({
  csrf,
  openWhatsApp,
}: {
  csrf: string
  openWhatsApp: () => void
}) {
  const [month, setMonth] = useState(() => localMinute(new Date()).slice(0, 7))
  const [data, setData] = useState<FinanceView | null>(null)
  const [fetching, setFetching] = useState(false)
  const [loadError, setLoadError] = useState('')
  const [error, setError] = useState('')
  const [notice, setNotice] = useState('')
  const [action, setAction] = useState('')
  const actionLock = useRef(false)
  const generation = useRef(0)
  const [edit, setEdit] = useState<{
    form: FinanceEdit
    binding: string
  } | null>(null)
  const load = useCallback(async () => {
    const current = ++generation.current
    setFetching(true)
    try {
      const view = await request<FinanceView>(
        '/finance?month=' + encodeURIComponent(month),
      )
      if (current === generation.current) {
        setData(view)
        setLoadError('')
      }
    } catch (e) {
      if (current === generation.current) {
        setLoadError(errorText(e))
        setData(null)
      }
    } finally {
      if (current === generation.current) setFetching(false)
    }
  }, [month])
  useEffect(() => {
    setData(null)
    setLoadError('')
    setNotice('')
    setError('')
    void load()
    const interval = window.setInterval(() => {
      if (document.visibilityState === 'visible') void load()
    }, 9000)
    return () => {
      ++generation.current
      window.clearInterval(interval)
    }
  }, [load])

  useEffect(() => {
    if (edit && data && data.binding_token !== edit.binding) {
      setEdit(null)
      setError(
        'O vínculo do WhatsApp mudou. Confira os dados antes de abrir outra edição.',
      )
    }
  }, [edit, data])

  function openEditor(form: FinanceEdit) {
    if (data?.binding_token) setEdit({ form, binding: data.binding_token })
  }
  async function act(name: string, operation: () => Promise<void>) {
    if (actionLock.current) return
    actionLock.current = true
    setAction(name)
    setError('')
    setNotice('')
    try {
      await operation()
    } catch (e) {
      if (
        e instanceof ApiError &&
        ['finance_binding_changed', 'finance_unbound'].includes(e.code)
      )
        await load()
      setError(errorText(e))
    } finally {
      actionLock.current = false
      setAction('')
    }
  }
  function sync(target: Target) {
    const binding = data?.binding_token
    if (!binding) return
    void act(target, async () => {
      await request('/finance/exports/' + target, {
        method: 'POST',
        csrf,
        binding,
      })
      setNotice('Solicitação de atualização registrada.')
      await load()
    })
  }
  function toggle(enabled: boolean) {
    const binding = data?.binding_token
    if (!binding) return
    void act('toggle', async () => {
      await request('/finance/exports/google', {
        method: 'PUT',
        csrf,
        binding,
        body: { enabled },
      })
      setNotice(
        enabled
          ? 'Sincronização com Google Sheets ativada.'
          : 'Sincronização com Google Sheets desativada.',
      )
      await load()
    })
  }
  function download() {
    const binding = data?.binding_token
    if (!binding) return
    void act('download', async () => {
      const blob = await downloadFile(
        '/finance/exports/excel/file',
        xlsx,
        binding,
      )
      const url = URL.createObjectURL(blob)
      const a = document.createElement('a')
      a.href = url
      a.download = 'jarvis.xlsx'
      a.click()
      window.setTimeout(() => URL.revokeObjectURL(url), 1000)
    })
  }
  function status(s: ExportStatus) {
    return (
      <p className={'finance-state finance-state-' + s.state}>
        {stateLabels[s.state]}
        {s.detail && ' · ' + s.detail}
      </p>
    )
  }
  return (
    <div className="finance-page">
      <header className="headline">
        <h1>Finanças e planilhas</h1>
        <label className="finance-month">
          Mês de referência
          <input
            type="month"
            value={month}
            min="0002-01"
            max="9998-12"
            disabled={!!action}
            onChange={(e) => {
              if (/^\d{4}-(?:0[1-9]|1[0-2])$/.test(e.target.value))
                setMonth(e.target.value)
            }}
          />
        </label>
      </header>
      {loadError && (
        <div className="error" role="alert">
          {loadError}{' '}
          <button className="text-button" onClick={() => void load()}>
            Tentar novamente
          </button>
        </div>
      )}
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {notice && (
        <p className="notice" role="status">
          {notice}
        </p>
      )}
      {!data && fetching && <p role="status">Carregando finanças…</p>}
      {data && !data.bound && (
        <section className="panel finance-unbound">
          <h2>Vincule o WhatsApp</h2>
          <p>
            Os lançamentos, as anotações e as planilhas ficam associados ao
            vínculo do proprietário.
          </p>
          <button type="button" className="quiet" onClick={openWhatsApp}>
            Abrir WhatsApp
          </button>
        </section>
      )}
      {data?.bound && (
        <>
          <dl className="finance-summary" aria-label="Resumo do mês">
            <div>
              <dt>Gastos</dt>
              <dd>{money(data.totals.expense_cents)}</dd>
            </div>
            <div>
              <dt>Receitas</dt>
              <dd>{money(data.totals.income_cents)}</dd>
            </div>
            <div>
              <dt>Reserva</dt>
              <dd>{money(data.totals.reserve_cents)}</dd>
              <button
                className="text-button"
                onClick={() =>
                  openEditor({
                    kind: 'reserve',
                    cents: data.totals.reserve_cents,
                  })
                }
              >
                Ajustar reserva
              </button>
            </div>
            <div className="finance-margin">
              <dt>Margem no orçamento</dt>
              <dd
                className={
                  data.totals.available_cents === null
                    ? 'finance-no-budget'
                    : ''
                }
              >
                {money(data.totals.available_cents)}
              </dd>
              {data.totals.budget_cents !== null && (
                <small>Limite de {money(data.totals.budget_cents)}</small>
              )}
            </div>
          </dl>
          <section className="panel" aria-label="Lançamentos do mês">
            <div className="panel-head">
              <h2>Lançamentos</h2>
              <button
                className="icon"
                aria-label="Atualizar lançamentos"
                onClick={() => void load()}
                disabled={fetching}
              >
                <RefreshCw size={17} />
              </button>
            </div>
            {data.entries.length === 0 ? (
              <p className="finance-empty">Nenhum lançamento neste mês.</p>
            ) : (
              <div className="finance-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Data</th>
                      <th>Descrição</th>
                      <th>Tipo</th>
                      <th className="finance-value">Valor</th>
                      <th>
                        <span className="finance-sr">Ações</span>
                      </th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.entries.map((entry) => (
                      <tr key={entry.id}>
                        <td>{dayLabel(entry.day)}</td>
                        <td>{entry.description || 'Sem descrição'}</td>
                        <td>
                          {entry.kind === 'expense' ? 'Despesa' : 'Receita'}
                        </td>
                        <td className="finance-value">
                          {money(entry.amount_cents)}
                        </td>
                        <td>
                          <button
                            className="icon"
                            title="Corrigir valor"
                            aria-label={
                              'Corrigir valor de ' +
                              (entry.description || entry.id)
                            }
                            onClick={() => openEditor({ kind: 'entry', entry })}
                          >
                            <Pencil size={16} />
                          </button>
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section className="panel" aria-label="Anotações do mês">
            <div className="panel-head">
              <h2>Anotações da planilha</h2>
              <button
                className="quiet"
                onClick={() => openEditor({ kind: 'note' })}
              >
                <Plus size={16} />
                Nova anotação
              </button>
            </div>
            {data.notes.length === 0 ? (
              <p className="finance-empty">Nenhuma anotação neste mês.</p>
            ) : (
              <div className="finance-table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th>Data</th>
                      <th>Item ou anotação</th>
                      <th>Quantidade</th>
                    </tr>
                  </thead>
                  <tbody>
                    {data.notes.map((note) => (
                      <tr key={note.id}>
                        <td>{dayLabel(note.day)}</td>
                        <td>{note.description}</td>
                        <td>{note.quantity || '—'}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </section>
          <section className="panel" aria-label="Planilhas vinculadas">
            <div className="panel-head">
              <h2>Planilhas</h2>
            </div>
            <div className="finance-destinations">
              <section aria-label="Excel">
                <div>
                  <h3>
                    <FileSpreadsheet size={19} />
                    Excel
                  </h3>
                  {status(data.sheets.excel)}
                </div>
                <div className="finance-actions">
                  <button
                    className="quiet"
                    disabled={!!action}
                    onClick={() => sync('excel')}
                  >
                    <RefreshCw size={15} />
                    {action === 'excel' ? 'Solicitando…' : 'Atualizar Excel'}
                  </button>
                  {data.sheets.excel_available && (
                    <button
                      className="primary"
                      disabled={!!action}
                      onClick={download}
                    >
                      <Download size={15} />
                      {action === 'download' ? 'Baixando…' : 'Baixar Excel'}
                    </button>
                  )}
                </div>
              </section>
              <section aria-label="Google Sheets">
                <div>
                  <h3>
                    <FileSpreadsheet size={19} />
                    Google Sheets
                  </h3>
                  {data.sheets.google_enabled ? (
                    status(data.sheets.google)
                  ) : (
                    <p className="finance-state">
                      {data.sheets.google_configured
                        ? 'Configurado · sincronização desativada'
                        : 'Aguardando configuração no computador'}
                    </p>
                  )}
                  {!data.sheets.google_configured && (
                    <details>
                      <summary>Como configurar</summary>
                      <p>
                        Compartilhe a planilha como editor com a conta de
                        serviço. No computador do Jarvis, defina{' '}
                        <code>JARVIS_GOOGLE_SHEETS_CREDENTIALS</code> com o
                        caminho do arquivo da conta de serviço e{' '}
                        <code>JARVIS_GOOGLE_SHEETS_ID</code> com o ID da
                        planilha no <code>.env</code>. Consulte{' '}
                        <code>docs/PLANILHAS.md</code>. Reinicie o Jarvis e
                        ative a sincronização aqui.
                      </p>
                    </details>
                  )}
                </div>
                <div className="finance-actions">
                  {data.sheets.google_enabled && (
                    <button
                      className="quiet"
                      disabled={!!action || !data.sheets.google_configured}
                      onClick={() => sync('google')}
                    >
                      <RefreshCw size={15} />
                      Atualizar Sheets
                    </button>
                  )}
                  {(data.sheets.google_configured ||
                    data.sheets.google_enabled) && (
                    <button
                      className="quiet"
                      disabled={!!action}
                      onClick={() => toggle(!data.sheets.google_enabled)}
                    >
                      {data.sheets.google_enabled
                        ? 'Desativar Google Sheets'
                        : 'Ativar Google Sheets'}
                    </button>
                  )}
                </div>
              </section>
            </div>
          </section>
        </>
      )}
      {edit && data?.binding_token === edit.binding && (
        <FinanceEditor
          edit={edit.form}
          binding={edit.binding}
          month={month}
          csrf={csrf}
          close={() => setEdit(null)}
          refresh={() => void load()}
          saved={(message) => {
            setEdit(null)
            setNotice(message)
            void load()
          }}
        />
      )}
    </div>
  )
}
