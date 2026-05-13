import { useMemo, useState, useRef, useCallback, useEffect } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Billboard, Text, Line, Grid, Stars, Float, Html } from '@react-three/drei'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { computeHierarchicalLayout, type LayoutNode, type LayoutLink, FLOORS } from '@/lib/topology3d-layout'
import { useNavigate } from 'react-router-dom'
import { Activity, Server, Box, ChevronsDownUp, ChevronsUpDown, ChevronUp, ChevronDown, X, Thermometer } from 'lucide-react'
import * as THREE from 'three'
import { getMockTopologyData } from '@/lib/topology-mock-data'
import { useSSE } from '@/hooks/useSSE'

interface TrapAlert {
  trapType: string
  severity: string
  description: string
  deviceName: string
  timestamp: string
  ifaceIndex?: number
}

interface TrapFeedItem {
  id: number
  sourceIp: string
  deviceName: string
  trapType: string
  severity: string
  description: string
  timestamp: string
}

// ── Toggle this to use 500+ node mock data for testing ──
const USE_MOCK_DATA = false

// ── Temperature → colour mapping for heatmap mode ───────────────────────────
function tempToColor(t: number): string {
  if (t < 30) return '#3b82f6'   // blue  — cool
  if (t < 45) return '#10b981'   // green — normal
  if (t < 55) return '#f59e0b'   // amber — warm
  if (t < 65) return '#f97316'   // orange — hot
  return '#ef4444'               // red   — critical
}

// ── Heatmap: top-down camera controller ──────────────────────────────────────

function CameraTopDownController({ active }: { active: boolean }) {
  const { camera, controls } = useThree()
  useFrame(() => {
    if (!active || !controls) return
    const ctrl = controls as any
    const serverLayerY = FLOORS[FLOORS.length - 1].y
    camera.position.x += (0 - camera.position.x) * 0.06
    camera.position.y += (serverLayerY + 420 - camera.position.y) * 0.06
    camera.position.z += (10 - camera.position.z) * 0.06
    ctrl.target.x += (0 - ctrl.target.x) * 0.06
    ctrl.target.y += (serverLayerY - ctrl.target.y) * 0.06
    ctrl.target.z += (0 - ctrl.target.z) * 0.06
    ctrl.update()
  })
  return null
}

// ── Heatmap: Google-Maps-style continuous gradient overlay ───────────────────
// Algorithm:
//   1. Draw radial intensity blobs (lighter/additive blend) per device
//   2. Colorize each pixel through a blue→cyan→green→yellow→red colormap
//   3. Alpha scales with intensity so empty areas stay transparent

function HeatmapGradientOverlay({ nodes, tempMap }: {
  nodes: LayoutNode[]
  tempMap: Map<string, number>
}) {
  const overlayY = FLOORS[FLOORS.length - 1].y + 26

  // One heat point per device that has a temperature reading
  const heatPoints = useMemo(() => {
    const pts: { x: number; z: number; temp: number }[] = []
    nodes.forEach(n => {
      if (n.type === 'server') return
      const temp = (n.agentId ? tempMap.get(n.agentId) : undefined)
        ?? (n.ip ? tempMap.get(n.ip) : undefined)
      if (temp !== undefined) pts.push({ x: n.position[0], z: n.position[2], temp })
    })
    return pts
  }, [nodes, tempMap])

  // World-space bounding box of the whole scene
  const bounds = useMemo(() => {
    if (nodes.length === 0) return { minX: -60, maxX: 60, minZ: -60, maxZ: 60 }
    const xs = nodes.map(n => n.position[0])
    const zs = nodes.map(n => n.position[2])
    const p = 20
    return {
      minX: Math.min(...xs) - p,
      maxX: Math.max(...xs) + p,
      minZ: Math.min(...zs) - p,
      maxZ: Math.max(...zs) + p,
    }
  }, [nodes])

  // Canvas texture — recomputed whenever heat data or bounds change
  const texture = useMemo(() => {
    const W = 512, H = 512
    const canvas = document.createElement('canvas')
    canvas.width = W; canvas.height = H
    const ctx = canvas.getContext('2d')!

    const rX = (bounds.maxX - bounds.minX) || 1
    const rZ = (bounds.maxZ - bounds.minZ) || 1
    const toX = (x: number) => ((x - bounds.minX) / rX) * W
    const toY = (z: number) => ((z - bounds.minZ) / rZ) * H

    // Blob radius: large enough that adjacent nodes (~14 world-units apart) overlap
    const RADIUS = Math.max(44, (18 / rX) * W)

    // ── Pass 1: build greyscale intensity layer ──────────────────────────────
    ctx.fillStyle = '#000'
    ctx.fillRect(0, 0, W, H)

    if (heatPoints.length > 0) {
      const temps = heatPoints.map(p => p.temp)
      const minT = Math.min(...temps)
      const maxT = Math.max(...temps)
      const rangeT = (maxT - minT) || 1

      ctx.globalCompositeOperation = 'lighter'

      heatPoints.forEach(pt => {
        const px = toX(pt.x)
        const py = toY(pt.z)
        // Normalise temperature: coldest → 0.15 (still shows), hottest → 1.0
        const norm = 0.15 + 0.85 * Math.max(0, Math.min(1, (pt.temp - minT) / rangeT))
        // Per-blob alpha tuned so 3-4 overlapping blobs saturate to white
        const a = (norm * 0.38).toFixed(3)

        const grad = ctx.createRadialGradient(px, py, 0, px, py, RADIUS)
        grad.addColorStop(0,   `rgba(255,255,255,${a})`)
        grad.addColorStop(0.4, `rgba(255,255,255,${(norm * 0.12).toFixed(3)})`)
        grad.addColorStop(1,   'rgba(0,0,0,0)')
        ctx.fillStyle = grad
        ctx.beginPath()
        ctx.arc(px, py, RADIUS, 0, Math.PI * 2)
        ctx.fill()
      })

      // ── Pass 2: colorize each pixel through a heatmap gradient ───────────
      ctx.globalCompositeOperation = 'source-over'
      const img = ctx.getImageData(0, 0, W, H)
      const d = img.data

      for (let i = 0; i < d.length; i += 4) {
        const v = d[i] / 255       // intensity 0..1 from red channel
        if (v < 0.015) { d[i + 3] = 0; continue }

        // Classic heatmap gradient: blue → cyan → green → yellow → red
        let r = 0, g = 0, b = 0
        if (v < 0.25) {
          const f = v / 0.25
          r = 0; g = 0; b = Math.round(130 + f * 125)
        } else if (v < 0.5) {
          const f = (v - 0.25) / 0.25
          r = 0; g = Math.round(f * 255); b = Math.round((1 - f) * 255)
        } else if (v < 0.75) {
          const f = (v - 0.5) / 0.25
          r = Math.round(f * 255); g = 255; b = 0
        } else {
          const f = (v - 0.75) / 0.25
          r = 255; g = Math.round((1 - f) * 255); b = 0
        }

        d[i] = r; d[i + 1] = g; d[i + 2] = b
        // Alpha: grows quickly then plateaus — transparent where empty
        d[i + 3] = Math.round(Math.min(v * 3.2, 0.88) * 255)
      }

      ctx.putImageData(img, 0, 0)
    }

    const tex = new THREE.CanvasTexture(canvas)
    tex.needsUpdate = true
    return tex
  }, [heatPoints, bounds])

  // Properly dispose GPU texture when it's replaced or component unmounts
  useEffect(() => () => { texture?.dispose() }, [texture])

  const planeW = bounds.maxX - bounds.minX
  const planeD = bounds.maxZ - bounds.minZ
  const cx = (bounds.minX + bounds.maxX) / 2
  const cz = (bounds.minZ + bounds.maxZ) / 2

  return (
    <mesh position={[cx, overlayY, cz]} rotation={[-Math.PI / 2, 0, 0]}>
      <planeGeometry args={[planeW, planeD]} />
      <meshBasicMaterial
        map={texture}
        transparent
        depthWrite={false}
        side={THREE.DoubleSide}
      />
    </mesh>
  )
}

// ── Rack Post (vertical rail) ────────────────────────────────────────────────

function RackPost({ position }: { position: [number, number, number] }) {
  return (
    <mesh position={position}>
      <boxGeometry args={[0.3, 12, 0.3]} />
      <meshStandardMaterial color="#93c5fd" metalness={0.8} roughness={0.2} />
    </mesh>
  )
}

// ── Rack Slot (horizontal unit in the rack) ──────────────────────────────────

function RackSlot({
  position,
  filled,
  color,
}: {
  position: [number, number, number]
  filled: boolean
  color: string
}) {
  return (
    <group position={position}>
      {/* Slot panel */}
      <mesh>
        <boxGeometry args={[5, 0.8, 3.4]} />
        <meshStandardMaterial
          color={filled ? '#60a5fa' : '#3b82f6'}
          metalness={0.4}
          roughness={0.4}
        />
      </mesh>
      {/* Drive bay face plate */}
      {filled && (
        <mesh position={[0, 0, 1.71]}>
          <boxGeometry args={[4.6, 0.6, 0.02]} />
          <meshStandardMaterial color="#93c5fd" metalness={0.5} roughness={0.3} />
        </mesh>
      )}
      {/* Ventilation lines on face */}
      {filled && (
        <>
          {[-1.5, -0.5, 0.5, 1.5].map((xOff, i) => (
            <mesh key={i} position={[xOff, 0, 1.72]}>
              <boxGeometry args={[0.6, 0.15, 0.01]} />
              <meshStandardMaterial color="#dbeafe" />
            </mesh>
          ))}
        </>
      )}
      {/* LED indicator */}
      {filled && (
        <mesh position={[2.1, 0, 1.72]}>
          <circleGeometry args={[0.08, 12]} />
          <meshBasicMaterial color={color} />
        </mesh>
      )}
    </group>
  )
}

// ── Server Node (Rack Cabinet) ───────────────────────────────────────────────

function ServerNode({
  node,
  isSelected,
  onSelect,
  onHover,
  expanded,
  agentCount,
  onDoubleClick,
  heatmapMode = false,
  temperature,
}: {
  node: LayoutNode
  isSelected: boolean
  onSelect: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
  expanded: boolean
  agentCount: number
  onDoubleClick: (node: LayoutNode) => void
  heatmapMode?: boolean
  temperature?: number
}) {
  const groupRef = useRef<THREE.Group>(null)
  const glowRef = useRef<THREE.Mesh>(null)
  const [hovered, setHovered] = useState(false)
  const clickTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  useFrame(() => {
    if (!glowRef.current) return
    const mat = glowRef.current.material as THREE.MeshBasicMaterial
    if (node.status === 'offline') {
      mat.opacity = 0.15 + Math.sin(Date.now() * 0.004) * 0.15
    } else {
      mat.opacity = hovered || isSelected ? 0.12 : 0
    }
  })

  const handleClick = useCallback(
    (e: THREE.Event & { stopPropagation: () => void }) => {
      e.stopPropagation()
      // Debounce to distinguish from double-click
      if (clickTimerRef.current) {
        clearTimeout(clickTimerRef.current)
        clickTimerRef.current = null
      }
      clickTimerRef.current = setTimeout(() => {
        onSelect(node)
        clickTimerRef.current = null
      }, 250)
    },
    [node, onSelect]
  )

  const handleDoubleClick = useCallback(
    (e: THREE.Event & { stopPropagation: () => void }) => {
      e.stopPropagation()
      if (clickTimerRef.current) {
        clearTimeout(clickTimerRef.current)
        clickTimerRef.current = null
      }
      onDoubleClick(node)
    },
    [node, onDoubleClick]
  )

  const handlePointerOver = useCallback(
    (e: THREE.Event & { stopPropagation: () => void }) => {
      e.stopPropagation()
      setHovered(true)
      onHover(true)
    },
    [onHover]
  )

  const handlePointerOut = useCallback(() => {
    setHovered(false)
    onHover(false)
  }, [onHover])

  const ledColor = node.status === 'offline' ? '#ef4444' : '#10b981'
  const accentColor = node.status === 'offline' ? '#991b1b' : (node.color || '#8b5cf6')
  const heatColor = heatmapMode && temperature !== undefined ? tempToColor(temperature) : null
  const slotCount = 8

  return (
    <group
      ref={groupRef}
      position={node.position}
      onClick={handleClick}
      onDoubleClick={handleDoubleClick}
      onPointerOver={handlePointerOver}
      onPointerOut={handlePointerOut}
    >
      {/* ── Rack cabinet shell ── */}
      {/* Back panel */}
      <mesh position={[0, 0, -1.8]}>
        <boxGeometry args={[6, 12, 0.1]} />
        <meshStandardMaterial color={heatColor ?? '#7dd3fc'} metalness={0.5} roughness={0.3} />
      </mesh>
      {/* Left side panel */}
      <mesh position={[-3, 0, 0]}>
        <boxGeometry args={[0.1, 12, 3.7]} />
        <meshStandardMaterial color={heatColor ?? '#7dd3fc'} metalness={0.45} roughness={0.35} />
      </mesh>
      {/* Right side panel */}
      <mesh position={[3, 0, 0]}>
        <boxGeometry args={[0.1, 12, 3.7]} />
        <meshStandardMaterial color={heatColor ?? '#7dd3fc'} metalness={0.45} roughness={0.35} />
      </mesh>
      {/* Top panel */}
      <mesh position={[0, 6.05, 0]}>
        <boxGeometry args={[6, 0.1, 3.7]} />
        <meshStandardMaterial color={heatColor ?? '#7dd3fc'} metalness={0.45} roughness={0.35} />
      </mesh>
      {/* Bottom panel */}
      <mesh position={[0, -6.05, 0]}>
        <boxGeometry args={[6, 0.1, 3.7]} />
        <meshStandardMaterial color={heatColor ?? '#7dd3fc'} metalness={0.45} roughness={0.35} />
      </mesh>

      {/* ── 4 vertical rack posts ── */}
      <RackPost position={[-2.7, 0, -1.5]} />
      <RackPost position={[2.7, 0, -1.5]} />
      <RackPost position={[-2.7, 0, 1.5]} />
      <RackPost position={[2.7, 0, 1.5]} />

      {/* ── Rack unit slots (stacked) ── */}
      {Array.from({ length: slotCount }).map((_, i) => {
        const yPos = -4.8 + i * 1.2
        return (
          <RackSlot
            key={i}
            position={[0, yPos, 0]}
            filled={i < slotCount - 1}
            color={ledColor}
          />
        )
      })}

      {/* ── Top accent strip (server color branding) ── */}
      <mesh position={[0, 5.6, 1.86]}>
        <boxGeometry args={[5.8, 0.5, 0.02]} />
        <meshStandardMaterial
          color={accentColor}
          emissive={accentColor}
          emissiveIntensity={0.3}
          metalness={0.4}
          roughness={0.3}
        />
      </mesh>

      {/* ── Status LED strip at bottom front ── */}
      <group position={[0, -5.6, 1.86]}>
        {[-1.8, -0.9, 0, 0.9, 1.8].map((xOff, i) => (
          <mesh key={i} position={[xOff, 0, 0]}>
            <circleGeometry args={[0.12, 12]} />
            <meshBasicMaterial color={i === 0 ? ledColor : (node.status === 'offline' ? '#ef444488' : '#10b98155')} />
          </mesh>
        ))}
      </group>

      {/* ── Glow ── */}
      <mesh ref={glowRef}>
        <boxGeometry args={[7, 13, 5]} />
        <meshBasicMaterial
          color={node.status === 'offline' ? '#ef4444' : '#3b82f6'}
          transparent
          opacity={0}
          depthWrite={false}
        />
      </mesh>

      {/* ── Selection wireframe ── */}
      {isSelected && (
        <mesh>
          <boxGeometry args={[7.5, 13.5, 5.5]} />
          <meshBasicMaterial color="#3b82f6" wireframe transparent opacity={0.5} />
        </mesh>
      )}

      {/* ── Label ── */}
      <Billboard position={[0, 8.5, 0]}>
        <Text
          fontSize={1.4}
          color="white"
          anchorX="center"
          anchorY="middle"
          outlineWidth={0.08}
          outlineColor="#000000"
          font={undefined}
        >
          {node.name}
        </Text>
      </Billboard>

      {/* ── Agent count badge ── */}
      {agentCount > 0 && (
        <Billboard position={[0, 7, 0]}>
          <Text
            fontSize={0.8}
            color={expanded ? '#a78bfa' : '#93c5fd'}
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.04}
            outlineColor="#000000"
          >
            {expanded ? `▾ ${agentCount} agents` : `▸ ${agentCount} agents`}
          </Text>
        </Billboard>
      )}

      {/* ── Temperature billboard (heatmap mode) ── */}
      {heatmapMode && temperature !== undefined && (
        <Billboard position={[0, 9.5, 0]}>
          <Text
            fontSize={1.0}
            color={tempToColor(temperature)}
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.06}
            outlineColor="#000000"
          >
            {temperature.toFixed(1)}°C
          </Text>
        </Billboard>
      )}

      {/* ── Status label for offline ── */}
      {node.status === 'offline' && (
        <Billboard position={[0, -8, 0]}>
          <Text
            fontSize={0.9}
            color="#ef4444"
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.06}
            outlineColor="#000000"
          >
            DISCONNECTED
          </Text>
        </Billboard>
      )}
    </group>
  )
}

// ── Agent Node (1U Rack Server) ──────────────────────────────────────────────

function AgentNode({
  node,
  isSelected,
  onSelect,
  onHover,
  useFloat = true,
  heatmapMode = false,
  temperature,
}: {
  node: LayoutNode
  isSelected: boolean
  onSelect: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
  useFloat?: boolean
  heatmapMode?: boolean
  temperature?: number
}) {
  const glowRef = useRef<THREE.Mesh>(null)
  const [hovered, setHovered] = useState(false)

  useFrame(() => {
    if (!glowRef.current) return
    const mat = glowRef.current.material as THREE.MeshBasicMaterial
    if (node.status === 'offline') {
      mat.opacity = 0.12 + Math.sin(Date.now() * 0.005) * 0.12
    } else {
      mat.opacity = hovered || isSelected ? 0.1 : 0
    }
  })

  const handleClick = useCallback(
    (e: THREE.Event & { stopPropagation: () => void }) => {
      e.stopPropagation()
      onSelect(node)
    },
    [node, onSelect]
  )

  const handlePointerOver = useCallback(
    (e: THREE.Event & { stopPropagation: () => void }) => {
      e.stopPropagation()
      setHovered(true)
      onHover(true)
    },
    [onHover]
  )

  const handlePointerOut = useCallback(() => {
    setHovered(false)
    onHover(false)
  }, [onHover])

  const ledColor = node.status === 'online' ? '#10b981' : '#ef4444'
  const bodyColor = node.status === 'online' ? '#60a5fa' : '#f87171'
  const faceColor = node.status === 'online' ? '#93c5fd' : '#fca5a5'
  const bodyHeatColor = heatmapMode && temperature !== undefined ? tempToColor(temperature) : undefined

  const serverUnit = (
    <group
      position={node.position}
      onClick={handleClick}
      onPointerOver={handlePointerOver}
      onPointerOut={handlePointerOut}
    >
      {/* ── Main chassis (1U server body) ── */}
      <mesh>
        <boxGeometry args={[5, 1.2, 2.8]} />
        <meshStandardMaterial
          color={bodyHeatColor ?? bodyColor}
          metalness={0.45}
          roughness={0.3}
        />
      </mesh>

      {/* ── Front face plate ── */}
      <mesh position={[0, 0, 1.41]}>
        <boxGeometry args={[4.9, 1.1, 0.02]} />
        <meshStandardMaterial color={faceColor} metalness={0.4} roughness={0.3} />
      </mesh>

      {/* ── Drive bays (4 white port rectangles on front) ── */}
      {[-1.5, -0.5, 0.5, 1.5].map((xOff, i) => (
        <group key={`bay-${i}`} position={[xOff, 0, 1.42]}>
          {/* Bay slot (white) */}
          <mesh>
            <boxGeometry args={[0.7, 0.6, 0.01]} />
            <meshStandardMaterial color="#ffffff" metalness={0.3} roughness={0.4} />
          </mesh>
          {/* Bay handle (white) */}
          <mesh position={[0, -0.2, 0.01]}>
            <boxGeometry args={[0.5, 0.08, 0.01]} />
            <meshStandardMaterial color="#e0e7ff" metalness={0.6} roughness={0.2} />
          </mesh>
        </group>
      ))}

      {/* ── Power button ── */}
      <mesh position={[2.2, 0, 1.42]}>
        <circleGeometry args={[0.12, 16]} />
        <meshBasicMaterial color={ledColor} />
      </mesh>

      {/* ── Status LED row ── */}
      {[0, 0.25, 0.5].map((xOff, i) => (
        <mesh key={`led-${i}`} position={[2.2 - 0.4 - xOff, 0.3, 1.42]}>
          <circleGeometry args={[0.06, 8]} />
          <meshBasicMaterial
            color={i === 0 ? ledColor : (node.status === 'online' ? '#3b82f6' : '#ef444466')}
          />
        </mesh>
      ))}

      {/* ── Side handles (white) ── */}
      <mesh position={[-2.55, 0, 0.8]}>
        <boxGeometry args={[0.08, 0.4, 0.8]} />
        <meshStandardMaterial color="#e0e7ff" metalness={0.6} roughness={0.2} />
      </mesh>
      <mesh position={[2.55, 0, 0.8]}>
        <boxGeometry args={[0.08, 0.4, 0.8]} />
        <meshStandardMaterial color="#e0e7ff" metalness={0.6} roughness={0.2} />
      </mesh>

      {/* ── Rear ventilation grille (white ports) ── */}
      <mesh position={[0, 0, -1.41]}>
        <boxGeometry args={[4.8, 1, 0.02]} />
        <meshStandardMaterial color="#bfdbfe" metalness={0.3} roughness={0.4} />
      </mesh>
      {Array.from({ length: 6 }).map((_, i) => (
        <mesh key={`vent-${i}`} position={[-1.8 + i * 0.7, 0, -1.42]}>
          <boxGeometry args={[0.4, 0.8, 0.01]} />
          <meshStandardMaterial color="#ffffff" metalness={0.2} roughness={0.5} />
        </mesh>
      ))}

      {/* ── Glow effect (hover / offline pulse) ── */}
      <mesh ref={glowRef}>
        <boxGeometry args={[6, 2, 4]} />
        <meshBasicMaterial
          color={node.status === 'offline' ? '#ef4444' : '#3b82f6'}
          transparent
          opacity={0}
          depthWrite={false}
        />
      </mesh>

      {/* ── Selection wireframe ── */}
      {isSelected && (
        <mesh>
          <boxGeometry args={[6.5, 2.5, 4.5]} />
          <meshBasicMaterial color="#3b82f6" wireframe transparent opacity={0.5} />
        </mesh>
      )}

      {/* ── Label ── */}
      <Billboard position={[0, 2.5, 0]}>
        <Text
          fontSize={0.9}
          color="white"
          anchorX="center"
          anchorY="middle"
          outlineWidth={0.06}
          outlineColor="#000000"
        >
          {node.name}
        </Text>
      </Billboard>

      {/* ── Server name sub-label ── */}
      {node.serverName && (
        <Billboard position={[0, 1.5, 0]}>
          <Text
            fontSize={0.6}
            color="#94a3b8"
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.04}
            outlineColor="#000000"
          >
            {node.serverName}
          </Text>
        </Billboard>
      )}

      {/* ── Temperature billboard (heatmap mode) ── */}
      {heatmapMode && temperature !== undefined && (
        <Billboard position={[0, 3.5, 0]}>
          <Text
            fontSize={0.75}
            color={tempToColor(temperature)}
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.05}
            outlineColor="#000000"
          >
            {temperature.toFixed(1)}°C
          </Text>
        </Billboard>
      )}

      {/* ── Status label for offline ── */}
      {node.status === 'offline' && (
        <Billboard position={[0, -2.2, 0]}>
          <Text
            fontSize={0.7}
            color="#ef4444"
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.05}
            outlineColor="#000000"
          >
            NOT REACHABLE
          </Text>
        </Billboard>
      )}
    </group>
  )

  // Online agents get a gentle float bob (disabled for large counts)
  if (node.status === 'online' && useFloat) {
    return (
      <Float speed={2} rotationIntensity={0} floatIntensity={0.3} floatingRange={[-0.2, 0.2]}>
        {serverUnit}
      </Float>
    )
  }

  return serverUnit
}

// ── Device Node (SNMP Network Device) ────────────────────────────────────────

function DeviceNode({
  node,
  isSelected,
  onSelect,
  onHover,
  hasTrap = false,
  heatmapMode = false,
  temperature,
}: {
  node: LayoutNode
  isSelected: boolean
  onSelect: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
  hasTrap?: boolean
  heatmapMode?: boolean
  temperature?: number
}) {
  const glowRef = useRef<THREE.Mesh>(null)
  const [hovered, setHovered] = useState(false)

  useFrame(() => {
    if (!glowRef.current) return
    const mat = glowRef.current.material as THREE.MeshBasicMaterial
    if (node.status === 'offline' || hasTrap) {
      mat.opacity = 0.15 + Math.sin(Date.now() * 0.004) * 0.15
    } else {
      mat.opacity = hovered || isSelected ? 0.12 : 0
    }
  })

  const handleClick = useCallback(
    (e: THREE.Event & { stopPropagation: () => void }) => {
      e.stopPropagation()
      onSelect(node)
    },
    [node, onSelect]
  )

  const handlePointerOver = useCallback(
    (e: THREE.Event & { stopPropagation: () => void }) => {
      e.stopPropagation()
      setHovered(true)
      onHover(true)
    },
    [onHover]
  )

  const handlePointerOut = useCallback(() => {
    setHovered(false)
    onHover(false)
  }, [onHover])

  const online = node.status === 'online'
  // Trap alert overrides normal colors — device turns deep red with warning indicators
  // Heatmap mode overrides chassis color with temperature-mapped colour
  const chassisColor = heatmapMode && temperature !== undefined
    ? tempToColor(temperature)
    : (hasTrap ? '#7f1d1d' : online ? '#7dd3fc' : '#b91c1c')
  const accentColor  = hasTrap ? '#ef4444' : online ? '#3b82f6' : '#ef4444'
  const ledOn        = hasTrap ? '#ef4444' : online ? '#22d3ee' : '#fca5a5'
  const ledOff       = hasTrap ? '#dc2626' : online ? '#0ea5e9' : '#7f1d1d'
  const labelColor   = hasTrap ? '#fca5a5' : online ? '#93c5fd' : '#fca5a5'
  const portCount = 8

  return (
    <group
      position={node.position}
      onClick={handleClick}
      onPointerOver={handlePointerOver}
      onPointerOut={handlePointerOut}
    >
      {/* ── Rackmount switch chassis ── */}
      <mesh>
        <boxGeometry args={[3.4, 0.75, 1.7]} />
        <meshStandardMaterial
          color={chassisColor}
          metalness={0.55}
          roughness={0.35}
        />
      </mesh>

      {/* Top accent strip — brand color */}
      <mesh position={[0, 0.4, 0]}>
        <boxGeometry args={[3.3, 0.03, 1.5]} />
        <meshStandardMaterial
          color={accentColor}
          emissive={accentColor}
          emissiveIntensity={online ? 0.35 : 0.5}
          metalness={0.4}
          roughness={0.3}
        />
      </mesh>

      {/* Front bezel */}
      <mesh position={[0, 0, 0.86]}>
        <boxGeometry args={[3.35, 0.7, 0.02]} />
        <meshStandardMaterial color={online ? '#1e3a8a' : '#450a0a'} metalness={0.3} roughness={0.5} />
      </mesh>

      {/* Port LEDs — two rows */}
      {Array.from({ length: portCount }).map((_, i) => {
        const xOff = -1.35 + i * (2.7 / (portCount - 1))
        return (
          <group key={i}>
            <mesh position={[xOff, 0.12, 0.88]}>
              <boxGeometry args={[0.22, 0.15, 0.03]} />
              <meshStandardMaterial
                color={i % 2 === 0 ? ledOn : ledOff}
                emissive={i % 2 === 0 ? ledOn : ledOff}
                emissiveIntensity={online ? 0.8 : 0.15}
              />
            </mesh>
            <mesh position={[xOff, -0.12, 0.88]}>
              <boxGeometry args={[0.22, 0.15, 0.03]} />
              <meshStandardMaterial
                color={ledOff}
                emissive={ledOff}
                emissiveIntensity={online ? 0.4 : 0.1}
              />
            </mesh>
          </group>
        )
      })}

      {/* Status LED (left front) */}
      <mesh position={[-1.58, 0, 0.88]}>
        <circleGeometry args={[0.09, 12]} />
        <meshBasicMaterial color={online ? '#22c55e' : '#ef4444'} />
      </mesh>

      {/* Rack ears */}
      <mesh position={[-1.78, 0, 0.6]}>
        <boxGeometry args={[0.18, 0.72, 0.08]} />
        <meshStandardMaterial color={chassisColor} metalness={0.5} roughness={0.4} />
      </mesh>
      <mesh position={[1.78, 0, 0.6]}>
        <boxGeometry args={[0.18, 0.72, 0.08]} />
        <meshStandardMaterial color={chassisColor} metalness={0.5} roughness={0.4} />
      </mesh>

      {/* Pulsing glow (offline pulses red, online gets a subtle hover tint) */}
      <mesh ref={glowRef}>
        <boxGeometry args={[4, 1.4, 2.3]} />
        <meshBasicMaterial color={accentColor} transparent opacity={0} depthWrite={false} />
      </mesh>

      {/* Selection wireframe */}
      {isSelected && (
        <mesh>
          <boxGeometry args={[3.7, 1.05, 2]} />
          <meshBasicMaterial color={accentColor} wireframe transparent opacity={0.6} />
        </mesh>
      )}

      {/* Label */}
      <Billboard position={[0, 1.5, 0]}>
        <Text
          fontSize={0.6}
          color={labelColor}
          anchorX="center"
          anchorY="middle"
          outlineWidth={0.04}
          outlineColor="#000000"
        >
          {node.name}
        </Text>
      </Billboard>

      {node.agentName && (
        <Billboard position={[0, 1, 0]}>
          <Text
            fontSize={0.42}
            color="#94a3b8"
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.03}
            outlineColor="#000000"
          >
            via {node.agentName}
          </Text>
        </Billboard>
      )}

      {/* ── Temperature billboard (heatmap mode) ── */}
      {heatmapMode && temperature !== undefined && (
        <Billboard position={[0, 2.2, 0]}>
          <Text
            fontSize={0.55}
            color={tempToColor(temperature)}
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.04}
            outlineColor="#000000"
          >
            {temperature.toFixed(1)}°C
          </Text>
        </Billboard>
      )}

      {!online && (
        <Billboard position={[0, -1.1, 0]}>
          <Text
            fontSize={0.45}
            color="#fca5a5"
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.03}
            outlineColor="#000000"
          >
            NOT RESPONDING
          </Text>
        </Billboard>
      )}
    </group>
  )
}

// ── PDU Node (Power Distribution Unit — vertical power strip with lightning) ─

function PDUNode({
  node,
  isSelected,
  onSelect,
  onHover,
  heatmapMode = false,
  temperature,
}: {
  node: LayoutNode
  isSelected: boolean
  onSelect: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
  heatmapMode?: boolean
  temperature?: number
}) {
  const glowRef = useRef<THREE.Mesh>(null)
  const boltRef = useRef<THREE.Mesh>(null)
  const [hovered, setHovered] = useState(false)

  // Build lightning bolt shape once — extruded polygon
  const lightningGeo = useMemo(() => {
    const shape = new THREE.Shape()
    shape.moveTo(0.22, 0.75)
    shape.lineTo(-0.06, 0.06)
    shape.lineTo(0.1, 0.06)
    shape.lineTo(-0.22, -0.75)
    shape.lineTo(0.06, -0.06)
    shape.lineTo(-0.1, -0.06)
    shape.closePath()
    return new THREE.ExtrudeGeometry(shape, { depth: 0.12, bevelEnabled: false })
  }, [])

  useFrame(({ clock }) => {
    if (glowRef.current) {
      const mat = glowRef.current.material as THREE.MeshBasicMaterial
      if (node.status === 'offline') {
        mat.opacity = 0.1 + Math.sin(clock.getElapsedTime() * 3) * 0.1
      } else {
        mat.opacity = hovered || isSelected ? 0.18 : 0.06
      }
    }
    // Bolt pulses brighter when online
    if (boltRef.current && node.status === 'online') {
      const mat = boltRef.current.material as THREE.MeshStandardMaterial
      mat.emissiveIntensity = 1.4 + Math.sin(clock.getElapsedTime() * 4) * 0.6
    }
  })

  const handleClick = useCallback(
    (e: THREE.Event & { stopPropagation: () => void }) => {
      e.stopPropagation()
      onSelect(node)
    },
    [node, onSelect]
  )
  const handlePointerOver = useCallback(
    (e: THREE.Event & { stopPropagation: () => void }) => {
      e.stopPropagation()
      setHovered(true)
      onHover(true)
    },
    [onHover]
  )
  const handlePointerOut = useCallback(() => {
    setHovered(false)
    onHover(false)
  }, [onHover])

  const online = node.status === 'online'
  const isUPS = (node.deviceType ?? node.name ?? '').toUpperCase().includes('UPS')
  const boltColor   = online ? (isUPS ? '#bef264' : '#fde047') : '#6b7280'
  const bodyColor   = online ? (isUPS ? '#0f1a07' : '#1c1917') : '#0c0a09'
  const panelColor  = online ? (isUPS ? '#1a2e0a' : '#292524') : '#1c1917'
  const accentStrip = online ? (isUPS ? '#84cc16' : '#f59e0b') : (isUPS ? '#365314' : '#78350f')
  const outletGlow  = online ? (isUPS ? '#a3e635' : '#fbbf24') : '#1a1a1a'
  const labelColor  = online ? (isUPS ? '#d9f99d' : '#fde68a') : '#6b7280'
  const pduHeatBodyColor = heatmapMode && temperature !== undefined ? tempToColor(temperature) : undefined

  return (
    <group
      position={node.position}
      onClick={handleClick}
      onPointerOver={handlePointerOver}
      onPointerOut={handlePointerOut}
    >
      {/* ── Main vertical body ── */}
      <mesh>
        <boxGeometry args={[1.1, 8.0, 0.85]} />
        <meshStandardMaterial color={pduHeatBodyColor ?? bodyColor} metalness={0.7} roughness={0.25} />
      </mesh>

      {/* ── Front panel (slightly proud) ── */}
      <mesh position={[0, 0, 0.44]}>
        <boxGeometry args={[0.95, 7.8, 0.04]} />
        <meshStandardMaterial color={panelColor} metalness={0.5} roughness={0.3} />
      </mesh>

      {/* ── Vertical amber accent stripe (left edge) ── */}
      <mesh position={[-0.52, 0, 0.2]}>
        <boxGeometry args={[0.06, 7.6, 0.5]} />
        <meshStandardMaterial
          color={accentStrip}
          emissive={accentStrip}
          emissiveIntensity={online ? 0.6 : 0.2}
          metalness={0.3}
          roughness={0.4}
        />
      </mesh>

      {/* ── Outlet ports (6 down the front panel) ── */}
      {Array.from({ length: 6 }).map((_, i) => {
        const yOff = 2.8 - i
        return (
          <group key={i} position={[0, yOff, 0.47]}>
            {/* Outlet housing */}
            <mesh>
              <boxGeometry args={[0.55, 0.55, 0.06]} />
              <meshStandardMaterial color="#0a0a0a" metalness={0.2} roughness={0.8} />
            </mesh>
            {/* Two prong holes */}
            <mesh position={[-0.1, 0, 0.04]}>
              <boxGeometry args={[0.09, 0.22, 0.04]} />
              <meshStandardMaterial color={outletGlow} emissive={outletGlow} emissiveIntensity={online ? 0.9 : 0.1} />
            </mesh>
            <mesh position={[0.1, 0, 0.04]}>
              <boxGeometry args={[0.09, 0.22, 0.04]} />
              <meshStandardMaterial color={outletGlow} emissive={outletGlow} emissiveIntensity={online ? 0.9 : 0.1} />
            </mesh>
            {/* Ground hole */}
            <mesh position={[0, -0.16, 0.04]}>
              <cylinderGeometry args={[0.04, 0.04, 0.04, 8]} />
              <meshStandardMaterial color={outletGlow} emissive={outletGlow} emissiveIntensity={online ? 0.5 : 0.05} />
            </mesh>
          </group>
        )
      })}

      {/* ── Status indicator bar (top of panel) ── */}
      <mesh position={[0, 3.7, 0.46]}>
        <boxGeometry args={[0.7, 0.22, 0.03]} />
        <meshStandardMaterial
          color={online ? '#22c55e' : '#ef4444'}
          emissive={online ? '#22c55e' : '#ef4444'}
          emissiveIntensity={online ? 1.2 : 0.6}
        />
      </mesh>

      {/* ── Top cap face ── */}
      <mesh position={[0, 4.05, 0]}>
        <boxGeometry args={[1.1, 0.1, 0.85]} />
        <meshStandardMaterial color={accentStrip} emissive={accentStrip} emissiveIntensity={online ? 0.4 : 0.1} metalness={0.6} roughness={0.3} />
      </mesh>

      {/* ── Lightning bolt (extruded, floats above unit) ── */}
      <group position={[0, 5.6, 0]}>
        {/* Glow halo behind bolt */}
        <mesh>
          <sphereGeometry args={[0.9, 16, 16]} />
          <meshBasicMaterial
            color={boltColor}
            transparent
            opacity={online ? 0.12 : 0.04}
            depthWrite={false}
          />
        </mesh>
        {/* Outer ring */}
        <mesh rotation={[Math.PI / 2, 0, 0]}>
          <torusGeometry args={[0.65, 0.04, 8, 32]} />
          <meshStandardMaterial
            color={boltColor}
            emissive={boltColor}
            emissiveIntensity={online ? 0.8 : 0.2}
            metalness={0.3}
            roughness={0.4}
          />
        </mesh>
        {/* 3D extruded bolt, centered */}
        <mesh
          ref={boltRef}
          geometry={lightningGeo}
          position={[-0.16, -0.75, -0.06]}
          scale={[1.1, 1.1, 1.1]}
        >
          <meshStandardMaterial
            color={boltColor}
            emissive={boltColor}
            emissiveIntensity={online ? 2.0 : 0.4}
            metalness={0.1}
            roughness={0.3}
          />
        </mesh>
      </group>

      {/* ── Ambient glow volume ── */}
      <mesh ref={glowRef}>
        <boxGeometry args={[2.2, 9.5, 2.0]} />
        <meshBasicMaterial color={online ? '#f59e0b' : '#ef4444'} transparent opacity={0.06} depthWrite={false} />
      </mesh>

      {/* ── Selection wireframe ── */}
      {isSelected && (
        <mesh>
          <boxGeometry args={[2.4, 9.8, 2.2]} />
          <meshBasicMaterial color={boltColor} wireframe transparent opacity={0.5} />
        </mesh>
      )}

      {/* ── Label ── */}
      <Billboard position={[0, 7.0, 0]}>
        <Text
          fontSize={0.7}
          color={labelColor}
          anchorX="center"
          anchorY="middle"
          outlineWidth={0.05}
          outlineColor="#000000"
        >
          {node.name}
        </Text>
      </Billboard>

      {/* ── Temperature billboard (heatmap mode) ── */}
      {heatmapMode && temperature !== undefined && (
        <Billboard position={[0, 8.0, 0]}>
          <Text
            fontSize={0.65}
            color={tempToColor(temperature)}
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.05}
            outlineColor="#000000"
          >
            {temperature.toFixed(1)}°C
          </Text>
        </Billboard>
      )}

      {!online && (
        <Billboard position={[0, -5.4, 0]}>
          <Text fontSize={0.5} color="#ef4444" anchorX="center" anchorY="middle" outlineWidth={0.03} outlineColor="#000000">
            OFFLINE
          </Text>
        </Billboard>
      )}
    </group>
  )
}

// ── Connection Line ──────────────────────────────────────────────────────────

function ConnectionLine({ link, isLinkDown = false }: { link: LayoutLink; isLinkDown?: boolean }) {
  const ref = useRef<any>(null)
  const [hovered, setHovered] = useState(false)

  useFrame(() => {
    if (ref.current && (link.linkType === 'device-agent' || !link.connected)) {
      ref.current.material.dashOffset -= link.linkType === 'device-agent' ? 0.02 : 0.05
    }
  })

  const isAgentLink = link.linkType === 'device-agent'
  const isD2D = link.linkType === 'device-device'
  const color = isLinkDown
    ? '#ef4444'
    : isD2D
    ? (link.connected ? '#f59e0b' : '#ef4444')
    : isAgentLink
    ? '#06b6d4'
    : link.connected ? '#10b981' : '#ef4444'

  // Midpoint for the hover tooltip anchor.
  const midPoint: [number, number, number] = [
    (link.sourcePos[0] + link.targetPos[0]) / 2,
    (link.sourcePos[1] + link.targetPos[1]) / 2,
    (link.sourcePos[2] + link.targetPos[2]) / 2,
  ]

  const renderTooltip = () => {
    if (!isD2D || !link.d2dInfo || !hovered) return null
    const info = link.d2dInfo
    const faulted = info.sourceStatus === 'offline' || info.targetStatus === 'offline'
    const ageMs = Date.now() - new Date(info.lastSeen).getTime()
    const ageMin = Math.max(0, Math.round(ageMs / 60000))
    const ageText = ageMin < 2 ? 'just now' : ageMin < 60 ? `${ageMin} min ago` : `${Math.round(ageMin / 60)} h ago`
    let faultReason = ''
    if (info.sourceStatus === 'offline' && info.targetStatus === 'offline') {
      faultReason = 'Both endpoints offline'
    } else if (info.sourceStatus === 'offline') {
      faultReason = `${info.sourceName} offline`
    } else if (info.targetStatus === 'offline') {
      faultReason = `${info.targetName} offline`
    }
    return (
      <Html position={midPoint} center zIndexRange={[100, 0]} wrapperClass="pointer-events-none">
        <div className="bg-slate-900/95 border border-white/10 rounded-lg p-3 text-xs shadow-lg min-w-[240px]">
          <div className="font-bold text-base mb-1.5 text-amber-300">Link</div>
          <div className="grid grid-cols-[auto_1fr] gap-x-3 gap-y-1">
            <span className="text-slate-400">Status</span>
            <span className={`${faulted ? 'text-red-400' : 'text-green-400'} font-semibold`}>
              {faulted ? 'Link down' : 'Link up'}
            </span>
            {faultReason && (
              <>
                <span className="text-slate-400">Fault</span>
                <span className="text-red-300">{faultReason}</span>
              </>
            )}
            <span className="text-slate-400">Source</span>
            <span className="text-slate-200">
              {info.sourceName} <span className="text-slate-500 font-mono text-[11px]">({info.sourceIp})</span>
            </span>
            {info.sourcePort ? (
              <>
                <span className="text-slate-400">Src port</span>
                <span className="text-slate-300 font-mono text-[11px]">{info.sourcePort}</span>
              </>
            ) : null}
            <span className="text-slate-400">Target</span>
            <span className="text-slate-200">
              {info.targetName} <span className="text-slate-500 font-mono text-[11px]">({info.targetIp})</span>
            </span>
            {info.targetPort ? (
              <>
                <span className="text-slate-400">Tgt port</span>
                <span className="text-slate-300 font-mono text-[11px]">{info.targetPort}</span>
              </>
            ) : null}
            <span className="text-slate-400">Last seen</span>
            <span className="text-slate-300">{ageText}</span>
          </div>
        </div>
      </Html>
    )
  }

  return (
    <>
      <Line
        ref={ref}
        points={[link.sourcePos, link.targetPos]}
        color={color}
        lineWidth={isD2D ? (hovered ? 3 : 2) : isAgentLink ? 1 : link.connected ? 1.5 : 2}
        dashed={isAgentLink || !link.connected}
        dashSize={isAgentLink ? 0.5 : 1}
        gapSize={isAgentLink ? 0.5 : 0.8}
        transparent
        opacity={isD2D ? (link.connected ? 0.85 : 0.7) : isAgentLink ? 0.5 : link.connected ? 0.6 : 0.8}
        onPointerOver={isD2D ? (e: any) => { e.stopPropagation?.(); setHovered(true) } : undefined}
        onPointerOut={isD2D ? () => setHovered(false) : undefined}
      />
      {renderTooltip()}
    </>
  )
}

// ── Joystick Navigator ──────────────────────────────────────────────────────

function JoystickNav({ onVelocity }: { onVelocity: (vx: number, vy: number) => void }) {
  const padRef = useRef<HTMLDivElement>(null)
  const [knobPos, setKnobPos] = useState({ x: 0, y: 0 })
  const [active, setActive] = useState(false)
  const dragging = useRef(false)
  const PAD_R = 50
  const KNOB_R = 18
  const MAX_OFFSET = PAD_R - KNOB_R

  const updateFromPointer = useCallback((clientX: number, clientY: number) => {
    if (!padRef.current) return
    const rect = padRef.current.getBoundingClientRect()
    const cx = rect.left + rect.width / 2
    const cy = rect.top + rect.height / 2
    let dx = clientX - cx
    let dy = clientY - cy
    const dist = Math.sqrt(dx * dx + dy * dy)
    if (dist > MAX_OFFSET) {
      dx = (dx / dist) * MAX_OFFSET
      dy = (dy / dist) * MAX_OFFSET
    }
    setKnobPos({ x: dx, y: dy })
    onVelocity(dx / MAX_OFFSET, -dy / MAX_OFFSET)
  }, [onVelocity, MAX_OFFSET])

  const handleDown = useCallback((e: React.PointerEvent) => {
    dragging.current = true
    setActive(true)
    e.currentTarget.setPointerCapture(e.pointerId)
    updateFromPointer(e.clientX, e.clientY)
  }, [updateFromPointer])

  const handleMove = useCallback((e: React.PointerEvent) => {
    if (!dragging.current) return
    updateFromPointer(e.clientX, e.clientY)
  }, [updateFromPointer])

  const handleUp = useCallback(() => {
    dragging.current = false
    setActive(false)
    setKnobPos({ x: 0, y: 0 })
    onVelocity(0, 0)
  }, [onVelocity])

  // Compass tick marks
  const ticks = useMemo(() =>
    [0, 45, 90, 135, 180, 225, 270, 315].map(deg => ({
      deg,
      isMajor: deg % 90 === 0,
    })),
    []
  )

  return (
    <div className="flex flex-col items-center gap-2">
      <div
        ref={padRef}
        className="relative select-none touch-none"
        style={{
          width: PAD_R * 2,
          height: PAD_R * 2,
          borderRadius: '50%',
          cursor: active ? 'grabbing' : 'grab',
          background: 'radial-gradient(circle at 40% 35%, rgba(15,23,42,0.98), rgba(8,12,28,0.95))',
          boxShadow: active
            ? '0 0 35px rgba(6,182,212,0.35), 0 0 60px rgba(6,182,212,0.1), inset 0 0 25px rgba(6,182,212,0.12)'
            : '0 0 20px rgba(59,130,246,0.12), inset 0 0 15px rgba(59,130,246,0.05)',
          border: active ? '1.5px solid rgba(6,182,212,0.5)' : '1px solid rgba(59,130,246,0.25)',
          transition: 'box-shadow 0.3s, border-color 0.3s',
        }}
        onPointerDown={handleDown}
        onPointerMove={handleMove}
        onPointerUp={handleUp}
        onPointerCancel={handleUp}
      >
        {/* Compass tick marks */}
        {ticks.map(({ deg, isMajor }) => (
          <div
            key={deg}
            className="absolute"
            style={{
              left: '50%',
              top: '50%',
              width: isMajor ? 2 : 1,
              height: isMajor ? 6 : 4,
              background: active && isMajor ? 'rgba(6,182,212,0.5)' : 'rgba(6,182,212,0.25)',
              borderRadius: 1,
              transformOrigin: '50% 50%',
              transform: `translate(-50%, -50%) rotate(${deg}deg) translateY(-${PAD_R - (isMajor ? 5 : 4)}px)`,
              transition: 'background 0.3s',
            }}
          />
        ))}

        {/* Crosshair lines */}
        <div
          className="absolute left-1/2 -translate-x-1/2"
          style={{
            top: 12, bottom: 12, width: 1,
            background: 'linear-gradient(to bottom, rgba(6,182,212,0.15), rgba(6,182,212,0.08), transparent, rgba(6,182,212,0.08), rgba(6,182,212,0.15))',
          }}
        />
        <div
          className="absolute top-1/2 -translate-y-1/2"
          style={{
            left: 12, right: 12, height: 1,
            background: 'linear-gradient(to right, rgba(6,182,212,0.15), rgba(6,182,212,0.08), transparent, rgba(6,182,212,0.08), rgba(6,182,212,0.15))',
          }}
        />

        {/* Inner track ring */}
        <div
          className="absolute rounded-full"
          style={{
            inset: 14,
            border: active ? '1px solid rgba(6,182,212,0.15)' : '1px solid rgba(59,130,246,0.08)',
            transition: 'border-color 0.3s',
          }}
        />

        {/* Outer orbit ring (decorative, pulsing) */}
        <div
          className="absolute rounded-full"
          style={{
            inset: 6,
            border: '1px solid rgba(6,182,212,0.06)',
          }}
        />

        {/* Knob */}
        <div
          className="absolute pointer-events-none"
          style={{
            left: '50%',
            top: '50%',
            width: KNOB_R * 2,
            height: KNOB_R * 2,
            borderRadius: '50%',
            transform: `translate(calc(-50% + ${knobPos.x}px), calc(-50% + ${knobPos.y}px))`,
            background: active
              ? 'radial-gradient(circle at 35% 30%, #22d3ee, #06b6d4, #0e7490)'
              : 'radial-gradient(circle at 35% 30%, #93c5fd, #3b82f6, #1d4ed8)',
            boxShadow: active
              ? '0 0 16px rgba(6,182,212,0.7), 0 0 35px rgba(6,182,212,0.25), 0 2px 8px rgba(0,0,0,0.5)'
              : '0 0 8px rgba(59,130,246,0.4), 0 0 20px rgba(59,130,246,0.1), 0 2px 6px rgba(0,0,0,0.4)',
            border: active ? '1px solid rgba(103,232,249,0.5)' : '1px solid rgba(147,197,253,0.3)',
            transition: active ? 'background 0.15s, box-shadow 0.15s, border-color 0.15s' : 'all 0.25s cubic-bezier(0.25, 0.46, 0.45, 0.94)',
          }}
        >
          {/* Knob specular highlight */}
          <div
            className="absolute rounded-full"
            style={{
              inset: 3,
              background: 'linear-gradient(135deg, rgba(255,255,255,0.3) 0%, transparent 50%)',
              borderRadius: '50%',
            }}
          />
          {/* Knob center dot */}
          <div
            className="absolute rounded-full"
            style={{
              width: 4, height: 4,
              left: '50%', top: '50%',
              transform: 'translate(-50%, -50%)',
              background: active ? '#67e8f9' : '#93c5fd',
              boxShadow: active ? '0 0 6px rgba(103,232,249,0.8)' : '0 0 4px rgba(147,197,253,0.5)',
            }}
          />
        </div>
      </div>

      {/* Label */}
      <div
        className="text-[10px] font-medium tracking-wider uppercase"
        style={{ color: active ? '#22d3ee' : '#64748b', transition: 'color 0.3s' }}
      >
        Navigate
      </div>
    </div>
  )
}

// ── Floor Navigator ──────────────────────────────────────────────────────────


function CameraFloorRunner({ targetFloorY, targetPanX }: { targetFloorY: number; targetPanX: number }) {
  const { camera, controls } = useThree()

  useFrame(() => {
    if (!controls) return
    const ctrl = controls as any
    let needsUpdate = false

    const diffY = targetFloorY - ctrl.target.y
    if (Math.abs(diffY) > 0.01) {
      const step = diffY * 0.07
      ctrl.target.y += step
      camera.position.y += step
      needsUpdate = true
    }

    const diffX = targetPanX - ctrl.target.x
    if (Math.abs(diffX) > 0.05) {
      const step = diffX * 0.07
      ctrl.target.x += step
      camera.position.x += step
      needsUpdate = true
    }

    if (needsUpdate) ctrl.update()
  })

  return null
}

// ── Scene Content ────────────────────────────────────────────────────────────

function SceneContent({
  nodes,
  links,
  selectedNode,
  onSelectNode,
  onHover,
  onDeselect,
  expandedServers,
  agentCounts,
  onDoubleClickServer,
  currentFloorY,
  targetPanX,
  trapAlerts,
  linkDownAlerts,
  heatmapMode,
  tempMap,
}: {
  nodes: LayoutNode[]
  links: LayoutLink[]
  selectedNode: LayoutNode | null
  onSelectNode: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
  onDeselect: () => void
  expandedServers: Set<string>
  agentCounts: Record<string, number>
  onDoubleClickServer: (node: LayoutNode) => void
  currentFloorY: number
  targetPanX: number
  trapAlerts: Map<string, TrapAlert>
  linkDownAlerts: Map<string, TrapAlert>
  heatmapMode: boolean
  tempMap: Map<string, number>
}) {
  return (
    <>
      {/* Lighting */}
      <ambientLight intensity={0.4} />
      <directionalLight position={[20, 40, 20]} intensity={0.8} castShadow />
      <pointLight position={[0, 10, 0]} intensity={0.6} color="#3b82f6" distance={100} />

      {/* Background & Ground */}
      <Stars radius={200} depth={80} count={1500} factor={3} saturation={0.2} fade speed={0.5} />
      <Grid
        args={[200, 200]}
        position={[0, -15, 0]}
        cellSize={5}
        cellThickness={0.5}
        cellColor="#1e3a5f"
        sectionSize={25}
        sectionThickness={1}
        sectionColor="#2563eb"
        fadeDistance={150}
        fadeStrength={1.5}
        infiniteGrid
      />

      {/* Connection lines */}
      {links.map((link, i) => {
        const info = link.d2dInfo
        const isLinkDown = info
          ? linkDownAlerts.has(info.sourceIp) || linkDownAlerts.has(info.sourceName ?? '') ||
            linkDownAlerts.has(info.targetIp) || linkDownAlerts.has(info.targetName ?? '')
          : false
        return (
          <ConnectionLine key={`${link.sourceId}-${link.targetId}-${i}`} link={link} isLinkDown={isLinkDown} />
        )
      })}

      {/* Server nodes */}
      {nodes
        .filter((n) => n.type === 'server')
        .map((node) => (
          <ServerNode
            key={node.id}
            node={node}
            isSelected={selectedNode?.id === node.id}
            onSelect={onSelectNode}
            onHover={onHover}
            expanded={expandedServers.has(node.id)}
            agentCount={agentCounts[node.id] || 0}
            onDoubleClick={onDoubleClickServer}
            heatmapMode={heatmapMode}
            temperature={tempMap.get(node.agentId ?? '') ?? tempMap.get(node.ip ?? '') ?? undefined}
          />
        ))}

      {/* Agent nodes */}
      {(() => {
        const agentNodes = nodes.filter((n) => n.type === 'agent')
        const enableFloat = agentNodes.length < 50
        return agentNodes.map((node) => {
          const dt = ((node.deviceType ?? '') + ' ' + (node.agentId ?? '') + ' ' + (node.name ?? '')).toUpperCase()
          const isPDU = dt.includes('PDU') || dt.includes('FPDU') || dt.includes('UPS')
          const nodeTemp = tempMap.get(node.agentId ?? '') ?? tempMap.get(node.ip ?? '') ?? undefined
          return isPDU ? (
            <PDUNode
              key={node.id}
              node={node}
              isSelected={selectedNode?.id === node.id}
              onSelect={onSelectNode}
              onHover={onHover}
              heatmapMode={heatmapMode}
              temperature={nodeTemp}
            />
          ) : (
            <AgentNode
              key={node.id}
              node={node}
              isSelected={selectedNode?.id === node.id}
              onSelect={onSelectNode}
              onHover={onHover}
              useFloat={enableFloat}
              heatmapMode={heatmapMode}
              temperature={nodeTemp}
            />
          )
        })
      })()}

      {/* Device nodes (SNMP) — PDUs get their own vertical power-strip model */}
      {nodes
        .filter((n) => n.type === 'network')
        .map((node) => {
          const dt = ((node.deviceType ?? '') + ' ' + (node.agentId ?? '') + ' ' + (node.name ?? '')).toUpperCase()
          const isPDU = dt.includes('PDU') || dt.includes('FPDU') || dt.includes('UPS')
          const nodeTemp = tempMap.get(node.agentId ?? '') ?? tempMap.get(node.ip ?? '') ?? undefined
          return isPDU ? (
            <PDUNode
              key={node.id}
              node={node}
              isSelected={selectedNode?.id === node.id}
              onSelect={onSelectNode}
              onHover={onHover}
              heatmapMode={heatmapMode}
              temperature={nodeTemp}
            />
          ) : (
            <DeviceNode
              key={node.id}
              node={node}
              isSelected={selectedNode?.id === node.id}
              onSelect={onSelectNode}
              onHover={onHover}
              hasTrap={(!!node.ip && trapAlerts.has(node.ip)) || (!!node.name && trapAlerts.has(node.name))}
              heatmapMode={heatmapMode}
              temperature={nodeTemp}
            />
          )
        })}

      {/* Click on empty space to deselect */}
      <mesh position={[0, -14.9, 0]} rotation={[-Math.PI / 2, 0, 0]} onClick={onDeselect}>
        <planeGeometry args={[500, 500]} />
        <meshBasicMaterial transparent opacity={0} />
      </mesh>

      {heatmapMode
        ? <CameraTopDownController active={heatmapMode} />
        : <CameraFloorRunner targetFloorY={currentFloorY} targetPanX={targetPanX} />
      }

      {heatmapMode && <HeatmapGradientOverlay nodes={nodes} tempMap={tempMap} />}

      <OrbitControls
        makeDefault
        enablePan
        enableDamping
        dampingFactor={0.1}
        minDistance={15}
        maxDistance={2000}
      />
    </>
  )
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function Topology3D() {
  const mockData = USE_MOCK_DATA ? getMockTopologyData() : null
  const { data: realAgents, isLoading: realAgentsLoading } = useAgents()
  const { data: realServers, isLoading: realServersLoading } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 60000,
    enabled: !USE_MOCK_DATA,
  })
  const { data: realSnmpDevices } = useQuery({
    queryKey: ['snmp-devices-3d'],
    queryFn: () => api.getSNMPDevices(),
    staleTime: 15000,
    refetchInterval: USE_MOCK_DATA ? false : 15000,
    enabled: !USE_MOCK_DATA,
  })
  const { data: realTopologyLinks } = useQuery({
    queryKey: ['topology-links-3d'],
    queryFn: () => api.getTopologyLinks(),
    staleTime: 30000,
    refetchInterval: USE_MOCK_DATA ? false : 60000,
    enabled: !USE_MOCK_DATA,
  })

  // Fetch live devices from every sim-network container via the orchestrator
  const { data: orchDevices } = useQuery<{ container: string; data: { name: string; ip_address: string; type: string; vendor: string }[] }[]>({
    queryKey: ['orc-devices-3d'],
    queryFn: () => fetch('/orchestrator/devices').then(r => r.json()),
    staleTime: 30000,
    refetchInterval: 30000,
    enabled: !USE_MOCK_DATA,
  })

  // Fetch physical device-device wiring from the orchestrator topology
  const { data: orchTopology } = useQuery<{
    nodes: any[]
    edges: { src_ip: string; dst_ip: string; container: string }[]
  }>({
    queryKey: ['orc-topology-3d'],
    queryFn: () => fetch('/orchestrator/topology').then(r => r.json()),
    staleTime: 60000,
    refetchInterval: 60000,
    enabled: !USE_MOCK_DATA,
  })

  const agents = USE_MOCK_DATA ? mockData!.agents : realAgents
  const servers = USE_MOCK_DATA ? mockData!.servers : realServers
  const snmpDevices = USE_MOCK_DATA ? mockData!.snmpDevices : realSnmpDevices
  const agentsLoading = USE_MOCK_DATA ? false : realAgentsLoading
  const serversLoading = USE_MOCK_DATA ? false : realServersLoading
  const navigate = useNavigate()
  const [selectedNode, setSelectedNode] = useState<LayoutNode | null>(null)
  const [cursorPointer, setCursorPointer] = useState(false)
  const [currentFloor, setCurrentFloor] = useState(FLOORS.length - 1) // start at DCIM Servers (top)
  const [showLegend, setShowLegend] = useState(true)
  const [showStats, setShowStats] = useState(true)
  const [heatmapMode, setHeatmapMode] = useState(false)

  // Expand/collapse state
  const [expandedServers, setExpandedServers] = useState<Set<string>>(new Set())

  // Auto-expand all servers when heatmap activates so agent positions are in the layout
  const preHeatmapExpandRef = useRef<Set<string>>(new Set())
  useEffect(() => {
    if (heatmapMode && servers) {
      preHeatmapExpandRef.current = new Set(expandedServers)
      setExpandedServers(new Set(servers.filter(s => s.enabled).map(s => `server-${s.id}`)))
    } else if (!heatmapMode) {
      setExpandedServers(new Set(preHeatmapExpandRef.current))
    }
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [heatmapMode, servers])

  // ── SNMP trap state (mirrors 2D Topology) ────────────────────────────────
  const [trapAlerts, setTrapAlerts] = useState<Map<string, TrapAlert>>(new Map())
  const [linkDownAlerts, setLinkDownAlerts] = useState<Map<string, TrapAlert>>(new Map())
  const [trapFeed, setTrapFeed] = useState<TrapFeedItem[]>([])
  const [showTrapFeed, setShowTrapFeed] = useState(true)
  const trapTimeoutsRef = useRef<Map<string, ReturnType<typeof setTimeout>>>(new Map())

  const handleTrapEvent = useCallback((data: any) => {
    const ip: string | undefined = data.source_ip
    const name: string | undefined = data.device_name
    if (!ip && !name) return
    const alert: TrapAlert = {
      trapType: data.trap_type ?? '',
      severity: data.severity ?? 'critical',
      description: data.description ?? '',
      deviceName: name ?? '',
      timestamp: data.timestamp ?? new Date().toISOString(),
      ifaceIndex: data.iface_index != null ? Number(data.iface_index) : undefined,
    }
    const keys = [ip, name].filter(Boolean) as string[]
    keys.forEach(k => { const t = trapTimeoutsRef.current.get(k); if (t) clearTimeout(t) })
    setTrapAlerts(prev => {
      const next = new Map(prev)
      if (ip) next.set(ip, alert)
      if (name) next.set(name, alert)
      return next
    })
    const isLinkDown = (data.trap_type ?? '').toUpperCase().includes('LINK_DOWN')
    if (isLinkDown) {
      setLinkDownAlerts(prev => {
        const next = new Map(prev)
        if (ip) next.set(ip, alert)
        if (name) next.set(name, alert)
        return next
      })
    }
    setTrapFeed(prev => [{
      id: Date.now(), sourceIp: ip ?? '', deviceName: name ?? '',
      trapType: data.trap_type ?? '', severity: data.severity ?? 'critical',
      description: data.description ?? '', timestamp: data.timestamp ?? new Date().toISOString(),
    }, ...prev].slice(0, 30))
    const TRAP_TTL = Math.max(60_000, 5 * 60 * 1000)
    const timeout = setTimeout(() => {
      setTrapAlerts(prev => { const next = new Map(prev); keys.forEach(k => next.delete(k)); return next })
      if (isLinkDown) setLinkDownAlerts(prev => { const next = new Map(prev); keys.forEach(k => next.delete(k)); return next })
      keys.forEach(k => trapTimeoutsRef.current.delete(k))
    }, TRAP_TTL)
    keys.forEach(k => trapTimeoutsRef.current.set(k, timeout))
  }, [])

  useSSE('trap', handleTrapEvent, !USE_MOCK_DATA)

  // Seed from existing traps in DB on mount + every 30s
  const { data: activeTraps } = useQuery({
    queryKey: ['active-traps-seed-3d'],
    queryFn: () => api.getTraps({ limit: 200 }),
    staleTime: Infinity,
    refetchInterval: 30000,
    enabled: !USE_MOCK_DATA,
  })

  // Heatmap temperature data
  const { data: heatTempMetrics } = useQuery({
    queryKey: ['heatmap-temps', heatmapMode],
    queryFn: () => api.getMetrics({ metric_type: 'temperature', time_range: '1h', limit: 5000 }),
    enabled: heatmapMode,
    refetchInterval: heatmapMode ? 30000 : false,
    staleTime: 20000,
  })

  useEffect(() => {
    const traps: any[] = (activeTraps as any[]) ?? []
    if (!traps.length) return
    setTrapAlerts(prev => {
      const next = new Map(prev)
      traps.forEach(t => {
        const a: TrapAlert = { trapType: t.trap_type ?? '', severity: t.severity ?? 'critical', description: t.description ?? '', deviceName: t.device_name ?? '', timestamp: t.timestamp ?? new Date().toISOString() }
        if (t.source_ip) next.set(t.source_ip, a)
        if (t.device_name) next.set(t.device_name, a)
      })
      return next
    })
    setLinkDownAlerts(prev => {
      const next = new Map(prev)
      traps.filter(t => (t.trap_type ?? '').toUpperCase().includes('LINK_DOWN')).forEach(t => {
        const a: TrapAlert = { trapType: t.trap_type ?? '', severity: t.severity ?? 'critical', description: t.description ?? '', deviceName: t.device_name ?? '', timestamp: t.timestamp ?? new Date().toISOString() }
        if (t.source_ip) next.set(t.source_ip, a)
        if (t.device_name) next.set(t.device_name, a)
      })
      return next
    })
    setTrapFeed(traps.slice(0, 30).map((t, i) => ({
      id: i, sourceIp: t.source_ip ?? '', deviceName: t.device_name ?? '',
      trapType: t.trap_type ?? '', severity: t.severity ?? 'critical',
      description: t.description ?? '', timestamp: t.timestamp ?? new Date().toISOString(),
    })))
  }, [activeTraps])

  const toggleServerExpansion = useCallback((node: LayoutNode) => {
    setExpandedServers(prev => {
      const next = new Set(prev)
      if (next.has(node.id)) {
        next.delete(node.id)
      } else {
        next.add(node.id)
      }
      return next
    })
  }, [])

  const expandAll = useCallback(() => {
    const allServerIds = servers?.filter(s => s.enabled).map(s => `server-${s.id}`) || []
    setExpandedServers(new Set(allServerIds))
  }, [servers])

  const collapseAll = useCallback(() => {
    setExpandedServers(new Set())
  }, [])

  // Map server_id → orchestrator device list (same logic as 2D Topology.tsx)
  const orchDevicesByServerId = useMemo(() => {
    if (!orchDevices || !servers) return undefined
    const map = new Map<string, { name: string; ip_address: string; type: string; vendor: string }[]>()
    for (const srv of servers) {
      if (!srv.enabled) continue
      const network = srv.metadata?.network as string | undefined  // e.g. "network-a"
      if (!network) continue
      const simName = 'sim-' + network                             // "sim-network-a"
      const entry = orchDevices.find(o => o.container === simName)
      if (entry && srv.id) map.set(srv.id, entry.data)
    }
    return map.size > 0 ? map : undefined
  }, [orchDevices, servers])

  // Compute agent counts per server — prefer orchestrator device count when available
  const agentCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    if (orchDevicesByServerId) {
      for (const [rawId, devs] of orchDevicesByServerId) {
        counts[`server-${rawId}`] = devs.length
      }
      // Fallback for servers without orchestrator data
      agents?.forEach(a => {
        const sid = `server-${a.server_id}`
        if (!counts[sid]) counts[sid] = (counts[sid] || 0) + 1
      })
    } else {
      agents?.forEach(a => {
        const sid = `server-${a.server_id}`
        counts[sid] = (counts[sid] || 0) + 1
      })
    }
    return counts
  }, [agents, orchDevicesByServerId])

  // Temperature map for heatmap mode — latest reading per agent_id
  const tempMap = useMemo(() => {
    const map = new Map<string, number>()
    if (!heatTempMetrics) return map
    const sorted = [...heatTempMetrics].sort(
      (a, b) => new Date(b.timestamp).getTime() - new Date(a.timestamp).getTime()
    )
    sorted.forEach(m => { if (!map.has(m.agent_id)) map.set(m.agent_id, m.value) })
    return map
  }, [heatTempMetrics])

  // Filter to expanded servers and compute 3D layout
  const layout = useMemo(() => {
    if (!servers || !agents) return { nodes: [], links: [] }
    const visibleAgents = agents.filter(a => expandedServers.has(`server-${a.server_id}`))
    const visibleDevices = (snmpDevices || []).filter(
      d => expandedServers.has(`server-${d.server_id}`)
    )
    const effectiveLinks = USE_MOCK_DATA ? (mockData?.topologyLinks || []) : (realTopologyLinks || [])

    // Build a filtered orchDevicesByServerId with only expanded servers
    let visibleOrchDevices: typeof orchDevicesByServerId
    if (orchDevicesByServerId) {
      const filtered = new Map<string, { name: string; ip_address: string; type: string; vendor: string }[]>()
      for (const [sid, devs] of orchDevicesByServerId) {
        if (expandedServers.has(`server-${sid}`)) filtered.set(sid, devs)
      }
      if (filtered.size > 0) visibleOrchDevices = filtered
    }

    // Filter orchestrator topology edges to only those whose containers are expanded
    const expandedNetworks = new Set<string>()
    if (orchDevicesByServerId) {
      for (const sid of expandedServers) {
        const rawId = sid.replace(/^server-/, '')
        if (orchDevicesByServerId.has(rawId)) {
          const srv = servers.find(s => `server-${s.id}` === sid)
          if (srv?.metadata?.network) expandedNetworks.add('sim-' + srv.metadata.network)
        }
      }
    }
    const visibleOrchEdges = orchTopology?.edges.filter(
      e => !e.container || expandedNetworks.size === 0 || expandedNetworks.has(e.container)
    )

    return computeHierarchicalLayout(servers, visibleAgents, visibleDevices, effectiveLinks, visibleOrchDevices, visibleOrchEdges)
  }, [servers, agents, expandedServers, snmpDevices, realTopologyLinks, mockData, orchDevicesByServerId, orchTopology])

  // ── Joystick-driven camera panning ──
  const panVelocityRef = useRef({ x: 0, y: 0 })
  const [panX, setPanX] = useState(0)
  const [panYOffset, setPanYOffset] = useState(0)

  useEffect(() => {
    let frame: number
    let last = performance.now()
    const tick = (now: number) => {
      const dt = Math.min((now - last) / 1000, 0.05)
      last = now
      const vx = panVelocityRef.current.x
      const vy = panVelocityRef.current.y
      if (Math.abs(vx) > 0.01) {
        setPanX(prev => prev + vx * dt * 400)
      }
      if (Math.abs(vy) > 0.01) {
        setPanYOffset(prev => prev + vy * dt * 120)
      }
      frame = requestAnimationFrame(tick)
    }
    frame = requestAnimationFrame(tick)
    return () => cancelAnimationFrame(frame)
  }, [])

  const handleJoystickVelocity = useCallback((vx: number, vy: number) => {
    panVelocityRef.current = { x: vx, y: vy }
  }, [])

  const resetPan = useCallback(() => {
    setPanX(0)
    setPanYOffset(0)
  }, [])

  const handleSelectNode = useCallback((node: LayoutNode) => {
    setSelectedNode((prev) => (prev?.id === node.id ? null : node))
  }, [])

  const handleDeselect = useCallback(() => {
    setSelectedNode(null)
  }, [])

  const handleHover = useCallback((hovering: boolean) => {
    setCursorPointer(hovering)
  }, [])

  const isLoading = agentsLoading || serversLoading

  if (isLoading) {
    return (
      <div className="flex items-center justify-center h-full">
        <div className="flex flex-col items-center gap-4">
          <div className="w-12 h-12 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
          <div className="text-slate-400">Loading 3D topology...</div>
        </div>
      </div>
    )
  }

  const onlineAgents = agents?.filter((a) => a.status === 'online').length || 0
  const offlineAgents = agents?.filter((a) => a.status === 'offline').length || 0
  const enabledServers = servers?.filter((s) => s.enabled) || []
  const offlineServers = enabledServers.filter((s) => s.health?.status !== 'healthy').length
  const hasExpanded = expandedServers.size > 0

  return (
    <div className="h-full flex flex-col space-y-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex-1">
          <h1 className="text-4xl font-bold text-white flex items-center gap-3">
            <Box className="w-9 h-9 text-blue-400" />
            3D Network Topology
          </h1>
          <p className="text-slate-400 mt-2 text-lg">
            Interactive 3D visualization — drag to rotate, scroll to zoom, double-click a server to expand
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={hasExpanded ? collapseAll : expandAll}
            className="flex items-center gap-2 px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors font-medium border border-white/10"
            title={hasExpanded ? 'Collapse all servers' : 'Expand all servers'}
          >
            {hasExpanded ? <ChevronsDownUp className="w-4 h-4" /> : <ChevronsUpDown className="w-4 h-4" />}
            {hasExpanded ? 'Collapse All' : 'Expand All'}
          </button>
          {!showTrapFeed && (
              <button
                onClick={() => setShowTrapFeed(true)}
                className="flex items-center gap-2 px-3 py-2 bg-red-900/40 hover:bg-red-900/60 border border-red-500/40 text-red-300 rounded-lg transition-colors font-medium relative text-sm"
              >
                <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                Traps
                {trapFeed.length > 0 && (
                  <span className="absolute -top-1 -right-1 w-4 h-4 rounded-full bg-red-500 text-white text-[9px] font-bold flex items-center justify-center">
                    {trapFeed.length > 9 ? '9+' : trapFeed.length}
                  </span>
                )}
              </button>
            )}
          <button
            onClick={() => setHeatmapMode(h => !h)}
            className={`flex items-center gap-2 px-4 py-2 rounded-lg transition-colors font-medium border text-sm ${
              heatmapMode
                ? 'bg-orange-500/20 border-orange-500/50 text-orange-300 hover:bg-orange-500/30'
                : 'bg-slate-700 hover:bg-slate-600 text-white border-white/10'
            }`}
            title="Toggle temperature heatmap"
          >
            <Thermometer className="w-4 h-4" />
            Heatmap
          </button>
          <button
            onClick={() => navigate('/app/topology')}
            className="flex items-center gap-2 px-4 py-2 bg-slate-700 hover:bg-slate-600 text-white rounded-lg transition-colors font-medium border border-white/10"
          >
            2D View
          </button>
        </div>
      </div>

      <div className="flex-1 flex gap-4">
        {/* 3D Canvas */}
        <div
          className="flex-1 bg-slate-900/80 backdrop-blur-sm border border-white/10 rounded-xl overflow-hidden relative"
          style={{ cursor: cursorPointer ? 'pointer' : 'grab' }}
        >
          <Canvas
            camera={{ position: [0, 80, 160], fov: 50, near: 0.1, far: 2000 }}
            style={{ background: 'transparent' }}
            onPointerMissed={handleDeselect}
          >
            <SceneContent
              nodes={layout.nodes}
              links={layout.links}
              selectedNode={selectedNode}
              onSelectNode={handleSelectNode}
              onHover={handleHover}
              onDeselect={handleDeselect}
              expandedServers={expandedServers}
              agentCounts={agentCounts}
              onDoubleClickServer={toggleServerExpansion}
              currentFloorY={FLOORS[currentFloor].y + panYOffset}
              targetPanX={panX}
              trapAlerts={trapAlerts}
              linkDownAlerts={linkDownAlerts}
              heatmapMode={heatmapMode}
              tempMap={tempMap}
            />
          </Canvas>

          {/* Legend overlay */}
          {showLegend && (
          <div className="absolute bottom-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm">
            <div className="flex items-center justify-between mb-3">
              <h3 className="text-sm font-semibold text-white">Legend</h3>
              <button
                onClick={() => setShowLegend(false)}
                className="ml-3 p-1 -mr-1 rounded hover:bg-white/10 text-slate-400 hover:text-white transition-colors"
                aria-label="Close legend"
              >
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="space-y-2 text-xs">
              <div className="flex items-center gap-2">
                <div className="w-4 h-6 rounded-sm bg-sky-300 border border-sky-400 relative">
                  <div className="absolute top-0 left-0 right-0 h-1 bg-purple-500 rounded-t-sm" />
                  <div className="absolute bottom-0.5 left-0.5 right-0.5 h-0.5 bg-white/80 rounded-sm" />
                </div>
                <span className="text-slate-300">Server Rack (Healthy)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-4 h-6 rounded-sm bg-red-300 border border-red-400 shadow-[0_0_6px_rgba(239,68,68,0.4)] relative">
                  <div className="absolute top-0 left-0 right-0 h-1 bg-red-600 rounded-t-sm" />
                  <div className="absolute bottom-0.5 left-0.5 right-0.5 h-0.5 bg-white/80 rounded-sm" />
                </div>
                <span className="text-slate-300">Server Rack (Offline)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-3 rounded-sm bg-blue-400 border border-blue-300 relative">
                  <div className="absolute top-0.5 left-0.5 w-1 h-1.5 rounded-sm bg-white" />
                  <div className="absolute top-0.5 left-2 w-1 h-1.5 rounded-sm bg-white" />
                  <div className="absolute top-1 right-0.5 w-1 h-1 rounded-full bg-green-400" />
                </div>
                <span className="text-slate-300">Agent Server (Online)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-3 rounded-sm bg-red-400 border border-red-300 shadow-[0_0_6px_rgba(239,68,68,0.3)] relative">
                  <div className="absolute top-0.5 left-0.5 w-1 h-1.5 rounded-sm bg-white" />
                  <div className="absolute top-0.5 left-2 w-1 h-1.5 rounded-sm bg-white" />
                  <div className="absolute top-1 right-0.5 w-1 h-1 rounded-full bg-red-600" />
                </div>
                <span className="text-slate-300">Agent Server (Offline)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-0.5 bg-green-500" />
                <span className="text-slate-300">Healthy Link</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-0.5 border-t-2 border-dashed border-red-500" />
                <span className="text-slate-300">Disconnected Link</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 rounded-full bg-cyan-600 border-2 border-cyan-300 shadow-[0_0_6px_rgba(6,182,212,0.5)]" />
                <span className="text-slate-300">SNMP Device (Active)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-4 h-4 rounded-full bg-slate-700 border-2 border-slate-500" />
                <span className="text-slate-300">SNMP Device (Inactive)</span>
              </div>
              <div className="flex items-center gap-2">
                <div className="w-6 h-0.5 border-t-2 border-dashed border-cyan-400 opacity-60" />
                <span className="text-slate-300">Device Link</span>
              </div>
              <div className="mt-2 pt-2 border-t border-white/10 space-y-1">
                <p className="text-slate-500 text-[10px] font-semibold uppercase tracking-wide">Hierarchy</p>
                {[
                  { label: 'L1', name: 'DCIM Servers',    color: '#a855f7' },
                  { label: 'L2', name: 'Core Routers',    color: '#ef4444' },
                  { label: 'L3', name: 'Agg Switches',    color: '#f59e0b' },
                  { label: 'L4', name: 'ToR Switches',    color: '#06b6d4' },
                  { label: 'L5', name: 'Compute/Srvs',    color: '#10b981' },
                  { label: 'L6', name: 'Infrastructure',  color: '#64748b' },
                ].map(tier => (
                  <div key={tier.label} className="flex items-center gap-2">
                    <span className="text-[10px] font-mono w-5" style={{ color: tier.color }}>{tier.label}</span>
                    <span className="text-slate-400 text-[10px]">{tier.name}</span>
                  </div>
                ))}
                <p className="text-slate-400 text-[10px] pt-1">Double-click a server to expand</p>
              </div>
            </div>
          </div>
          )}

          {/* Temperature heatmap legend */}
          {heatmapMode && (
            <div
              className="absolute bottom-4 bg-slate-900/90 border border-orange-500/30 rounded-lg p-3 backdrop-blur-sm"
              style={{ left: showLegend ? 'calc(16px + 200px + 12px)' : '16px' }}
            >
              <h3 className="text-xs font-semibold text-orange-300 mb-2 flex items-center gap-1">
                <Thermometer className="w-3 h-3" /> Temperature
              </h3>
              <div className="space-y-1">
                {[
                  { color: '#3b82f6', label: '< 30°C',   desc: 'Cool'     },
                  { color: '#10b981', label: '30–45°C',  desc: 'Normal'   },
                  { color: '#f59e0b', label: '45–55°C',  desc: 'Warm'     },
                  { color: '#f97316', label: '55–65°C',  desc: 'Hot'      },
                  { color: '#ef4444', label: '> 65°C',   desc: 'Critical' },
                ].map(item => (
                  <div key={item.label} className="flex items-center gap-2">
                    <div className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: item.color }} />
                    <span className="text-slate-300 text-[10px]">{item.label}</span>
                    <span className="text-slate-500 text-[10px]">{item.desc}</span>
                  </div>
                ))}
              </div>
              {tempMap.size === 0 && heatmapMode && (
                <p className="text-slate-500 text-[10px] mt-2 border-t border-white/10 pt-1">
                  No temperature data in last 1h
                </p>
              )}
              <p className="text-slate-500 text-[10px] mt-1">{tempMap.size} readings</p>
            </div>
          )}

          {/* Joystick + Reset — bottom right */}
          <div className="absolute bottom-4 right-4 flex items-end gap-3 z-10">
            <JoystickNav onVelocity={handleJoystickVelocity} />
            <button
              onClick={resetPan}
              className="w-9 h-9 flex items-center justify-center rounded-lg transition-all duration-200"
              style={{
                background: panX === 0 && panYOffset === 0
                  ? 'rgba(30,41,59,0.8)'
                  : 'rgba(6,182,212,0.15)',
                border: panX === 0 && panYOffset === 0
                  ? '1px solid rgba(100,116,139,0.3)'
                  : '1px solid rgba(6,182,212,0.4)',
                boxShadow: panX === 0 && panYOffset === 0
                  ? 'none'
                  : '0 0 10px rgba(6,182,212,0.2)',
              }}
              title="Reset camera to center"
            >
              <svg width="16" height="16" viewBox="0 0 16 16" fill="none" xmlns="http://www.w3.org/2000/svg">
                <circle cx="8" cy="8" r="6.5" stroke={panX === 0 && panYOffset === 0 ? '#64748b' : '#22d3ee'} strokeWidth="1" />
                <circle cx="8" cy="8" r="1.5" fill={panX === 0 && panYOffset === 0 ? '#64748b' : '#22d3ee'} />
                <line x1="8" y1="0.5" x2="8" y2="3.5" stroke={panX === 0 && panYOffset === 0 ? '#64748b' : '#22d3ee'} strokeWidth="1" />
                <line x1="8" y1="12.5" x2="8" y2="15.5" stroke={panX === 0 && panYOffset === 0 ? '#64748b' : '#22d3ee'} strokeWidth="1" />
                <line x1="0.5" y1="8" x2="3.5" y2="8" stroke={panX === 0 && panYOffset === 0 ? '#64748b' : '#22d3ee'} strokeWidth="1" />
                <line x1="12.5" y1="8" x2="15.5" y2="8" stroke={panX === 0 && panYOffset === 0 ? '#64748b' : '#22d3ee'} strokeWidth="1" />
              </svg>
            </button>
          </div>

          {/* Floor Navigator */}
          <div className="absolute right-4 top-1/2 -translate-y-1/2 flex flex-col items-center gap-2 bg-slate-900/90 border border-white/20 rounded-xl p-3 backdrop-blur-sm select-none z-10">
            <button
              onClick={() => { setCurrentFloor(f => Math.min(f + 1, FLOORS.length - 1)); setPanYOffset(0) }}
              disabled={currentFloor === FLOORS.length - 1}
              className="w-9 h-9 flex items-center justify-center rounded-lg bg-slate-700 hover:bg-slate-600 disabled:opacity-25 disabled:cursor-not-allowed text-white transition-colors"
              title="Go up one floor"
            >
              <ChevronUp className="w-5 h-5" />
            </button>

            {/* Floor dots (rendered top→bottom visually = high→low Y) */}
            <div className="flex flex-col items-center gap-2 py-1">
              {[...FLOORS].reverse().map((floor, ri) => {
                const fi = FLOORS.length - 1 - ri
                const isActive = fi === currentFloor
                return (
                  <button
                    key={fi}
                    onClick={() => { setCurrentFloor(fi); setPanYOffset(0) }}
                    title={floor.name}
                    className={`rounded-full transition-all duration-200 ${
                      isActive
                        ? 'w-3.5 h-3.5 ring-2 ring-white/50 shadow-lg'
                        : 'w-2.5 h-2.5 opacity-40 hover:opacity-70'
                    }`}
                    style={{ backgroundColor: floor.color }}
                  />
                )
              })}
            </div>

            <button
              onClick={() => { setCurrentFloor(f => Math.max(f - 1, 0)); setPanYOffset(0) }}
              disabled={currentFloor === 0}
              className="w-9 h-9 flex items-center justify-center rounded-lg bg-slate-700 hover:bg-slate-600 disabled:opacity-25 disabled:cursor-not-allowed text-white transition-colors"
              title="Go down one floor"
            >
              <ChevronDown className="w-5 h-5" />
            </button>

            {/* Current floor label */}
            <div className="text-center mt-1 border-t border-white/10 pt-2 w-full">
              <div
                className="text-xs font-bold"
                style={{ color: FLOORS[currentFloor].color }}
              >
                {FLOORS[currentFloor].label}
              </div>
              <div className="text-[10px] text-slate-400 leading-tight max-w-[56px] text-center">
                {FLOORS[currentFloor].name}
              </div>
            </div>
          </div>

          {/* Live Trap Feed */}
          {showTrapFeed && (
          <div className="absolute top-16 right-4 w-72 max-h-80 bg-slate-900/95 border border-red-500/30 rounded-lg backdrop-blur-sm flex flex-col overflow-hidden z-10">
            <div className="flex items-center justify-between px-3 py-2 border-b border-white/10 shrink-0">
              <div className="flex items-center gap-2">
                <span className="w-2 h-2 rounded-full bg-red-500 animate-pulse" />
                <span className="text-xs font-semibold text-white">Live SNMP Traps</span>
                {trapFeed.length > 0 && (
                  <span className="px-1.5 py-0.5 rounded-full text-[10px] font-bold bg-red-500/20 text-red-400 border border-red-500/30">
                    {trapFeed.length}
                  </span>
                )}
              </div>
              <button onClick={() => setShowTrapFeed(false)} className="text-slate-400 hover:text-white transition-colors">
                <X className="w-3 h-3" />
              </button>
            </div>
            <div className="overflow-y-auto flex-1">
              {trapFeed.length === 0 ? (
                <p className="text-slate-500 text-xs text-center py-4">Waiting for traps…</p>
              ) : (
                trapFeed.map(t => (
                  <div key={t.id} className="px-3 py-2 border-b border-white/5 hover:bg-white/5 transition-colors">
                    <div className="flex items-center justify-between gap-2">
                      <span className={`text-[10px] font-bold px-1.5 py-0.5 rounded ${
                        t.severity.toLowerCase() === 'critical' ? 'bg-red-500/20 text-red-400' :
                        t.severity.toLowerCase() === 'warning' ? 'bg-amber-500/20 text-amber-400' :
                        'bg-blue-500/20 text-blue-400'
                      }`}>{t.severity.toUpperCase()}</span>
                      <span className="text-[10px] text-slate-500 shrink-0">{new Date(t.timestamp).toLocaleTimeString()}</span>
                    </div>
                    <p className="text-xs font-medium text-white mt-0.5 truncate">{t.deviceName || t.sourceIp}</p>
                    <p className="text-[10px] text-slate-400 truncate">{t.trapType}</p>
                    {t.description && <p className="text-[10px] text-slate-500 truncate">{t.description}</p>}
                  </div>
                ))
              )}
            </div>
          </div>
          )}

          {/* Stats overlay */}
          {showStats && (
          <div className="absolute top-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm">
            <div className="flex justify-end mb-1">
              <button onClick={() => setShowStats(false)} className="text-slate-400 hover:text-white transition-colors" aria-label="Close stats">
                <X className="w-3.5 h-3.5" />
              </button>
            </div>
            <div className="space-y-2 text-sm">
              <div className="flex items-center gap-2">
                <Server className="w-4 h-4 text-purple-400" />
                <span className="text-slate-300">
                  Servers: <span className="font-bold text-white">{enabledServers.length}</span>
                </span>
                {offlineServers > 0 && (
                  <span className="px-2 py-0.5 rounded-full text-xs font-medium bg-red-500/20 text-red-400 border border-red-500/30 animate-pulse">
                    {offlineServers} down
                  </span>
                )}
              </div>
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-blue-400" />
                <span className="text-slate-300">
                  Agents: <span className="font-bold text-white">{agents?.length || 0}</span>
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-green-400" />
                <span className="text-slate-300">
                  Online: <span className="font-bold text-green-400">{onlineAgents}</span>
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-red-400" />
                <span className="text-slate-300">
                  Offline: <span className="font-bold text-red-400">{offlineAgents}</span>
                </span>
              </div>
              <div className="flex items-center gap-2">
                <Activity className="w-4 h-4 text-cyan-400" />
                <span className="text-slate-300">
                  Devices: <span className="font-bold text-cyan-400">{snmpDevices?.length || 0}</span>
                </span>
              </div>
              <div className="border-t border-white/10 pt-2 mt-2 flex items-center gap-2">
                <span className="text-slate-400 text-xs">
                  {expandedServers.size} / {enabledServers.length} expanded
                </span>
              </div>
            </div>
          </div>
          )}
        </div>

        {/* Detail panel */}
        {selectedNode && (
          <div className="w-80 bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-6 space-y-4">
            <div className="flex items-center justify-between">
              <h3 className="text-xl font-bold text-white">Node Details</h3>
              <button
                onClick={() => setSelectedNode(null)}
                className="text-slate-400 hover:text-white"
              >
                ✕
              </button>
            </div>

            <div className="space-y-4">
              <div>
                <p className="text-sm text-slate-400">Name</p>
                <p className="text-lg font-semibold text-white">{selectedNode.name}</p>
              </div>

              <div>
                <p className="text-sm text-slate-400">Type</p>
                <p className="text-lg font-semibold text-white capitalize">{selectedNode.type}</p>
              </div>

              <div>
                <p className="text-sm text-slate-400">Status</p>
                <span
                  className={`inline-flex items-center px-3 py-1 rounded-full text-xs font-medium ${
                    selectedNode.status === 'online'
                      ? 'bg-green-500/20 text-green-400 border border-green-500/30'
                      : 'bg-red-500/20 text-red-400 border border-red-500/30'
                  }`}
                >
                  {selectedNode.status.toUpperCase()}
                </span>
              </div>

              {selectedNode.ip && (
                <div>
                  <p className="text-sm text-slate-400">
                    {selectedNode.type === 'server' ? 'URL' : 'IP Address'}
                  </p>
                  <p className="text-base font-mono text-white">{selectedNode.ip}</p>
                </div>
              )}

              {selectedNode.type === 'agent' && selectedNode.metrics !== undefined && (
                <div>
                  <p className="text-sm text-slate-400">Total Metrics</p>
                  <p className="text-2xl font-bold text-blue-400">
                    {selectedNode.metrics.toLocaleString()}
                  </p>
                </div>
              )}

              {selectedNode.type === 'agent' &&
                selectedNode.alerts !== undefined &&
                selectedNode.alerts > 0 && (
                  <div>
                    <p className="text-sm text-slate-400">Active Alerts</p>
                    <p className="text-2xl font-bold text-red-400">{selectedNode.alerts}</p>
                  </div>
                )}

              {selectedNode.type === 'server' && (
                <>
                  <div>
                    <p className="text-sm text-slate-400">Connected Agents</p>
                    <p className="text-2xl font-bold text-purple-400">
                      {agentCounts[selectedNode.id] || 0}
                    </p>
                  </div>
                  <button
                    onClick={() => toggleServerExpansion(selectedNode)}
                    className="block w-full text-center px-4 py-2 bg-indigo-500/20 hover:bg-indigo-500/30 border border-indigo-500/30 text-indigo-400 rounded-lg transition-colors"
                  >
                    {expandedServers.has(selectedNode.id) ? 'Collapse Agents' : 'Expand Agents'}
                  </button>
                </>
              )}

              {selectedNode.type === 'agent' && selectedNode.deviceType && (
                <div>
                  <p className="text-sm text-slate-400">Device Type</p>
                  <p className="text-sm font-mono text-amber-300">
                    {selectedNode.deviceType.replace('DeviceType.', '')}
                  </p>
                </div>
              )}

              {selectedNode.type === 'agent' && selectedNode.serverName && (
                <div>
                  <p className="text-sm text-slate-400">Server</p>
                  <p className="text-base font-semibold text-purple-400">{selectedNode.serverName}</p>
                </div>
              )}

              {selectedNode.type === 'network' && selectedNode.agentName && (
                <div>
                  <p className="text-sm text-slate-400">Monitored By</p>
                  <p className="text-base font-semibold text-cyan-400">{selectedNode.agentName}</p>
                </div>
              )}

              {selectedNode.type === 'network' && selectedNode.lastSeen && (
                <div>
                  <p className="text-sm text-slate-400">Last Seen</p>
                  <p className="text-sm font-mono text-slate-300">
                    {new Date(selectedNode.lastSeen).toLocaleString()}
                  </p>
                </div>
              )}

              {selectedNode.type === 'agent' && (
                <button
                  onClick={() => navigate(`/app/agents/${selectedNode.agentId || selectedNode.id}`)}
                  className="block w-full text-center px-4 py-2 bg-blue-500/20 hover:bg-blue-500/30 border border-blue-500/30 text-blue-400 rounded-lg transition-colors"
                >
                  View Agent Details
                </button>
              )}
            </div>
          </div>
        )}
      </div>
    </div>
  )
}
