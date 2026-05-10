import { useEffect, useRef } from 'react'
import { useQuery } from '@tanstack/react-query'
import toast from 'react-hot-toast'
import { api } from '@/lib/api'

// Poll every 15 s; show toast for any alert newer than the last seen timestamp
const POLL_MS = 15_000
const SEVERITY_LABEL: Record<string, string> = {
  CRITICAL: '🔴 CRITICAL',
  WARNING:  '🟡 WARNING',
  INFO:     '🔵 INFO',
}

export function useAlertNotifications() {
  const lastSeenTs = useRef<string>(new Date().toISOString())

  const { data } = useQuery({
    queryKey: ['alert-notifications-poll'],
    queryFn: () => api.getAlerts({ limit: 20 }),
    refetchInterval: POLL_MS,
    staleTime: POLL_MS,
  })

  useEffect(() => {
    if (!data || !Array.isArray(data)) return

    // Find alerts newer than what we've already shown
    const fresh = data.filter((a: any) => {
      const ts = a.timestamp || a.created_at || ''
      return ts > lastSeenTs.current
    })

    if (fresh.length === 0) return

    // Advance watermark
    const newest = fresh.reduce((max: any, a: any) => {
      const ts = a.timestamp || a.created_at || ''
      return ts > (max.timestamp || max.created_at || '') ? a : max
    }, fresh[0])
    lastSeenTs.current = newest.timestamp || newest.created_at || lastSeenTs.current

    // Group by severity — show one toast per severity group, newest first
    const bySev: Record<string, any[]> = {}
    for (const a of fresh) {
      const sev = (a.severity || 'INFO').toUpperCase()
      ;(bySev[sev] = bySev[sev] || []).push(a)
    }

    for (const [sev, alerts] of Object.entries(bySev)) {
      const label  = SEVERITY_LABEL[sev] ?? sev
      const sample = alerts[0]
      const device = sample.agent_id?.split('-').slice(2).join('-') || sample.agent_id || 'device'
      const msg    = alerts.length === 1
        ? `${label}: ${device} — ${sample.metric_type || sample.message || 'alert'}`
        : `${label}: ${alerts.length} new alerts (${device} + ${alerts.length - 1} more)`

      if (sev === 'CRITICAL') {
        toast.error(msg, { duration: 8000, id: `alert-${sev}-${Date.now()}` })
      } else if (sev === 'WARNING') {
        toast(msg, {
          duration: 6000,
          id: `alert-${sev}-${Date.now()}`,
          style: { background: '#854d0e', color: '#fef9c3' },
        })
      } else {
        toast(msg, { duration: 4000, id: `alert-${sev}-${Date.now()}` })
      }
    }
  }, [data])
}
