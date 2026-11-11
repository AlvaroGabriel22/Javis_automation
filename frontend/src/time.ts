export const zone = 'America/Manaus'
const wall = new Intl.DateTimeFormat('sv-SE', {
  timeZone: zone,
  year: 'numeric',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hourCycle: 'h23',
})
export function localMinute(date: Date): string {
  return wall.format(date).replace(' ', 'T')
}
export function isoFromManaus(value: string): string {
  if (!/^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}$/.test(value))
    throw new Error('Informe uma data e um horário válidos.')
  const target = Date.parse(value + ':00Z')
  if (!Number.isFinite(target)) throw new Error('Data inválida.')
  const localAtTarget = Date.parse(localMinute(new Date(target)) + ':00Z')
  const result = new Date(target + target - localAtTarget)
  if (localMinute(result) !== value)
    throw new Error('Horário inválido no fuso de Manaus.')
  return result.toISOString()
}
export const formatDay = (s: string) =>
  new Intl.DateTimeFormat('pt-BR', {
    timeZone: zone,
    day: '2-digit',
    month: 'short',
    year: 'numeric',
  }).format(new Date(s))
export const formatTime = (s: string) =>
  new Intl.DateTimeFormat('pt-BR', {
    timeZone: zone,
    hour: '2-digit',
    minute: '2-digit',
    hourCycle: 'h23',
  }).format(new Date(s))
