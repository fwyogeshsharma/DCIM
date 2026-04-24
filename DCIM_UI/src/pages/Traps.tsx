import { useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { Activity, AlertTriangle, CheckCircle, RefreshCw, Wifi } from 'lucide-react'
import type { SNMPTrap } from '@/lib/types'

const severityColor: Record<string, string> = {
  critical: 'text-red-400 bg-red-400/10 border-red-500/30',
  warning:  'text-yellow-400 bg-yellow-400/10 border-yellow-500/30',
  info:     'text-blue-400 bg-blue-400/10 border-blue-500/30',
}

const severityDot: Record<string, string> = {
  critical: 'bg-red-400',
  warning:  'bg-yellow-400',
  info:     'bg-blue-400',
}

function timeAgo(ts: string) {
  const diff = Math.floor((Date.now() - new Date(ts).getTime()) / 1000)
  if (diff < 60) return `${diff}s ago`
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`
  return new Date(ts).toLocaleDateString()
}

export default function Traps() {
  const [filter, setFilter] = useState<'all' | 'active' | 'resolved'>('all')

  const { data: traps = [], isLoading, dataUpdatedAt, refetch } = useQuery({
    queryKey: ['snmp-traps', filter],
    queryFn: () => api.getSNMPTraps({
      resolved: filter === 'all' ? undefined : filter === 'resolved',
      limit: 200,
    }),
    refetchInterval: 5000,
  })

  const critical = traps.filter(t => t.severity?.toLowerCase() === 'critical' && !t.resolved).length
  const active   = traps.filter(t => !t.resolved).length

  return (
    <div className="p-6 space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h1 className="text-2xl font-bold text-white">SNMP Traps</h1>
          <p className="text-slate-400 text-sm mt-1">Live trap events from network devices</p>
        </div>
        <div className="flex items-center gap-3">
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <span className="relative flex h-2 w-2">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-green-400 opacity-75"></span>
              <span className="relative inline-flex rounded-full h-2 w-2 bg-green-400"></span>
            </span>
            Live · updated {dataUpdatedAt ? timeAgo(new Date(dataUpdatedAt).toISOString()) : '—'}
          </div>
          <button
            onClick={() => refetch()}
            className="p-2 rounded-lg bg-slate-800 hover:bg-slate-700 text-slate-400 hover:text-white transition-colors"
          >
            <RefreshCw size={16} />
          </button>
        </div>
      </div>

      {/* Stats */}
      <div className="grid grid-cols-3 gap-4">
        {[
          { label: 'Total', value: traps.length, icon: Wifi, color: 'text-slate-300' },
          { label: 'Active', value: active, icon: AlertTriangle, color: 'text-yellow-400' },
          { label: 'Critical', value: critical, icon: Activity, color: 'text-red-400' },
        ].map(s => (
          <div key={s.label} className="bg-slate-900 border border-slate-700/50 rounded-xl p-4 flex items-center gap-4">
            <s.icon className={`${s.color} shrink-0`} size={22} />
            <div>
              <div className={`text-2xl font-bold ${s.color}`}>{s.value}</div>
              <div className="text-slate-400 text-xs">{s.label}</div>
            </div>
          </div>
        ))}
      </div>

      {/* Filter tabs */}
      <div className="flex gap-2">
        {(['all', 'active', 'resolved'] as const).map(f => (
          <button
            key={f}
            onClick={() => setFilter(f)}
            className={`px-4 py-1.5 rounded-lg text-sm font-medium transition-colors capitalize ${
              filter === f
                ? 'bg-blue-600 text-white'
                : 'bg-slate-800 text-slate-400 hover:text-white'
            }`}
          >
            {f}
          </button>
        ))}
      </div>

      {/* Trap list */}
      <div className="bg-slate-900 border border-slate-700/50 rounded-xl overflow-hidden">
        {isLoading ? (
          <div className="p-8 text-center text-slate-500">Loading traps...</div>
        ) : traps.length === 0 ? (
          <div className="p-8 text-center text-slate-500">No traps found</div>
        ) : (
          <div className="divide-y divide-slate-800">
            {traps.map((trap: SNMPTrap) => {
              const sev = trap.severity?.toLowerCase() || 'info'
              return (
                <div key={trap.id} className="px-5 py-4 hover:bg-slate-800/50 transition-colors">
                  <div className="flex items-start gap-4">
                    {/* Severity dot */}
                    <div className="mt-1.5 shrink-0">
                      <span className={`inline-block w-2 h-2 rounded-full ${severityDot[sev] || 'bg-slate-400'}`} />
                    </div>

                    {/* Main content */}
                    <div className="flex-1 min-w-0">
                      <div className="flex items-center gap-2 flex-wrap">
                        <span className="text-white font-medium text-sm">
                          {trap.device_name || trap.source_ip}
                        </span>
                        <span className="text-slate-500 text-xs">{trap.source_ip}</span>
                        <span className={`px-2 py-0.5 rounded text-xs font-medium border ${severityColor[sev] || 'text-slate-400 bg-slate-800 border-slate-600'}`}>
                          {trap.severity?.toUpperCase()}
                        </span>
                        {trap.resolved && (
                          <span className="flex items-center gap-1 text-green-400 text-xs">
                            <CheckCircle size={12} /> resolved
                          </span>
                        )}
                      </div>
                      <div className="text-slate-300 text-sm mt-1">{trap.trap_type}</div>
                      {trap.description && (
                        <div className="text-slate-500 text-xs mt-0.5 truncate">{trap.description}</div>
                      )}
                      <div className="text-slate-600 text-xs mt-1 font-mono">{trap.trap_oid}</div>
                    </div>

                    {/* Right side */}
                    <div className="text-right shrink-0">
                      <div className="text-slate-400 text-xs">{timeAgo(trap.timestamp)}</div>
                      {trap.server_name && (
                        <div className="text-slate-600 text-xs mt-1">{trap.server_name}</div>
                      )}
                    </div>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
