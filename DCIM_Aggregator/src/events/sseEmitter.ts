import { Response } from 'express'

let nextId = 0
const clients = new Map<number, Response>()

export function addSSEClient(res: Response): number {
  const id = nextId++
  res.setHeader('Content-Type', 'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection', 'keep-alive')
  res.setHeader('X-Accel-Buffering', 'no')
  res.flushHeaders()
  res.write(':ok\n\n')
  clients.set(id, res)
  return id
}

export function removeSSEClient(id: number) {
  clients.delete(id)
}

export function emitSSEEvent(eventType: string, data: unknown) {
  const payload = `event: ${eventType}\ndata: ${JSON.stringify(data)}\n\n`
  for (const [id, res] of clients) {
    try {
      res.write(payload)
    } catch {
      clients.delete(id)
    }
  }
}
