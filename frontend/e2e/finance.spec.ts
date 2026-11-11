import { expect } from '@playwright/test'
import { execFileSync } from 'node:child_process'
import { test } from './panel-fixture'

test.use({ serverScript: 'frontend/e2e/finance-test-server.py' })

test('corrige uma despesa, protege a reserva e baixa a anotação no Excel', async ({
  page,
  panel,
}, info) => {
  void panel
  await page.getByRole('button', { name: 'Finanças e planilhas' }).click()
  await page.getByLabel('Mês de referência').fill('2031-02')
  const entries = page.getByRole('region', { name: 'Lançamentos do mês' })
  await expect(entries.getByText(/mercado/i)).toBeVisible()
  await expect(entries.getByText(/aeroporto/i)).toHaveCount(0)
  await entries
    .getByRole('button', { name: /Corrigir valor de .*mercado/i })
    .click()
  const dialog = page.getByRole('dialog')
  await dialog.getByLabel('Valor em reais').fill('42,35')
  await dialog.getByRole('button', { name: 'Salvar' }).click()
  await expect(entries.getByText(/42,35/)).toBeVisible()
  await page.getByRole('button', { name: 'Ajustar reserva' }).click()
  await dialog.getByLabel('Valor em reais').fill('100,00')
  await dialog.getByRole('button', { name: 'Salvar' }).click()
  await expect(page.getByLabel('Resumo do mês')).toContainText('357,65')
  await page.getByRole('button', { name: 'Nova anotação' }).click()
  await dialog.getByLabel('Item ou anotação').fill('Papel reciclado')
  await dialog.getByLabel('Quantidade').fill('2 pacotes')
  await dialog.getByLabel('Data', { exact: true }).fill('2031-02-12')
  await dialog.getByRole('button', { name: 'Salvar' }).click()
  const notes = page.getByRole('region', { name: 'Anotações do mês' })
  await expect(notes.getByText('Papel reciclado')).toBeVisible()
  await expect(notes.getByText('2 pacotes')).toBeVisible()
  await page.getByRole('button', { name: 'Atualizar Excel' }).click()
  const excel = page.getByRole('region', { name: 'Excel', exact: true })
  await expect(async () => {
    await page.getByRole('button', { name: 'Atualizar lançamentos' }).click()
    await expect(excel.getByText('Atualizada', { exact: false })).toBeVisible({
      timeout: 900,
    })
  }).toPass({ timeout: 12000 })
  const download = page.waitForEvent('download')
  await excel.getByRole('button', { name: 'Baixar Excel' }).click()
  const saved = info.outputPath('financas.xlsx')
  await (await download).saveAs(saved)
  const content = execFileSync(
    'python3',
    [
      '-c',
      'from zipfile import ZipFile; from xml.etree.ElementTree import fromstring; import sys\n' +
        'with ZipFile(sys.argv[1]) as z:\n' +
        ' for path in z.namelist():\n' +
        '  if path.startswith("xl/worksheets/sheet") or path == "xl/sharedStrings.xml":\n' +
        '   print(" ".join(fromstring(z.read(path)).itertext()))',
      saved,
    ],
    { encoding: 'utf8' },
  )
  expect(content).toContain('Papel reciclado')
  expect(content.toLowerCase().match(/mercado/g)).toHaveLength(1)
  expect(content).toContain('42.35')
  expect(content.toLowerCase()).not.toContain('aeroporto')
  const google = page.getByRole('region', { name: 'Google Sheets' })
  await expect(google).toContainText('Aguardando configuração')
  await google.getByText('Como configurar').click()
  await expect(google).toContainText('JARVIS_GOOGLE_SHEETS_ID')
  await page.setViewportSize({ width: 390, height: 844 })
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390)
  await page.reload()
  await page.getByRole('button', { name: 'Finanças e planilhas' }).click()
  await page.getByLabel('Mês de referência').fill('2031-02')
  await expect(entries.getByText(/42,35/)).toBeVisible()
  await expect(notes.getByText('Papel reciclado')).toBeVisible()
})

test.describe('Google Sheets', () => {
  test.use({ simulatedSheets: true })

  test('mostra a falha do provedor e permite sincronizar novamente', async ({
    page,
    panel,
  }) => {
    void panel
    await page.getByRole('button', { name: 'Finanças e planilhas' }).click()
    const google = page.getByRole('region', { name: 'Google Sheets' })
    const refresh = page.getByRole('button', { name: 'Atualizar lançamentos' })
    await google
      .getByRole('button', { name: 'Ativar Google Sheets', exact: true })
      .click()
    await expect(async () => {
      await refresh.click()
      await expect(google).toContainText('Acesso temporariamente recusado', {
        timeout: 900,
      })
    }).toPass({ timeout: 8000 })
    await google.getByRole('button', { name: 'Atualizar Sheets' }).click()
    await expect(async () => {
      await refresh.click()
      await expect(google).toContainText('Atualizada', { timeout: 900 })
    }).toPass({ timeout: 8000 })
    await page.reload()
    await page.getByRole('button', { name: 'Finanças e planilhas' }).click()
    await google
      .getByRole('button', { name: 'Desativar Google Sheets' })
      .click()
    await expect(google).toContainText('sincronização desativada')
  })
})
