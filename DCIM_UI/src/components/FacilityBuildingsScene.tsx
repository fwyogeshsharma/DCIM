/**
 * FacilityBuildingsScene — physical facility, one building per network_id.
 *
 * Driven by the /facility/layout device rows (building → room → rack → devices).
 * Each network_id renders as its own building shell, placed left-to-right. Inside
 * a building the interior is partitioned into its real rooms; each room holds the
 * device rack cabinets labelled with their real rack number. Hovering (or
 * clicking) a rack surfaces the list of devices in that rack and which building
 * it belongs to.
 */

import { useState, useMemo, useEffect, Component, type ReactNode } from 'react'
import { Canvas, useThree } from '@react-three/fiber'
import { OrbitControls, Text, Billboard } from '@react-three/drei'
import * as THREE from 'three'
import type { FacilityBuilding, FacilityRoom, FacilityRack } from '@/lib/facilityLayout'

// ── WebGL Error Boundary ───────────────────────────────────────────────────────
class WebGLErrorBoundary extends Component<{ children: ReactNode }, { error: string | null }> {
  state = { error: null }
  static getDerivedStateFromError(e: Error) { return { error: e.message } }
  render() {
    if (this.state.error) {
      return (
        <div className="w-full rounded-xl border border-red-500/30 bg-slate-900 flex items-center justify-center" style={{ height: 680 }}>
          <div className="text-center px-6">
            <p className="text-red-400 font-semibold mb-2">3D Scene Error</p>
            <p className="text-slate-400 text-sm max-w-md">{this.state.error}</p>
            <button className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-500"
              onClick={() => this.setState({ error: null })}>Retry</button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

// ── Geometry constants ──────────────────────────────────────────────────────────
const FLOOR_H  = 6.0
const B        = { w: 50, d: 36 }                 // single-building footprint
const INTERIOR = { w: B.w - 9, d: B.d - 9 }
const GAP      = 18                                // gap between adjacent buildings
const RACK     = { w: 1.5, d: 1.1, h: 3.4 }

const STATUS_HEX = { online: '#22c55e', offline: '#ef4444' } as const
const BUILDING_TINT = ['#1d4ed8', '#7c3aed', '#0e7490', '#15803d', '#c2410c', '#b91c1c'] as const
const tintFor = (i: number) => BUILDING_TINT[i % BUILDING_TINT.length]

// ── Hovered/selected rack descriptor (for the side panel) ───────────────────────
export interface ActiveRack {
  buildingId: string
  buildingName: string
  room: string
  rack: FacilityRack
}

// ── Layout helpers ──────────────────────────────────────────────────────────────
interface RoomRect { name: string; x: number; z: number; w: number; d: number }

/** Partition the building interior into a grid of room cells (local coords). */
function layoutRooms(rooms: FacilityRoom[]): RoomRect[] {
  const n = Math.max(rooms.length, 1)
  const cols = Math.ceil(Math.sqrt(n))
  const rows = Math.ceil(n / cols)
  const gap = 1.6
  const cellW = (INTERIOR.w - gap * (cols - 1)) / cols
  const cellD = (INTERIOR.d - gap * (rows - 1)) / rows
  const startX = -INTERIOR.w / 2 + cellW / 2
  const startZ = -INTERIOR.d / 2 + cellD / 2
  return rooms.map((r, i) => {
    const c = i % cols
    const rr = Math.floor(i / cols)
    return { name: r.name, x: startX + c * (cellW + gap), z: startZ + rr * (cellD + gap), w: cellW, d: cellD }
  })
}

/** Even grid of XZ slots inside a room rect (padded from the walls). */
function slotsInRoom(room: RoomRect, count: number, pad = 1.2): [number, number][] {
  const n = Math.max(count, 1)
  const cols = Math.ceil(Math.sqrt(n))
  const rows = Math.ceil(n / cols)
  const iw = Math.max(room.w - pad * 2, 0.1)
  const id = Math.max(room.d - pad * 2, 0.1)
  const out: [number, number][] = []
  for (let i = 0; i < count; i++) {
    const c = i % cols
    const r = Math.floor(i / cols)
    const x = room.x - iw / 2 + (cols === 1 ? iw / 2 : (c / (cols - 1)) * iw)
    const z = room.z - id / 2 + (rows === 1 ? id / 2 : (r / (rows - 1)) * id)
    out.push([x, z])
  }
  return out
}

function rackStatus(rack: FacilityRack): 'online' | 'offline' {
  return rack.devices.some((d) => d.status === 'online') ? 'online' : 'offline'
}

// ── Building shell ──────────────────────────────────────────────────────────────
function BuildingShell({ name, subtitle, tint }: { name: string; subtitle: string; tint: string }) {
  return (
    <group>
      {/* Floor slab */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]}>
        <planeGeometry args={[B.w + 3, B.d + 3]} />
        <meshStandardMaterial color="#16263f" roughness={0.6} metalness={0.15} />
      </mesh>
      {/* Roof */}
      <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, FLOOR_H, 0]}>
        <planeGeometry args={[B.w, B.d]} />
        <meshStandardMaterial color="#0d1e30" roughness={0.9} side={THREE.DoubleSide} />
      </mesh>
      {/* Exterior walls */}
      {([
        { pos: [0, FLOOR_H / 2, -B.d / 2] as [number, number, number], sz: [B.w, FLOOR_H, 0.4] as [number, number, number] },
        { pos: [0, FLOOR_H / 2, B.d / 2] as [number, number, number], sz: [B.w, FLOOR_H, 0.4] as [number, number, number] },
        { pos: [-B.w / 2, FLOOR_H / 2, 0] as [number, number, number], sz: [0.4, FLOOR_H, B.d] as [number, number, number] },
        { pos: [B.w / 2, FLOOR_H / 2, 0] as [number, number, number], sz: [0.4, FLOOR_H, B.d] as [number, number, number] },
      ]).map(({ pos, sz }, i) => (
        <mesh key={i} position={pos}>
          <boxGeometry args={sz} />
          <meshStandardMaterial color={tint} roughness={0.7} metalness={0.2} transparent opacity={0.18} side={THREE.DoubleSide} />
        </mesh>
      ))}
      {/* Corner columns */}
      {([[-B.w / 2 + 1, -B.d / 2 + 1], [B.w / 2 - 1, -B.d / 2 + 1], [-B.w / 2 + 1, B.d / 2 - 1], [B.w / 2 - 1, B.d / 2 - 1]] as [number, number][]).map(([x, z], i) => (
        <mesh key={`col-${i}`} position={[x, FLOOR_H / 2, z]}>
          <cylinderGeometry args={[0.3, 0.3, FLOOR_H, 8]} />
          <meshStandardMaterial color="#4a7db5" metalness={0.8} roughness={0.2} />
        </mesh>
      ))}
      {/* Building nameplate */}
      <Billboard position={[0, FLOOR_H + 3.2, 0]}>
        <Text fontSize={2.0} color="#bfdbfe" anchorX="center" outlineWidth={0.06} outlineColor="#000">
          {name}
        </Text>
        {subtitle && (
          <Text position={[0, -1.6, 0]} fontSize={1.0} color="#7dd3fc" anchorX="center" outlineWidth={0.03} outlineColor="#000">
            {subtitle}
          </Text>
        )}
      </Billboard>
    </group>
  )
}

// ── Room partition ──────────────────────────────────────────────────────────────
function RoomCell({ rect, tint }: { rect: RoomRect; tint: string }) {
  const WALL_H = 1.4
  return (
    <group>
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[rect.x, 0.04, rect.z]}>
        <planeGeometry args={[rect.w, rect.d]} />
        <meshBasicMaterial color={tint} transparent opacity={0.1} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      {([
        { pos: [rect.x, WALL_H / 2, rect.z - rect.d / 2] as [number, number, number], sz: [rect.w, WALL_H, 0.1] as [number, number, number] },
        { pos: [rect.x, WALL_H / 2, rect.z + rect.d / 2] as [number, number, number], sz: [rect.w, WALL_H, 0.1] as [number, number, number] },
        { pos: [rect.x - rect.w / 2, WALL_H / 2, rect.z] as [number, number, number], sz: [0.1, WALL_H, rect.d] as [number, number, number] },
        { pos: [rect.x + rect.w / 2, WALL_H / 2, rect.z] as [number, number, number], sz: [0.1, WALL_H, rect.d] as [number, number, number] },
      ]).map((w, i) => (
        <mesh key={i} position={w.pos}>
          <boxGeometry args={w.sz} />
          <meshStandardMaterial color={tint} transparent opacity={0.28} roughness={0.5} metalness={0.3} />
        </mesh>
      ))}
      <Billboard position={[rect.x, WALL_H + 0.5, rect.z - rect.d / 2 + 0.3]}>
        <Text fontSize={0.55} color="#cfe3ff" anchorX="center" maxWidth={rect.w} textAlign="center" outlineWidth={0.02} outlineColor="#000">
          {rect.name}
        </Text>
      </Billboard>
    </group>
  )
}

// ── Rack cabinet (one per real rack) ────────────────────────────────────────────
function RackCabinet({
  rack, pos, active, onEnter, onLeave, onClick,
}: {
  rack: FacilityRack
  pos: [number, number]
  active: boolean
  onEnter: () => void
  onLeave: () => void
  onClick: () => void
}) {
  const st = rackStatus(rack)
  const color = STATUS_HEX[st]
  const [x, z] = pos
  // Stack a thin slot per device (capped) to read as a populated rack.
  const slotCount = Math.min(rack.devices.length, 10)
  return (
    <group position={[x, 0, z]}>
      {/* Cabinet body */}
      <mesh
        position={[0, RACK.h / 2, 0]}
        onPointerOver={(e) => { e.stopPropagation(); onEnter() }}
        onPointerOut={onLeave}
        onClick={(e) => { e.stopPropagation(); onClick() }}
      >
        <boxGeometry args={[RACK.w, RACK.h, RACK.d]} />
        <meshStandardMaterial color={active ? '#334b6b' : '#1f3554'} roughness={0.45} metalness={0.55} />
      </mesh>
      {/* Front device slots */}
      {Array.from({ length: slotCount }, (_, i) => {
        const d = rack.devices[i]
        const sy = 0.4 + i * ((RACK.h - 0.6) / Math.max(slotCount, 1))
        return (
          <mesh key={i} position={[0, sy, RACK.d / 2 + 0.01]}>
            <boxGeometry args={[RACK.w - 0.25, (RACK.h - 0.6) / Math.max(slotCount, 1) - 0.06, 0.04]} />
            <meshStandardMaterial
              color={d.status === 'online' ? '#2dd4bf' : '#7f1d1d'}
              emissive={d.status === 'online' ? '#0ea5a4' : '#450a0a'}
              emissiveIntensity={0.5}
              metalness={0.4}
              roughness={0.4}
            />
          </mesh>
        )
      })}
      {/* Active highlight outline */}
      {active && (
        <mesh position={[0, RACK.h / 2, 0]}>
          <boxGeometry args={[RACK.w + 0.25, RACK.h + 0.25, RACK.d + 0.25]} />
          <meshBasicMaterial color="#fbbf24" wireframe transparent opacity={0.8} />
        </mesh>
      )}
      {/* Rack number label */}
      <Billboard position={[0, RACK.h + 0.7, 0]}>
        <Text fontSize={0.6} color={active ? '#fbbf24' : color} anchorX="center" outlineWidth={0.03} outlineColor="#000">
          {rack.label}
        </Text>
        <Text position={[0, -0.55, 0]} fontSize={0.42} color="#94a3b8" anchorX="center" outlineWidth={0.02} outlineColor="#000">
          {rack.devices.length} dev
        </Text>
      </Billboard>
    </group>
  )
}

// ── One building (shell + rooms + racks), offset along X ────────────────────────
function BuildingGroup({
  building, index, offsetX, active, onRackEnter, onRackLeave, onRackClick,
}: {
  building: FacilityBuilding
  index: number
  offsetX: number
  active: ActiveRack | null
  onRackEnter: (r: ActiveRack) => void
  onRackLeave: () => void
  onRackClick: (r: ActiveRack) => void
}) {
  const tint = tintFor(index)
  const rooms = useMemo(() => layoutRooms(building.rooms), [building.rooms])

  return (
    <group position={[offsetX, 0, 0]}>
      <BuildingShell name={building.name} subtitle={building.subtitle} tint={tint} />
      {rooms.map((rect, ri) => {
        const room = building.rooms[ri]
        const slots = slotsInRoom(rect, room.racks.length)
        return (
          <group key={rect.name}>
            <RoomCell rect={rect} tint={tint} />
            {room.racks.map((rack, idx) => {
              const isActive =
                !!active && active.buildingId === building.id && active.room === room.name &&
                active.rack.label === rack.label
              const descriptor: ActiveRack = {
                buildingId: building.id, buildingName: building.name, room: room.name, rack,
              }
              return (
                <RackCabinet
                  key={rack.label}
                  rack={rack}
                  pos={slots[idx] ?? [rect.x, rect.z]}
                  active={isActive}
                  onEnter={() => onRackEnter(descriptor)}
                  onLeave={onRackLeave}
                  onClick={() => onRackClick(descriptor)}
                />
              )
            })}
          </group>
        )
      })}
    </group>
  )
}

// ── Camera framing — fit all buildings ──────────────────────────────────────────
function FitCamera({ span }: { span: number }) {
  const { camera } = useThree()
  useEffect(() => {
    const dist = Math.max(span * 0.9, B.d + 30)
    camera.position.set(0, FLOOR_H + dist * 0.55, dist)
    camera.lookAt(0, 0, 0)
  }, [span, camera])
  return null
}

// ══════════════════════════════════════════════════════════════════════════════
// EXPORTED SCENE
// ══════════════════════════════════════════════════════════════════════════════

interface Props {
  buildings: FacilityBuilding[]
}

export default function FacilityBuildingsScene({ buildings }: Props) {
  const [hovered, setHovered] = useState<ActiveRack | null>(null)
  const [selected, setSelected] = useState<ActiveRack | null>(null)
  const active = hovered ?? selected

  // Place buildings left-to-right, centred on the origin.
  const step = B.w + GAP
  const totalSpan = Math.max(buildings.length, 1) * step
  const offsets = buildings.map((_, i) => -totalSpan / 2 + step / 2 + i * step)

  const totalDevices = buildings.reduce((s, b) => s + b.deviceCount, 0)
  const totalRacks = buildings.reduce((s, b) => s + b.rackCount, 0)

  const handleEnter = (r: ActiveRack) => setHovered(r)
  const handleLeave = () => setHovered(null)
  const handleClick = (r: ActiveRack) =>
    setSelected((p) => (p && p.buildingId === r.buildingId && p.room === r.room && p.rack.label === r.rack.label ? null : r))

  return (
    <WebGLErrorBoundary>
      <div className="relative w-full rounded-xl overflow-hidden border border-slate-600" style={{ height: 680 }}>
        <Canvas
          camera={{ position: [0, 40, 90], fov: 50, near: 0.5, far: 2000 }}
          gl={{ antialias: true, alpha: false, powerPreference: 'high-performance' }}
          dpr={[1, 1.5]}
          style={{ background: 'linear-gradient(180deg, #0a1628 0%, #0f2240 60%, #1a3a5f 100%)' }}
          onCreated={({ gl }) => { gl.setClearColor('#0a1628') }}
          onPointerMissed={() => setSelected(null)}
        >
          <FitCamera span={totalSpan} />
          <ambientLight intensity={1.4} color="#c8dff5" />
          <hemisphereLight args={['#b8d4f0', '#1e3a5f', 1.0]} />
          <directionalLight position={[20, 45, 25]} intensity={2.0} color="#ffffff" />
          <directionalLight position={[-20, 25, -25]} intensity={0.9} color="#9bc4e8" />

          {buildings.map((b, i) => (
            <BuildingGroup
              key={b.id}
              building={b}
              index={i}
              offsetX={offsets[i]}
              active={active}
              onRackEnter={handleEnter}
              onRackLeave={handleLeave}
              onRackClick={handleClick}
            />
          ))}

          <OrbitControls
            enableDamping
            dampingFactor={0.08}
            minDistance={8}
            maxDistance={Math.max(totalSpan * 1.6, 300)}
            maxPolarAngle={Math.PI / 1.9}
            target={[0, FLOOR_H / 2, 0]}
          />
        </Canvas>

        {/* Summary */}
        <div className="absolute top-3 left-3 bg-black/60 backdrop-blur-sm rounded-lg p-2.5 pointer-events-none">
          <span className="text-xs text-slate-300">
            {buildings.length} building{buildings.length !== 1 ? 's' : ''} · {totalRacks} racks · {totalDevices} devices
          </span>
        </div>

        {/* Building legend */}
        <div className="absolute top-3 right-3 bg-black/60 backdrop-blur-sm rounded-lg p-2.5 flex flex-col gap-1.5 max-w-[220px]">
          {buildings.map((b, i) => (
            <div key={b.id} className="flex items-center gap-2">
              <span className="w-3 h-3 rounded-sm flex-shrink-0" style={{ backgroundColor: tintFor(i) }} />
              <span className="text-xs text-slate-200 truncate" title={`${b.name} (${b.id})`}>{b.name}</span>
            </div>
          ))}
        </div>

        {/* Rack device list — shown on hover or click */}
        {active && (
          <div className="absolute bottom-3 left-3 bg-slate-900/95 border border-amber-500/40 rounded-xl p-4 min-w-[260px] max-w-[340px] max-h-[60%] overflow-y-auto shadow-xl">
            <div className="flex items-center justify-between mb-1">
              <span className="font-semibold text-amber-300 text-sm">{active.rack.label}</span>
              <span className="text-xs text-slate-400">{active.rack.devices.length} devices</span>
            </div>
            <p className="text-xs text-slate-400 mb-3">
              {active.buildingName} · {active.room}
            </p>
            <div className="space-y-1.5">
              {active.rack.devices.length === 0 && (
                <p className="text-xs text-slate-500">No devices in this rack.</p>
              )}
              {active.rack.devices.map((d) => (
                <div key={d.id} className="flex items-center gap-2 text-xs">
                  <span className="w-2 h-2 rounded-full flex-shrink-0" style={{ backgroundColor: STATUS_HEX[d.status] }} />
                  <span className="text-slate-200 truncate flex-1" title={d.name}>{d.name}</span>
                  {d.rackUnit != null && <span className="text-slate-500 font-mono">U{d.rackUnit}</span>}
                  {d.deviceType && <span className="text-slate-500 truncate max-w-[80px]">{d.deviceType}</span>}
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="absolute bottom-3 left-1/2 -translate-x-1/2 text-xs text-slate-500 pointer-events-none select-none">
          hover a rack for its devices · drag · scroll · click to pin
        </div>
      </div>
    </WebGLErrorBoundary>
  )
}
