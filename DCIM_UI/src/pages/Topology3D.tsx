import { useMemo, useState, useRef, useCallback, useEffect } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Billboard, Text, Line, Grid, Stars, Float, Html } from '@react-three/drei'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { useActiveTrapStream } from '@/hooks/useActiveTrapStream'
import { api } from '@/lib/api'
import { computeHierarchicalLayout, type LayoutNode, type LayoutLink } from '@/lib/topology3d-layout'
import { useNavigate } from 'react-router-dom'
import { Activity, Server, Box, ChevronsDownUp, ChevronsUpDown, ChevronUp, ChevronDown } from 'lucide-react'
import * as THREE from 'three'
import { getMockTopologyData } from '@/lib/topology-mock-data'

// ── Toggle this to use 500+ node mock data for testing ──
const USE_MOCK_DATA = false

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
}: {
  node: LayoutNode
  isSelected: boolean
  onSelect: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
  expanded: boolean
  agentCount: number
  onDoubleClick: (node: LayoutNode) => void
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
        <meshStandardMaterial color="#7dd3fc" metalness={0.5} roughness={0.3} />
      </mesh>
      {/* Left side panel */}
      <mesh position={[-3, 0, 0]}>
        <boxGeometry args={[0.1, 12, 3.7]} />
        <meshStandardMaterial color="#7dd3fc" metalness={0.45} roughness={0.35} />
      </mesh>
      {/* Right side panel */}
      <mesh position={[3, 0, 0]}>
        <boxGeometry args={[0.1, 12, 3.7]} />
        <meshStandardMaterial color="#7dd3fc" metalness={0.45} roughness={0.35} />
      </mesh>
      {/* Top panel */}
      <mesh position={[0, 6.05, 0]}>
        <boxGeometry args={[6, 0.1, 3.7]} />
        <meshStandardMaterial color="#7dd3fc" metalness={0.45} roughness={0.35} />
      </mesh>
      {/* Bottom panel */}
      <mesh position={[0, -6.05, 0]}>
        <boxGeometry args={[6, 0.1, 3.7]} />
        <meshStandardMaterial color="#7dd3fc" metalness={0.45} roughness={0.35} />
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

      {/* ── Glow effect (hover/offline pulse) ── */}
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
}: {
  node: LayoutNode
  isSelected: boolean
  onSelect: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
  useFloat?: boolean
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
          color={bodyColor}
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
  trapBadge,
}: {
  node: LayoutNode
  isSelected: boolean
  onSelect: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
  trapBadge?: { label: string; color: string; count: number } | null
}) {
  const glowRef = useRef<THREE.Mesh>(null)
  const [hovered, setHovered] = useState(false)

  useFrame(() => {
    if (!glowRef.current) return
    const mat = glowRef.current.material as THREE.MeshBasicMaterial
    if (node.status === 'offline') {
      // Red pulse when not responding — matches server behavior.
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
  // Blue chassis when responding, red when not — matches the server's online/offline palette.
  const chassisColor = online ? '#7dd3fc' : '#b91c1c'
  const accentColor = online ? '#3b82f6' : '#ef4444'
  const ledOn = online ? '#22d3ee' : '#fca5a5'
  const ledOff = online ? '#0ea5e9' : '#7f1d1d'
  const labelColor = online ? '#93c5fd' : '#fca5a5'
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

      {trapBadge && (
        <Billboard position={[0, 2.1, 0]}>
          <Text
            fontSize={0.48}
            color={trapBadge.color}
            anchorX="center"
            anchorY="middle"
            outlineWidth={0.04}
            outlineColor="#000000"
          >
            ⚠ {trapBadge.label}{trapBadge.count > 1 ? ` +${trapBadge.count - 1}` : ''}
          </Text>
        </Billboard>
      )}
    </group>
  )
}

// ── Connection Line ──────────────────────────────────────────────────────────

function ConnectionLine({ link, brokenPairs }: { link: LayoutLink; brokenPairs: Set<string> }) {
  const ref = useRef<any>(null)
  const [hovered, setHovered] = useState(false)

  const isAgentLink = link.linkType === 'device-agent'
  const isD2D = link.linkType === 'device-device'
  const hasTrap = isD2D && link.d2dInfo != null &&
    brokenPairs.has([link.d2dInfo.sourceIp, link.d2dInfo.targetIp].sort().join('|'))

  useFrame(() => {
    if (ref.current && (isAgentLink || !link.connected || hasTrap)) {
      ref.current.material.dashOffset -= isAgentLink ? 0.02 : 0.05
    }
  })
  const color = hasTrap
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
    const faulted = hasTrap || info.sourceStatus === 'offline' || info.targetStatus === 'offline'
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
    } else if (hasTrap) {
      faultReason = 'SNMP linkDown trap'
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
        dashed={isAgentLink || !link.connected || hasTrap}
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

export const FLOORS = [
  { name: 'SNMP Devices',  label: 'L1', y: -22, color: '#06b6d4' },
  { name: 'Agent Servers', label: 'L2', y: -8,  color: '#3b82f6' },
  { name: 'Server Racks',  label: 'L3', y: 20,  color: '#a855f7' },
]

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
  brokenPairs,
  deviceTrapMap,
  selectedNode,
  onSelectNode,
  onHover,
  onDeselect,
  expandedServers,
  agentCounts,
  onDoubleClickServer,
  currentFloorY,
  targetPanX,
}: {
  nodes: LayoutNode[]
  links: LayoutLink[]
  brokenPairs: Set<string>
  deviceTrapMap: Map<string, { label: string; color: string; count: number }>
  selectedNode: LayoutNode | null
  onSelectNode: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
  onDeselect: () => void
  expandedServers: Set<string>
  agentCounts: Record<string, number>
  onDoubleClickServer: (node: LayoutNode) => void
  currentFloorY: number
  targetPanX: number
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
      {links.map((link, i) => (
        <ConnectionLine key={`${link.sourceId}-${link.targetId}-${i}`} link={link} brokenPairs={brokenPairs} />
      ))}

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
          />
        ))}

      {/* Agent nodes */}
      {(() => {
        const agentNodes = nodes.filter((n) => n.type === 'agent')
        const enableFloat = agentNodes.length < 50
        return agentNodes.map((node) => (
          <AgentNode
            key={node.id}
            node={node}
            isSelected={selectedNode?.id === node.id}
            onSelect={onSelectNode}
            onHover={onHover}
            useFloat={enableFloat}
          />
        ))
      })()}

      {/* Device nodes (SNMP) */}
      {nodes
        .filter((n) => n.type === 'network')
        .map((node) => (
          <DeviceNode
            key={node.id}
            node={node}
            isSelected={selectedNode?.id === node.id}
            onSelect={onSelectNode}
            onHover={onHover}
            trapBadge={node.ip ? (deviceTrapMap.get(node.ip) ?? null) : null}
          />
        ))}

      {/* Click on empty space to deselect */}
      <mesh position={[0, -14.9, 0]} rotation={[-Math.PI / 2, 0, 0]} onClick={onDeselect}>
        <planeGeometry args={[500, 500]} />
        <meshBasicMaterial transparent opacity={0} />
      </mesh>

      <CameraFloorRunner targetFloorY={currentFloorY} targetPanX={targetPanX} />

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
  // DB polling — reliable baseline (5s; SSE handles real-time on top)
  const { data: dbTraps } = useQuery({
    queryKey: ['snmp-traps-active-3d'],
    queryFn: () => api.getSNMPTraps({ resolved: false, limit: 500 }),
    staleTime: 0,
    refetchInterval: USE_MOCK_DATA ? false : 5000,
    enabled: !USE_MOCK_DATA,
  })
  // SSE stream — instant real-time updates
  const streamTraps = useActiveTrapStream()
  // Merge: keyed by server|ip|trapType|ifIndex so multiple interfaces stay separate
  const activeTraps = useMemo(() => {
    if (USE_MOCK_DATA) return []
    const OID_IF_DESCR = '1.3.6.1.2.1.2.2.1.2.'
    const ifIdx = (v?: Record<string, any>) => {
      if (!v) return ''
      for (const oid of Object.keys(v)) if (oid.startsWith(OID_IF_DESCR)) return oid.slice(OID_IF_DESCR.length)
      return ''
    }
    const map = new Map<string, any>()
    for (const t of (dbTraps || [])) map.set(`${t.server_id}|${t.source_ip}|${t.trap_type}|${ifIdx(t.varbinds)}`, t)
    for (const t of streamTraps) map.set(`${t.server_id}|${t.source_ip}|${t.trap_type}|${ifIdx(t.varbinds as any)}`, t)
    return Array.from(map.values())
  }, [dbTraps, streamTraps])
  const TRAP_BADGE_CFG: Record<string, { label: string; color: string }> = {
    coldStart:             { label: 'REBOOT',    color: '#60a5fa' },
    warmStart:             { label: 'RESTART',   color: '#22d3ee' },
    authenticationFailure: { label: 'AUTH FAIL', color: '#fb923c' },
    highTemperature:       { label: 'HIGH TEMP', color: '#f87171' },
    highCPU:               { label: 'HIGH CPU',  color: '#fbbf24' },
    highMemory:            { label: 'HIGH MEM',  color: '#fbbf24' },
    fanFailure:            { label: 'FAN FAIL',  color: '#facc15' },
    powerAlert:            { label: 'PWR FAIL',  color: '#f87171' },
    environmentalAlert:    { label: 'ENV ALERT', color: '#facc15' },
    thresholdRising:       { label: 'THRESHOLD', color: '#fbbf24' },
    enterpriseTrap:        { label: 'ALERT',     color: '#94a3b8' },
  }
  const TRAP_PRI: Record<string, number> = {
    authenticationFailure: 1, highTemperature: 1, powerAlert: 1,
    highCPU: 2, highMemory: 2, fanFailure: 2, environmentalAlert: 2,
    thresholdRising: 3, coldStart: 4, warmStart: 5,
  }

  const deviceTrapMap = useMemo(() => {
    const map = new Map<string, { label: string; color: string; count: number }>()
    const active = (activeTraps || []).filter(
      t => !t.resolved && t.trap_type !== 'linkDown' && t.trap_type !== 'linkUp' && t.trap_type !== 'thresholdFalling'
    )
    const byIp = new Map<string, typeof active>()
    for (const t of active) {
      if (!byIp.has(t.source_ip)) byIp.set(t.source_ip, [])
      byIp.get(t.source_ip)!.push(t)
    }
    for (const [ip, traps] of byIp) {
      traps.sort((a, b) => (TRAP_PRI[a.trap_type] ?? 6) - (TRAP_PRI[b.trap_type] ?? 6))
      const top = traps[0]
      const cfg = TRAP_BADGE_CFG[top.trap_type] || { label: 'ALERT', color: '#94a3b8' }
      map.set(ip, { ...cfg, count: traps.length })
    }
    return map
  }, [activeTraps])

  const brokenPairs = useMemo(() => {
    const pairs = new Set<string>()
    const validLinkPairs = new Set<string>()
    const effectiveLinks = USE_MOCK_DATA ? (mockData?.topologyLinks || []) : (realTopologyLinks || [])
    for (const tl of effectiveLinks) {
      validLinkPairs.add([tl.source_ip, tl.target_ip].sort().join('|'))
    }
    const linkDownTraps = (activeTraps || []).filter(t => t.trap_type === 'linkDown')
    for (let i = 0; i < linkDownTraps.length; i++) {
      for (let j = i + 1; j < linkDownTraps.length; j++) {
        const t1 = linkDownTraps[i], t2 = linkDownTraps[j]
        if (t1.source_ip === t2.source_ip) continue
        const pair = [t1.source_ip, t2.source_ip].sort().join('|')
        if (!validLinkPairs.has(pair)) continue
        const diff = Math.abs(new Date(t1.timestamp).getTime() - new Date(t2.timestamp).getTime())
        if (diff <= 30000) {
          pairs.add(pair)
        }
      }
    }
    return pairs
  }, [activeTraps, realTopologyLinks, mockData])

  const agents = USE_MOCK_DATA ? mockData!.agents : realAgents
  const servers = USE_MOCK_DATA ? mockData!.servers : realServers
  const snmpDevices = USE_MOCK_DATA ? mockData!.snmpDevices : realSnmpDevices
  const agentsLoading = USE_MOCK_DATA ? false : realAgentsLoading
  const serversLoading = USE_MOCK_DATA ? false : realServersLoading
  const navigate = useNavigate()
  const [selectedNode, setSelectedNode] = useState<LayoutNode | null>(null)
  const [cursorPointer, setCursorPointer] = useState(false)
  const [currentFloor, setCurrentFloor] = useState(2) // start at Server Racks (top)

  // Expand/collapse state
  const [expandedServers, setExpandedServers] = useState<Set<string>>(new Set())

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

  // Compute agent counts per server (always full list)
  const agentCounts = useMemo(() => {
    const counts: Record<string, number> = {}
    agents?.forEach(a => {
      const sid = `server-${a.server_id}`
      counts[sid] = (counts[sid] || 0) + 1
    })
    return counts
  }, [agents])

  // Filter agents to only expanded servers, then compute layout with devices
  const layout = useMemo(() => {
    if (!servers || !agents) return { nodes: [], links: [] }
    const visibleAgents = agents.filter(a => expandedServers.has(`server-${a.server_id}`))
    // Show every device whose parent server is expanded — this includes
    // SNMP-walker-discovered devices (agent_id="snmp-walker") that have no
    // matching agent record and would otherwise be filtered out.
    const visibleDevices = (snmpDevices || []).filter(
      d => expandedServers.has(`server-${d.server_id}`)
    )
    const effectiveLinks = USE_MOCK_DATA ? (mockData?.topologyLinks || []) : (realTopologyLinks || [])
    return computeHierarchicalLayout(servers, visibleAgents, visibleDevices, effectiveLinks)
  }, [servers, agents, expandedServers, snmpDevices, realTopologyLinks, mockData])

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
              brokenPairs={brokenPairs}
              deviceTrapMap={deviceTrapMap}
              selectedNode={selectedNode}
              onSelectNode={handleSelectNode}
              onHover={handleHover}
              onDeselect={handleDeselect}
              expandedServers={expandedServers}
              agentCounts={agentCounts}
              onDoubleClickServer={toggleServerExpansion}
              currentFloorY={FLOORS[currentFloor].y + panYOffset}
              targetPanX={panX}
            />
          </Canvas>

          {/* Legend overlay */}
          <div className="absolute bottom-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm">
            <h3 className="text-sm font-semibold text-white mb-3">Legend</h3>
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
              <div className="mt-2 pt-2 border-t border-white/10">
                <p className="text-slate-400">Double-click a server to expand/collapse its agents</p>
              </div>
            </div>
          </div>

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

          {/* Stats overlay */}
          <div className="absolute top-4 left-4 bg-slate-900/90 border border-white/20 rounded-lg p-4 backdrop-blur-sm">
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
