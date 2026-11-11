import { expect } from '@playwright/test'
import { test } from './panel-fixture'

test('cria no mês, posiciona eventos simultâneos na semana e edita no dia', async ({
  page,
  panel,
}) => {
  void panel
  await page
    .getByRole('button', { name: 'Agenda pessoal', exact: true })
    .click()
  await page.getByLabel('Data de referência').fill('2031-01-13')
  const month = page.getByLabel('Calendário mensal')
  await expect(month).toBeVisible()
  await month
    .getByRole('button', {
      name: /Novo compromisso em segunda-feira, 13 de janeiro/,
    })
    .click()
  const modal = page.getByRole('dialog')
  await expect(modal.getByLabel('Início')).toHaveValue('2031-01-13T09:00')
  await modal.getByLabel('Assunto').fill('Revisão do projeto')
  await modal.getByRole('button', { name: 'Salvar', exact: true }).click()
  await expect(
    month.getByRole('button', { name: /09:00 · Revisão do projeto/ }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Semana', exact: true }).click()
  const week = page.getByLabel('Calendário semanal')
  await week
    .getByRole('button', {
      name: /Criar em segunda-feira, 13 de janeiro.*às 10:00/,
    })
    .click()
  await expect(modal.getByLabel('Início')).toHaveValue('2031-01-13T10:00')
  await modal.getByLabel('Início').fill('2031-01-13T09:00')
  await modal.getByLabel('Assunto').fill('Reunião da equipe')
  await modal.getByRole('button', { name: 'Salvar', exact: true }).click()
  const cards = week.locator('button.calendar-event.timed')
  await expect(cards).toHaveCount(2)
  const first = await cards.nth(0).boundingBox(),
    second = await cards.nth(1).boundingBox()
  expect(first!.y).toBeCloseTo(second!.y, 0)
  expect(Math.abs(first!.x - second!.x)).toBeGreaterThan(20)
  await week.getByRole('button', { name: 'Seg 13', exact: true }).click()
  const day = page.getByLabel('Calendário diário')
  await day.getByRole('button', { name: /09:00 · Revisão do projeto/ }).click()
  await modal.getByRole('button', { name: 'Editar compromisso' }).click()
  await modal.getByLabel('Assunto').fill('Revisão final')
  await modal.getByRole('button', { name: 'Salvar', exact: true }).click()
  await expect(day.getByRole('button', { name: /Revisão final/ })).toBeVisible()
  await day.getByRole('button', { name: /Novo item de dia inteiro em/ }).click()
  await expect(modal.getByLabel('Dia inteiro', { exact: true })).toBeChecked()
  await modal.getByLabel('Assunto').fill('Visita técnica')
  await modal.getByRole('button', { name: 'Salvar', exact: true }).click()
  await expect(
    day.locator('.all-day').getByRole('button', { name: /Visita técnica/ }),
  ).toBeVisible()
  await page
    .getByRole('button', { name: 'Conectar iCloud', exact: true })
    .click()
  await expect(
    modal.getByRole('link', { name: 'Abrir Conta Apple' }),
  ).toHaveAttribute('href', 'https://account.apple.com/')
  await expect(modal.getByLabel('Senha específica do app')).toHaveAttribute(
    'type',
    'password',
  )
  await modal.getByRole('button', { name: 'Fechar' }).click()
  await page.setViewportSize({ width: 390, height: 844 })
  await page.getByRole('button', { name: 'Mês', exact: true }).click()
  await expect(
    month.getByRole('button', { name: /Visita técnica/ }),
  ).toBeVisible()
  expect(
    await page.evaluate(() => document.documentElement.scrollWidth),
  ).toBeLessThanOrEqual(390)
})
