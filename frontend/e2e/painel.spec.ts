import { expect } from '@playwright/test'
import { test } from './panel-fixture'

test('o dono cadastra contato, salva um envio, recarrega e cancela', async ({
  page,
  panel,
}) => {
  void panel
  await expect(
    page.getByRole('heading', { name: 'Agenda de envios' }),
  ).toBeVisible()
  await page.getByRole('button', { name: 'Meu perfil' }).click()
  await page.getByLabel('Nome que o Jarvis deve usar').fill('Lu')
  await page.getByLabel('Gênero').fill('Feminino')
  await page.getByLabel(/Forma de tratamento/).fill('Sra.')
  await expect(page.getByLabel(/Tom da conversa/)).toHaveValue('proximo')
  await page.getByLabel(/Tom da conversa/).selectOption('neutro')
  await page.getByRole('button', { name: 'Salvar perfil' }).click()
  await expect(page.getByText('Perfil salvo.')).toBeVisible()
  await page.reload()
  await page.getByRole('button', { name: 'Meu perfil' }).click()
  await expect(page.getByLabel('Nome que o Jarvis deve usar')).toHaveValue('Lu')
  await expect(page.getByLabel(/Forma de tratamento/)).toHaveValue('Sra.')
  await expect(page.getByLabel(/Tom da conversa/)).toHaveValue('neutro')
  await page.getByRole('button', { name: 'Voltar' }).click()
  await page.getByRole('button', { name: 'WhatsApp', exact: true }).click()
  await expect(
    page.getByText('Gateway desligado', { exact: true }),
  ).toBeVisible()
  await page
    .getByRole('button', { name: 'Agenda de envios', exact: true })
    .click()
  await page
    .getByRole('button', { name: 'Agendar mensagem', exact: true })
    .first()
    .click()
  await page.getByLabel('Nome', { exact: true }).fill('Contato de teste')
  await page.getByLabel('WhatsApp com código do país').fill('+5592999990001')
  await page.getByRole('button', { name: 'Salvar contato' }).click()
  await page
    .getByLabel('Destinatário')
    .selectOption({ label: 'Contato de teste · +5592999990001' })
  const year = new Date().getUTCFullYear() + 1
  await page.getByLabel('Data e hora').fill(year + '-10-13T09:17')
  await page
    .getByLabel('Mensagem', { exact: true })
    .fill('A apresentação começa às dez.')
  await page.getByRole('button', { name: 'Salvar agendamento' }).click()
  await expect(page.getByText('Agendamento salvo.')).toBeVisible()
  await page.reload()
  await expect(page.getByText('A apresentação começa às dez.')).toBeVisible()
  await expect(page.getByText('09:17', { exact: true })).toBeVisible()
  await page.getByRole('button', { name: 'Cancelar', exact: true }).click()
  await page
    .getByRole('button', { name: 'Cancelar agendamento', exact: true })
    .click()
  await page.getByRole('button', { name: 'Canceladas', exact: true }).click()
  await expect(page.getByText('A apresentação começa às dez.')).toBeVisible()
  await page.getByRole('button', { name: 'Contatos', exact: true }).click()
  const remove = page.getByRole('button', {
    name: /Excluir contato Contato de teste/,
  })
  await remove.click()
  const dialog = page.getByRole('dialog', {
    name: 'Excluir contato do Jarvis?',
  })
  await expect(
    dialog.getByText('+5592999990001', { exact: false }),
  ).toBeVisible()
  await dialog.getByRole('button', { name: 'Voltar' }).click()
  await remove.click()
  await dialog
    .getByRole('button', { name: 'Excluir contato', exact: true })
    .click()
  await expect(page.getByText('Contato excluído.')).toBeVisible()
  await expect(remove).toHaveCount(0)
  await page.reload()
  await page.getByRole('button', { name: 'Canceladas', exact: true }).click()
  await expect(
    page.getByText('Contato de teste', { exact: true }),
  ).toBeVisible()
  await page
    .getByRole('button', { name: 'Agenda pessoal', exact: true })
    .click()
  await page.getByRole('button', { name: 'Novo item' }).click()
  await page.getByLabel('Assunto').fill('Revisão dos indicadores')
  await page.getByRole('button', { name: 'Salvar', exact: true }).click()
  await expect(page.getByText('Revisão dos indicadores')).toBeVisible()
  await page.getByRole('button', { name: 'Mês', exact: true }).click()
  const grid = page.getByLabel('Calendário mensal')
  await grid.getByRole('button', { name: /Revisão dos indicadores/ }).click()
  await page
    .getByRole('button', { name: 'Editar compromisso', exact: true })
    .click()
  await page.getByRole('button', { name: 'Cancelar item' }).click()
  await page.getByRole('button', { name: 'Sim, cancelar' }).click()
  await expect(
    grid.getByRole('button', { name: /Revisão dos indicadores/ }),
  ).toHaveCount(0)
  await page.getByRole('button', { name: 'Sair', exact: true }).click()
  await expect(
    page.getByRole('heading', { name: 'Acesso ao painel' }),
  ).toBeVisible()
})
