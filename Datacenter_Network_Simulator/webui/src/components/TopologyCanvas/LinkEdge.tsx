import { memo, useState, useCallback } from 'react'
import { createPortal } from 'react-dom'
import { useStore } from '../../store/useStore'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react'
import { LAYER_COLOR, LAYER_DEFAULT, FLOW_COLD, FLOW_HOT } from '../../theme'

// Re-exported under their historical names for existing importers. The values
// live in index.css :root; see src/theme.ts.
export const COOL_COLD = FLOW_COLD   // chilled-water supply / cooled condenser return
export const COOL_HOT  = FLOW_HOT    // warm return / hot condenser water

// Classify a cooling edge as hot (warm return / condenser-hot) vs cold
// (chilled supply / cooled return) from the device names at its endpoints.
export function isHotFlow(src: string, dst: string): boolean {
  const s = src.toUpperCase(), t = dst.toUpperCase()
  if (s.startsWith('CRAH')) return true            // CHW return: CRAH → CHWR
  if (s.includes('CHWR') || t.includes('CHWR')) return true
  if (s.includes('CWP') || t.includes('CWP')) return true   // condenser-water pump (hot loop)
  if (s.includes('CWR') || t.includes('CWR')) return true   // condenser-water RETURN (hot)
  if (/^CT\d/.test(t)) return true                 // to a cooling tower (CT1/CT2…) = hot
  if (s.startsWith('VCW') || t.startsWith('VCW')) return true   // condenser-water (VCW) valve
  if (t.startsWith('CDU') && s.startsWith('SRV')) return true   // cold-plate return: server → CDU (hot)
  return false                                     // chilled supply / cooled return = cold
}

export interface LinkEdgeData {
  layer: string
  broken: boolean
  src_iface?: number | null
  dst_iface?: number | null
  // Power layer only — a cord's terminations. Null everywhere else.
  outlet?: number | null
  psu?: number | null
  srcName?: string
  dstName?: string
  flow?: 'hot' | 'cold'
  standby?: boolean          // cooling loop through a staged-off train (no flow)
  [key: string]: unknown
}

function LinkEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY,
          sourcePosition, targetPosition, data, selected, source, target,
          markerEnd } = props
  const d      = (data ?? {}) as LinkEdgeData
  const broken = Boolean(d.broken)
  const layer  = String(d.layer || 'production')

  // Name + hot/cold classification are precomputed in linksToEdges and carried
  // in edge data — so this edge does NOT subscribe to the device store and will
  // not re-render on every metrics tick.
  const srcName = d.srcName || source
  const dstName = d.dstName || target

  const isCool = layer === 'cooling'
  // A cooling loop through a staged-off train carries no flow — draw it dim and
  // static (no flow animation) so it reads as idle. Live per-edge selector, but a
  // stable boolean, so cooling edges re-render only when standby actually flips —
  // never on the per-tick metrics churn.
  const standby = useStore(s => isCool && (s.plantStandby.includes(source) || s.plantStandby.includes(target)))
  const hot    = isCool && d.flow === 'hot'
  const color  = broken ? 'var(--crit)'
               : isCool  ? (hot ? COOL_HOT : COOL_COLD)
               : (LAYER_COLOR[layer] || LAYER_DEFAULT)

  const [tipPos, setTipPos] = useState<{ x: number; y: number } | null>(null)
  const onEnter = useCallback((e: React.MouseEvent<SVGPathElement>) =>
    setTipPos({ x: e.clientX + 14, y: e.clientY + 14 }), [])
  const onMove  = useCallback((e: React.MouseEvent<SVGPathElement>) =>
    setTipPos({ x: e.clientX + 14, y: e.clientY + 14 }), [])
  const onLeave = useCallback(() => setTipPos(null), [])

  // Cooling supply & return run between the same node pair — offset each
  // perpendicular (opposite signs by direction) so both draw as parallel
  // lines instead of overlapping into one.
  let sx = sourceX, sy = sourceY, tx = targetX, ty = targetY
  if (isCool) {
    const off = source < target ? 8 : -8
    const dx = targetX - sourceX, dy = targetY - sourceY
    const len = Math.hypot(dx, dy) || 1
    const ox = -dy / len * off, oy = dx / len * off
    sx += ox; sy += oy; tx += ox; ty += oy
  }
  const [edgePath] = getBezierPath({
    sourceX: sx, sourceY: sy, sourcePosition,
    targetX: tx, targetY: ty, targetPosition,
  })

  const statusColor = broken ? 'var(--crit)' : 'var(--ok)'
  const statusText  = broken ? 'Broken'  : 'Active'

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        markerEnd={markerEnd}
        style={{
          stroke: color,
          strokeWidth: selected ? 2.5 : (isCool ? 2.4 : 1.5),
          strokeDasharray: broken ? '5,3' : (isCool ? '7,5' : undefined),
          // Animate dashes along source→target = water flow direction. A standby
          // loop is NOT flowing, so it stays static.
          animation: (isCool && !broken && !standby) ? 'flow-dash 0.7s linear infinite' : undefined,
          opacity: broken ? 0.6 : standby ? 0.28 : (layer === 'management' ? 0.7 : 1),
        }}
      />

      {/* Wide invisible hit area for reliable hover detection */}
      <path
        d={edgePath}
        stroke="transparent"
        strokeWidth={20}
        fill="none"
        onMouseEnter={onEnter}
        onMouseMove={onMove}
        onMouseLeave={onLeave}
      />

      {broken && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%,-50%) translate(${(sourceX + targetX) / 2}px,${(sourceY + targetY) / 2}px)`,
              background: 'var(--crit-solid-bg)',
              color: 'var(--crit-fill-fg)',
              fontSize: 8,
              padding: '1px 4px',
              borderRadius: 3,
              pointerEvents: 'none',
            }}
            className="nodrag nopan"
          >
            DOWN
          </div>
        </EdgeLabelRenderer>
      )}

      {tipPos && createPortal(
        <div style={{
          position: 'fixed',
          left: tipPos.x,
          top: tipPos.y,
          zIndex: 9999,
          background: 'var(--bg-panel)',
          border: '1px solid var(--border)',
          borderRadius: 6,
          padding: '8px 10px',
          boxShadow: '0 8px 24px rgba(0,0,0,0.8)',
          minWidth: 180,
          pointerEvents: 'none',
          fontFamily: 'system-ui, -apple-system, sans-serif',
        }}>
          <div style={{
            fontWeight: 700, color: 'var(--text)', fontSize: 12,
            marginBottom: 6, paddingBottom: 4,
            borderBottom: '1px solid var(--border)',
          }}>
            {srcName} ↔ {dstName}
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 10.5, lineHeight: 1.85 }}>
            <span style={{ color: statusColor, fontSize: 14, lineHeight: 1 }}>●</span>
            <span style={{ color: 'var(--text-muted)' }}>Status:</span>
            <span style={{ color: statusColor }}>{statusText}</span>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 10.5, lineHeight: 1.85 }}>
            <span style={{ color: 'var(--text-muted)' }}>Layer:</span>
            <span style={{ color: 'var(--text)', textTransform: 'capitalize' }}>{layer}</span>
          </div>
          {(d.src_iface != null || d.dst_iface != null) && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 10.5, lineHeight: 1.85 }}>
              <span style={{ color: 'var(--text-muted)' }}>Interfaces:</span>
              <span style={{ color: 'var(--text)', fontFamily: 'Consolas, monospace' }}>
                {d.src_iface ?? '?'} ↔ {d.dst_iface ?? '?'}
              </span>
            </div>
          )}
          {/* A power cord: outlet → PSU. Read left-to-right as the power FLOWS,
              which is why it uses the arrow the interfaces row does not. */}
          {d.outlet != null && (
            <div style={{ display: 'flex', justifyContent: 'space-between', gap: 16, fontSize: 10.5, lineHeight: 1.85 }}>
              <span style={{ color: 'var(--text-muted)' }}>Cord:</span>
              <span style={{ color: 'var(--text)', fontFamily: 'Consolas, monospace' }}>
                outlet {d.outlet} → PSU{d.psu ?? '?'}
              </span>
            </div>
          )}
        </div>,
        document.body
      )}
    </>
  )
}

export default memo(LinkEdge)
