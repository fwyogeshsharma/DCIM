import { memo } from 'react'
import {
  BaseEdge,
  EdgeLabelRenderer,
  getBezierPath,
  type EdgeProps,
} from '@xyflow/react'

const LAYER_COLOR: Record<string, string> = {
  production: '#4a9eff',
  management: '#3fb950',
  power:      '#f59e0b',
}

export interface LinkEdgeData {
  layer: string
  broken: boolean
  src_iface?: number
  dst_iface?: number
  [key: string]: unknown
}

function LinkEdge(props: EdgeProps) {
  const { id, sourceX, sourceY, targetX, targetY,
          sourcePosition, targetPosition, data, selected } = props
  const d = (data ?? {}) as LinkEdgeData
  const broken = Boolean(d.broken)
  const layer  = String(d.layer || 'production')
  const color  = broken ? '#f85149' : (LAYER_COLOR[layer] || '#4a9eff')

  const [edgePath] = getBezierPath({
    sourceX, sourceY, sourcePosition,
    targetX, targetY, targetPosition,
  })

  return (
    <>
      <BaseEdge
        id={id}
        path={edgePath}
        style={{
          stroke: color,
          strokeWidth: selected ? 2.5 : 1.5,
          strokeDasharray: broken ? '5,3' : undefined,
          opacity: broken ? 0.6 : (layer === 'management' ? 0.7 : 1),
        }}
      />
      {broken && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute',
              transform: `translate(-50%,-50%) translate(${(sourceX+targetX)/2}px,${(sourceY+targetY)/2}px)`,
              background: '#7f1d1d',
              color: '#fca5a5',
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
    </>
  )
}

export default memo(LinkEdge)
