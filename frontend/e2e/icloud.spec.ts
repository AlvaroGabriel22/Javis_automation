import { expect } from '@playwright/test'
import { test } from './panel-fixture'
test.use({
  serverScript: 'frontend/e2e/icloud-test-server.py',
  locale: 'pt-BR',
})

test('vincula iCloud e cria na agenda escolhida', async ({
  page,
  panel,
}, info) => {
  void panel
  await page
    .getByRole('button', { name: 'Agenda pessoal', exact: true })
    .click()
  await page
    .getByRole('button', { name: 'Conectar iCloud', exact: true })
    .click()
  const d = page.getByRole('dialog')
  await d.getByLabel('E-mail da Conta Apple').fill('example@example.invalid')
  await d.getByLabel('Senha específica do app').fill('senha-incorreta')
  await d.getByRole('button', { name: 'Vincular iCloud' }).click()
  await expect(d.getByRole('alert')).toContainText('recusou as credenciais')
  await d.getByLabel('Senha específica do app').fill('app-synthetic')
  await d.getByRole('button', { name: 'Vincular iCloud' }).click()
  await expect(d).not.toBeVisible()
  await expect(
    page.getByRole('button', { name: 'Conectar Google Agenda', exact: true }),
  ).toBeVisible()
  const marker = page
    .getByLabel('Origens dos compromissos')
    .getByText('iCloud', { exact: true })
  await expect(marker).toBeVisible()
  expect(
    await marker.evaluate((el) => getComputedStyle(el, '::before').backgroundColor),
  ).toBe('rgb(72, 118, 191)')
  const target = page
    .getByRole('group', { name: 'Destinos do WhatsApp' })
    .getByLabel('iCloud')
  await target.selectOption({ label: 'Pessoal' })
  await page.getByRole('button', { name: 'Salvar agendas' }).click()
  await expect(page.getByText('Preferências salvas.')).toBeVisible()
  await page.reload()
  await page
    .getByRole('button', { name: 'Agenda pessoal', exact: true })
    .click()
  await expect(target.locator('option:checked')).toHaveText('Pessoal')
  await page.getByLabel('Data de referência').fill('2031-01-13')
  const month = page.getByLabel('Calendário mensal')
  for (const [weekday, title, origin] of [
    ['segunda-feira, 13', 'Revisão do projeto', 'iCloud · Pessoal'],
    ['terça-feira, 14', 'Buscar encomenda', 'Somente neste computador'],
    ['sexta-feira, 17', 'Reunião da equipe', 'iCloud · Pessoal'],
  ]) {
    await month
      .getByRole('button', {
        name: new RegExp('Novo compromisso em ' + weekday + ' de janeiro'),
      })
      .click()
    await d.getByLabel('Assunto').fill(title)
    await d.getByLabel('Agenda de destino').selectOption({ label: origin })
    await d.getByRole('button', { name: 'Salvar', exact: true }).click()
    await expect(
      month.getByRole('button', { name: new RegExp(title) }),
    ).toBeVisible()
  }
  const screenshot = info.outputPath('calendario-mensal.png')
  await page.locator('.calendar-surface').screenshot({ path: screenshot })
  await info.attach('Calendário mensal', {
    path: screenshot,
    contentType: 'image/png',
  })
  await page.reload()
  await page
    .getByRole('button', { name: 'Agenda pessoal', exact: true })
    .click()
  await page
    .getByRole('button', { name: 'Configurar iCloud', exact: true })
    .click()
  await expect(d.getByLabel('E-mail da Conta Apple')).toHaveValue(
    'example@example.invalid',
  )
  await expect(d.getByLabel('Senha específica do app')).toBeEmpty()
})
