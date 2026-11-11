import { expect, test as base } from '@playwright/test'
import { spawn, execFileSync } from 'node:child_process'
import { mkdtemp, rm } from 'node:fs/promises'
import { createServer } from 'node:net'
import { tmpdir } from 'node:os'
import { join, resolve } from 'node:path'
import { setTimeout as delay } from 'node:timers/promises'
import { once } from 'node:events'

export const test = base.extend<{
  serverScript: string
  simulatedSheets: boolean
  panel: void
}>({
  serverScript: ['scripts/panel.py', { option: true }],
  simulatedSheets: [false, { option: true }],
  panel: async ({ page, request, serverScript, simulatedSheets }, use) => {
    test.setTimeout(60000)
    const data = await mkdtemp(join(tmpdir(), 'jarvis-e2e-'))
    const root = resolve(import.meta.dirname, '../..')
    const python = join(root, 'backend/.venv/bin/python')
    const env = {
      ...process.env,
      XDG_DATA_HOME: data,
      JARVIS_E2E_SHEETS: simulatedSheets ? '1' : '0',
      JARVIS_GOOGLE_CLIENT_FILE: join(data, 'desktop.json'),
    }
    const portFinder = createServer()
    portFinder.listen(0, '127.0.0.1')
    await once(portFinder, 'listening')
    const address = portFinder.address()
    if (!address || typeof address === 'string')
      throw new Error('Sem porta disponível')
    const port = address.port
    await new Promise<void>((resolve, reject) =>
      portFinder.close((e) => (e ? reject(e) : resolve())),
    )
    const url = 'http://127.0.0.1:' + port
    const server = spawn(
      python,
      [serverScript, 'serve', '--demo', '--port', String(port)],
      {
        cwd: root,
        env,
        stdio: 'ignore',
      },
    )
    try {
      await expect
        .poll(
          async () => {
            if (server.exitCode !== null)
              throw new Error('O painel encerrou antes de iniciar')
            return (
              await request.get(url + '/api/v1/status').catch(() => undefined)
            )?.status()
          },
          { timeout: 20000 },
        )
        .toBe(200)
      const code = execFileSync(
        python,
        ['scripts/panel.py', 'login-code', '--demo'],
        { cwd: root, env, encoding: 'utf8', stdio: ['ignore', 'pipe', 'pipe'] },
      ).trim()
      await page.goto(url)
      await page.getByLabel('Código de acesso').fill(code)
      await page.getByRole('button', { name: 'Entrar no painel' }).click()
      await use()
    } finally {
      if (server.exitCode === null && server.signalCode === null) {
        const closed = once(server, 'exit')
        server.kill('SIGTERM')
        await Promise.race([closed, delay(3000)])
        if (server.exitCode === null && server.signalCode === null)
          server.kill('SIGKILL')
      }
      await rm(data, { recursive: true, force: true })
    }
  },
})
