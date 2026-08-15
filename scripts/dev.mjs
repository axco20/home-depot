import { existsSync } from 'node:fs'
import { spawn } from 'node:child_process'

const apiExecutable = '.venv/bin/uvicorn'

if (!existsSync(apiExecutable)) {
  console.error('Backend environment missing. Run: python3 -m venv .venv && .venv/bin/pip install -r requirements.txt')
  process.exit(1)
}

const children = [
  spawn(apiExecutable, ['backend.main:app', '--reload', '--host', '127.0.0.1', '--port', '8000'], {
    stdio: 'inherit',
  }),
  spawn(process.execPath, ['node_modules/vite/bin/vite.js'], {
    stdio: 'inherit',
  }),
]

let stopping = false

function stop(code = 0) {
  if (stopping) return
  stopping = true
  process.exitCode = code
  for (const child of children) {
    if (!child.killed) child.kill('SIGTERM')
  }
}

for (const child of children) {
  child.on('error', (error) => {
    console.error(error.message)
    stop(1)
  })
  child.on('exit', (code, signal) => {
    if (!stopping && (code !== 0 || signal)) stop(code ?? 1)
  })
}

process.on('SIGINT', () => stop())
process.on('SIGTERM', () => stop())

