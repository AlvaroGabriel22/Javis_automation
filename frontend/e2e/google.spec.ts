import { expect } from '@playwright/test'
import { test } from './panel-fixture'

test.use({ serverScript: 'frontend/e2e/google-test-server.py' })
test('vincula uma agenda pelo painel e permite repetir o consentimento cancelado', async ({
  page,
  context,
  panel,
}) => {
  void panel
  let authorized = false
  await context.route('https://accounts.google.com/**', async (route) => {
    const query = new URL(route.request().url()).searchParams
    expect(query.get('code_challenge_method')).toBe('S256')
    const callback = new URL(query.get('redirect_uri')!)
    expect(callback.hostname).toBe('127.0.0.1')
    callback.searchParams.set('state', query.get('state')!)
    if (authorized) callback.searchParams.set('code', 'test-code')
    else callback.searchParams.set('error', 'access_denied')
    await route.fulfill({
      contentType: 'text/html',
      body: '<a href="' + callback.href + '">Continuar</a>',
    })
  })
  await page
    .getByRole('button', { name: 'Agenda pessoal', exact: true })
    .click()
  await page
    .getByRole('button', { name: 'Conectar Google Agenda', exact: true })
    .click()
  const dialog = page.getByRole('dialog')
  await dialog.getByText('Como obter o arquivo no Google Cloud?').click()
  await expect(
    dialog.getByRole('link', { name: 'Google Calendar API' }),
  ).toBeVisible()
  const picker = dialog.getByLabel('Importar arquivo JSON')
  await picker.setInputFiles({
    name: 'web.json',
    mimeType: 'application/json',
    buffer: Buffer.from('{"web":{}}'),
  })
  await expect(dialog.getByRole('alert')).toContainText(
    'Aplicativo para computador',
  )
  await picker.setInputFiles({
    name: 'desktop.json',
    mimeType: 'application/json',
    buffer: Buffer.from(
      JSON.stringify({
        installed: { client_id: 'test', client_secret: 'synthetic' },
      }),
    ),
  })
  await expect(dialog.getByText('Credencial pronta para uso.')).toBeVisible()
  await dialog.getByText('O Google mostrou “Acesso bloqueado” (403)?').click()
  await expect(dialog.getByRole('link', { name: 'Público-alvo no Google Cloud' }))
    .toHaveAttribute('href', 'https://console.cloud.google.com/auth/audience')
  async function consent() {
    const opened = context.waitForEvent('page')
    await dialog
      .getByRole('button', { name: 'Autorizar no Google', exact: true })
      .click()
    const popup = await opened
    await popup.getByRole('link', { name: 'Continuar' }).click()
    await expect(popup.getByText(/Volte à aba do Jarvis/)).toBeVisible()
    await popup.close()
  }
  await consent()
  await expect(dialog.getByRole('alert')).toContainText('usuários de teste')
  authorized = true
  await consent()
  await expect(dialog.getByText('agenda@example.invalid')).toBeVisible()
  await dialog.getByRole('button', { name: 'Voltar à agenda' }).click()
  await page.reload()
  await page
    .getByRole('button', { name: 'Agenda pessoal', exact: true })
    .click()
  const marker = page
    .getByLabel('Origens dos compromissos')
    .getByText('Google', { exact: true })
  await expect(marker).toBeVisible()
  expect(
    await marker.evaluate((el) => getComputedStyle(el, '::before').backgroundColor),
  ).toBe('rgb(38, 131, 76)')
  await page
    .getByRole('button', { name: 'Configurar Google Agenda', exact: true })
    .click()
  await expect(page.getByText('agenda@example.invalid')).toBeVisible()
})
