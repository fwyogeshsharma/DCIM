import { useMemo, useState, useRef, useCallback } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import { OrbitControls, RoundedBox, Billboard, Text, Line, Grid, Stars, Float } from '@react-three/drei'
import { useAgents } from '@/hooks/useAgents'
import { useQuery } from '@tanstack/react-query'
import { api } from '@/lib/api'
import { computeHierarchicalLayout, type LayoutNode, type LayoutLink } from '@/lib/topology3d-layout'
import { useNavigate } from 'react-router-dom'
import { Activity, Server, Box } from 'lucide-react'
import * as THREE from 'three'

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
}: {
  node: LayoutNode
  isSelected: boolean
  onSelect: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
}) {
  const groupRef = useRef<THREE.Group>(null)
  const glowRef = useRef<THREE.Mesh>(null)
  const [hovered, setHovered] = useState(false)

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

  const ledColor = node.status === 'offline' ? '#ef4444' : '#10b981'
  const accentColor = node.status === 'offline' ? '#991b1b' : (node.color || '#8b5cf6')
  const slotCount = 8

  return (
    <group
      ref={groupRef}
      position={node.position}
      onClick={handleClick}
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
}: {
  node: LayoutNode
  isSelected: boolean
  onSelect: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
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

      {/* ── Server name sub-label (distinguishes same-named agents across servers) ── */}
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
            OFFLINE
          </Text>
        </Billboard>
      )}
    </group>
  )

  // Online agents get a gentle float bob
  if (node.status === 'online') {
    return (
      <Float speed={2} rotationIntensity={0} floatIntensity={0.3} floatingRange={[-0.2, 0.2]}>
        {serverUnit}
      </Float>
    )
  }

  return serverUnit
}

// ── Connection Line ──────────────────────────────────────────────────────────

function ConnectionLine({ link }: { link: LayoutLink }) {
  const ref = useRef<any>(null)

  useFrame(() => {
    if (!link.connected && ref.current) {
      ref.current.material.dashOffset -= 0.05
    }
  })

  return (
    <Line
      ref={ref}
      points={[link.sourcePos, link.targetPos]}
      color={link.connected ? '#10b981' : '#ef4444'}
      lineWidth={link.connected ? 1.5 : 2}
      dashed={!link.connected}
      dashSize={1}
      gapSize={0.8}
      transparent
      opacity={link.connected ? 0.6 : 0.8}
    />
  )
}

// ── Scene Content ────────────────────────────────────────────────────────────

function SceneContent({
  nodes,
  links,
  selectedNode,
  onSelectNode,
  onHover,
  onDeselect,
}: {
  nodes: LayoutNode[]
  links: LayoutLink[]
  selectedNode: LayoutNode | null
  onSelectNode: (node: LayoutNode) => void
  onHover: (hovering: boolean) => void
  onDeselect: () => void
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
        <ConnectionLine key={`${link.sourceId}-${link.targetId}-${i}`} link={link} />
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
          />
        ))}

      {/* Agent nodes */}
      {nodes
        .filter((n) => n.type === 'agent')
        .map((node) => (
          <AgentNode
            key={node.id}
            node={node}
            isSelected={selectedNode?.id === node.id}
            onSelect={onSelectNode}
            onHover={onHover}
          />
        ))}

      {/* Click on empty space to deselect */}
      <mesh position={[0, -14.9, 0]} rotation={[-Math.PI / 2, 0, 0]} onClick={onDeselect}>
        <planeGeometry args={[500, 500]} />
        <meshBasicMaterial transparent opacity={0} />
      </mesh>

      <OrbitControls
        makeDefault
        enableDamping
        dampingFactor={0.1}
        minDistance={15}
        maxDistance={250}
      />
    </>
  )
}

// ── Main Page ────────────────────────────────────────────────────────────────

export default function Topology3D() {
  const { data: agents, isLoading: agentsLoading } = useAgents()
  const { data: servers, isLoading: serversLoading } = useQuery({
    queryKey: ['servers'],
    queryFn: () => api.getServers(),
    staleTime: 60000,
  })
  const navigate = useNavigate()
  const [selectedNode, setSelectedNode] = useState<LayoutNode | null>(null)
  const [cursorPointer, setCursorPointer] = useState(false)

  const layout = useMemo(() => {
    if (!servers || !agents) return { nodes: [], links: [] }
    return computeHierarchicalLayout(servers, agents)
  }, [servers, agents])

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
            Interactive 3D visualization — drag to rotate, scroll to zoom, right-click to pan
          </p>
        </div>
        <div className="flex items-center gap-2">
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
            camera={{ position: [0, 60, 100], fov: 50 }}
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
                <div>
                  <p className="text-sm text-slate-400">Connected Agents</p>
                  <p className="text-2xl font-bold text-purple-400">
                    {agents?.filter(
                      (a) => `server-${a.server_id}` === selectedNode.id
                    ).length || 0}
                  </p>
                </div>
              )}

              {selectedNode.type === 'agent' && selectedNode.serverName && (
                <div>
                  <p className="text-sm text-slate-400">Server</p>
                  <p className="text-base font-semibold text-purple-400">{selectedNode.serverName}</p>
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
