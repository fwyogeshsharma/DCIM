import { useEffect, useRef, useState } from 'react'

export interface LiveTrap {
  server_id: string
  source_ip: string
  device_name: string
  trap_type: string
  trap_oid: string
  severity: string
  description: string
  timestamp: string
  varbinds: Record<string, unknown>
  resolved: boolean
}

// VITE_AGGREGATOR_URL already ends with /api/v1 — just append the path segment
const AGGREGATOR_URL = (import.meta.env.VITE_AGGREGATOR_URL as string | undefined) || '/api/v1'

function trapKey(t: { server_id: string; source_ip: string; trap_type: string }): string {
  return `${t.server_id}|${t.source_ip}|${t.trap_type}`
}

/**
 * Subscribes to the aggregator's real-time trap SSE stream.
 * Returns the current set of active (unresolved) traps, updated instantly on each event.
 */
export function useActiveTrapStream(): LiveTrap[] {
  const [traps, setTraps] = useState<LiveTrap[]>([])
  const esRef = useRef<EventSource | null>(null)

  useEffect(() => {
    const url = `${AGGREGATOR_URL}/traps/stream`
    const es = new EventSource(url)
    esRef.current = es

    es.addEventListener('init', (e: MessageEvent) => {
      try {
        setTraps(JSON.parse(e.data) as LiveTrap[])
      } catch { /* ignore */ }
    })

    es.addEventListener('trap_event', (e: MessageEvent) => {
      try {
        const trap = JSON.parse(e.data) as LiveTrap
        const key = trapKey(trap)
        setTraps(prev => {
          const idx = prev.findIndex(t => trapKey(t) === key)
          if (idx >= 0) {
            const next = [...prev]
            next[idx] = trap
            return next
          }
          return [trap, ...prev]
        })
      } catch { /* ignore */ }
    })

    es.addEventListener('trap_resolve', (e: MessageEvent) => {
      try {
        const { server_id, source_ip, trap_type } = JSON.parse(e.data) as {
          server_id: string; source_ip: string; trap_type: string
        }
        const key = `${server_id}|${source_ip}|${trap_type}`
        setTraps(prev => prev.filter(t => trapKey(t) !== key))
      } catch { /* ignore */ }
    })

    return () => {
      es.close()
      esRef.current = null
    }
  }, [])

  return traps
}
