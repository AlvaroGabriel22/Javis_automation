import assert from 'node:assert/strict'
import test from 'node:test'
import { formatTime, isoFromManaus, localMinute } from '../src/time.ts'
test('um horário em Manaus se mantém independente do fuso do navegador', () => {
  const utc = isoFromManaus('2026-11-08T09:17')
  assert.equal(utc, '2026-11-08T13:17:00.000Z')
  assert.equal(formatTime(utc), '09:17')
  assert.equal(localMinute(new Date(utc)), '2026-11-08T09:17')
})
test('atravessa o ano em UTC mantendo o dia local correto', () => {
  assert.equal(isoFromManaus('2026-12-31T23:59'), '2027-01-01T03:59:00.000Z')
})
test('rejeita datas inexistentes ou incompletas', () => {
  for (const invalid of ['', '2026-02-30T09:00', '2026-11-08T25:00']) {
    assert.throws(() => isoFromManaus(invalid))
  }
})
