export interface Profile {
  preferred_name: string
  gender: string
  address_as: string
  tone: 'proximo' | 'neutro'
}
export interface Session {
  profile: Profile
  owner: { id: number; name: string }
  csrf_token: string
}
export interface Contact {
  id: string
  name: string
  phone: string
}
export interface Schedule {
  id: string
  contact_id: string
  recipient: Contact
  text: string
  scheduled_at: string
  created_at: string
  status:
    | 'scheduled'
    | 'cancelled'
    | 'accepted'
    | 'sending'
    | 'needs_review'
    | 'uncertain'
    | 'failed'
  recurrence: 'none' | 'daily' | 'weekly'
  armed: boolean
  result_detail: string | null
}
export class ApiError extends Error {
  constructor(
    readonly status: number,
    readonly code: string,
    message: string,
  ) {
    super(message)
  }
}
interface Options {
  method?: 'GET' | 'POST' | 'DELETE' | 'PATCH' | 'PUT'
  body?: unknown
  csrf?: string
  key?: string
  binding?: string
}
async function apiResponse(
  path: string,
  options: Options,
  accept: string,
): Promise<Response> {
  const headers = new Headers({ Accept: accept })
  if (options.body !== undefined)
    headers.set('Content-Type', 'application/json')
  if (options.csrf) headers.set('X-CSRF-Token', options.csrf)
  if (options.key) headers.set('Idempotency-Key', options.key)
  if (options.binding) headers.set('X-Jarvis-Binding', options.binding)
  let res: Response
  try {
    res = await fetch('/api/v1' + path, {
      method: options.method ?? 'GET',
      headers,
      credentials: 'same-origin',
      body:
        options.body === undefined ? undefined : JSON.stringify(options.body),
      signal: AbortSignal.timeout(15000),
    })
  } catch {
    throw new ApiError(
      0,
      'offline',
      'Sem resposta da API. Confira a conexão e tente novamente.',
    )
  }
  if (!res.ok) {
    const detail = (await res.json().catch(() => null))?.detail
    if (res.status === 401 && path !== '/auth/login')
      window.dispatchEvent(new Event('jarvis-session-expired'))
    throw new ApiError(
      res.status,
      detail?.code ?? 'unavailable',
      detail?.message ?? 'O serviço está indisponível. Tente novamente.',
    )
  }
  return res
}
export async function downloadFile(
  path: string,
  mime: string,
  binding: string,
): Promise<Blob> {
  const res = await apiResponse(path, { binding }, mime)
  if (res.headers.get('content-type')?.split(';')[0] !== mime)
    throw new Error('O arquivo solicitado ainda não está disponível.')
  return res.blob()
}
export async function request<T>(
  path: string,
  options: Options = {},
): Promise<T> {
  const res = await apiResponse(path, options, 'application/json')
  if (res.status === 204) return undefined as T
  const payload = await res.json().catch(() => null)
  if (payload === null)
    throw new ApiError(
      res.status,
      'invalid_response',
      'A API retornou uma resposta inválida.',
    )
  return payload as T
}
export async function readList<T>(path: string): Promise<T[]> {
  const items: T[] = []
  const seen = new Set<string>()
  let cursor: string | null = null
  do {
    const params = new URLSearchParams({ limit: '100' })
    if (cursor !== null) params.set('cursor', cursor)
    const page: { items: T[]; next_cursor: string | null } = await request(
      path + '?' + params,
    )
    items.push(...page.items)
    cursor = page.next_cursor
    if (cursor && seen.has(cursor))
      throw new Error('Não foi possível concluir a leitura da lista.')
    if (cursor) seen.add(cursor)
  } while (cursor)
  return items
}
export function errorText(e: unknown): string {
  return e instanceof Error
    ? e.message
    : 'Não foi possível concluir a operação.'
}
