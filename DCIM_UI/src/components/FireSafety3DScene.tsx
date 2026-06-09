/**
 * FireSafety3DScene — data-driven facility.
 *
 * The building is generated from real device telemetry: each environment metric
 * carries its device's `room` and `floor` in metadata, which the page collapses
 * into a {@link Facility} (floors → rooms → devices). This scene renders exactly
 * that many floors, partitions each floor into its reported rooms, and drops a
 * status-coloured marker for every device inside its room. Manual Fire & Safety
 * sensors are matched into the closest-named room and overlaid on top.
 */

import { useRef, useState, useMemo, useCallback, useEffect, Component, type ReactNode } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Text, Billboard } from '@react-three/drei'
import * as THREE from 'three'
import type { Facility, FacilityFloor, SceneDevice, DeviceStatus } from '@/lib/fireSafetyFacility'

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
            <button
              className="mt-4 px-4 py-2 bg-blue-600 text-white rounded-lg text-sm hover:bg-blue-500"
              onClick={() => this.setState({ error: null })}
            >
              Retry
            </button>
          </div>
        </div>
      )
    }
    return this.props.children
  }
}

// ── Public types ───────────────────────────────────────────────────────────────
export type SensorStatus = DeviceStatus
export type SensorType   = 'smoke' | 'heat' | 'water-leak' | 'co2' | 'vesda'

export interface SceneSensor {
  id: string; name: string; type: SensorType; zone: string; status: SensorStatus
  room?: string   // optional placement hint matched against facility room names
}

interface Props {
  facility: Facility
  sensors?: SceneSensor[]
  onSensorClick?: (id: string, status: SensorStatus) => void
  onDeviceClick?: (id: string) => void
}

// ── Constants ──────────────────────────────────────────────────────────────────
const FLOOR_H = 5.5                       // per-floor clear height (m)
const B       = { w: 64, d: 44 }          // building footprint
const INTERIOR = { w: B.w - 9, d: B.d - 9 }
const RACK    = { w: 0.7, d: 1.0, h: 2.1 }

const FLOOR_COLORS = ['#1d4ed8', '#15803d', '#7c3aed', '#c2410c', '#0e7490', '#b91c1c'] as const
const floorColor = (fi: number) => FLOOR_COLORS[fi % FLOOR_COLORS.length]

const STATUS_HEX: Record<DeviceStatus, string> = {
  normal:  '#00ff88',
  alarm:   '#ff2244',
  fault:   '#ffb800',
  offline: '#8899aa',
  testing: '#00aaff',
}

// Status precedence for a room's aggregate colour (worst wins).
const STATUS_RANK: Record<DeviceStatus, number> = { alarm: 4, fault: 3, testing: 2, offline: 1, normal: 0 }

// ── Layout helpers ───────────────────────────────────────────────────────────
export interface RoomRect { name: string; x: number; z: number; w: number; d: number }

/** Partition the building interior into a grid of room cells. */
function layoutRooms(rooms: string[]): RoomRect[] {
  const n = Math.max(rooms.length, 1)
  const cols = Math.ceil(Math.sqrt(n))
  const rows = Math.ceil(n / cols)
  const gap = 1.4
  const cellW = (INTERIOR.w - gap * (cols - 1)) / cols
  const cellD = (INTERIOR.d - gap * (rows - 1)) / rows
  const startX = -INTERIOR.w / 2 + cellW / 2
  const startZ = -INTERIOR.d / 2 + cellD / 2
  return rooms.map((name, i) => {
    const c = i % cols
    const r = Math.floor(i / cols)
    return {
      name,
      x: startX + c * (cellW + gap),
      z: startZ + r * (cellD + gap),
      w: cellW,
      d: cellD,
    }
  })
}

/** Even grid of XZ slots inside a room rect (padded from the walls). */
function slotsInRoom(room: RoomRect, count: number, pad = 0.9): [number, number][] {
  const n = Math.max(count, 1)
  const cols = Math.ceil(Math.sqrt(n))
  const rows = Math.ceil(n / cols)
  const iw = room.w - pad * 2
  const id = room.d - pad * 2
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

function norm(s: string) { return s.toLowerCase().replace(/[^a-z0-9]+/g, ' ').trim() }

/** Best-effort match of a manual sensor's room hint to a real room name. */
function matchRoom(hint: string | undefined, rooms: RoomRect[]): RoomRect | undefined {
  if (!hint || !rooms.length) return undefined
  const h = norm(hint)
  if (!h) return undefined
  let best: RoomRect | undefined
  for (const r of rooms) {
    const rn = norm(r.name)
    if (rn === h) return r
    if (!best && (rn.includes(h) || h.includes(rn))) best = r
    if (!best) {
      const shared = h.split(' ').some((w) => w.length > 2 && rn.includes(w))
      if (shared) best = r
    }
  }
  return best
}

// ── Floor slab ─────────────────────────────────────────────────────────────────
function FloorSlab({ y, fi }: { y: number; fi: number }) {
  const tex = useMemo(() => {
    const c = document.createElement('canvas')
    c.width = c.height = 512
    const ctx = c.getContext('2d')!
    const T = 64
    const a = fi % 2 === 0 ? '#1e3a5f' : '#1a2e4a'
    const b = fi % 2 === 0 ? '#172d4a' : '#162840'
    for (let r = 0; r < 8; r++)
      for (let col = 0; col < 8; col++) {
        ctx.fillStyle = (r + col) % 2 === 0 ? a : b
        ctx.fillRect(col * T, r * T, T, T)
      }
    ctx.strokeStyle = '#2d5a8e'; ctx.lineWidth = 1.5
    for (let i = 0; i <= 8; i++) {
      ctx.beginPath(); ctx.moveTo(i * T, 0);   ctx.lineTo(i * T, 512); ctx.stroke()
      ctx.beginPath(); ctx.moveTo(0, i * T);   ctx.lineTo(512, i * T); ctx.stroke()
    }
    const t = new THREE.CanvasTexture(c)
    t.wrapS = t.wrapT = THREE.RepeatWrapping
    t.repeat.set(11, 8)
    return t
  }, [fi])

  return (
    <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, y, 0]}>
      <planeGeometry args={[B.w + 4, B.d + 4]} />
      <meshStandardMaterial map={tex} roughness={0.6} metalness={0.15} />
    </mesh>
  )
}

// ── Multi-floor building shell ─────────────────────────────────────────────────
function MultiFloorShell({ floors }: { floors: FacilityFloor[] }) {
  const count = floors.length
  const totalH = count * FLOOR_H
  return (
    <group>
      {Array.from({ length: count }, (_, f) => (
        <FloorSlab key={f} y={f * FLOOR_H} fi={f} />
      ))}

      {/* Roof */}
      <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, totalH, 0]}>
        <planeGeometry args={[B.w, B.d]} />
        <meshStandardMaterial color="#0d1e30" roughness={0.9} side={THREE.DoubleSide} />
      </mesh>

      {/* Exterior walls — full height */}
      {([
        { pos: [0, totalH / 2, -B.d / 2] as [number,number,number], sz: [B.w, totalH, 0.4] as [number,number,number] },
        { pos: [0, totalH / 2,  B.d / 2] as [number,number,number], sz: [B.w, totalH, 0.4] as [number,number,number] },
        { pos: [-B.w / 2, totalH / 2, 0] as [number,number,number], sz: [0.4, totalH, B.d] as [number,number,number] },
        { pos: [ B.w / 2, totalH / 2, 0] as [number,number,number], sz: [0.4, totalH, B.d] as [number,number,number] },
      ]).map(({ pos, sz }, i) => (
        <mesh key={i} position={pos}>
          <boxGeometry args={sz} />
          <meshStandardMaterial color="#1e3a5f" roughness={0.7} metalness={0.2} transparent opacity={0.5} side={THREE.DoubleSide} />
        </mesh>
      ))}

      {/* Floor-level fascia bands */}
      {Array.from({ length: count }, (_, f) => (
        <group key={`fascia-${f}`}>
          {([-B.w / 2, B.w / 2] as number[]).map((x) => (
            <mesh key={x} position={[x, f * FLOOR_H + 0.2, 0]}>
              <boxGeometry args={[0.14, 0.4, B.d + 0.6]} />
              <meshStandardMaterial color="#4a7db5" metalness={0.7} roughness={0.2} />
            </mesh>
          ))}
          {([-B.d / 2, B.d / 2] as number[]).map((z) => (
            <mesh key={z} position={[0, f * FLOOR_H + 0.2, z]}>
              <boxGeometry args={[B.w + 0.6, 0.4, 0.14]} />
              <meshStandardMaterial color="#4a7db5" metalness={0.7} roughness={0.2} />
            </mesh>
          ))}
        </group>
      ))}

      {/* Steel columns — full height */}
      {([-28, -14, 0, 14, 28] as number[]).flatMap((x) =>
        [-18, 0, 18].map((z) => (
          <mesh key={`col-${x}-${z}`} position={[x, totalH / 2, z]}>
            <cylinderGeometry args={[0.3, 0.3, totalH, 8]} />
            <meshStandardMaterial color="#4a7db5" metalness={0.8} roughness={0.2} />
          </mesh>
        ))
      )}

      {/* Floor name labels in 3D */}
      {floors.map((fl, f) => (
        <Billboard key={`lbl-${f}`} position={[-B.w / 2 + 1.8, f * FLOOR_H + FLOOR_H / 2, -B.d / 2 + 0.8]}>
          <Text fontSize={1.0} color="#60a5fa" anchorX="left" outlineWidth={0.04} outlineColor="#000">
            {fl.label}
          </Text>
        </Billboard>
      ))}
    </group>
  )
}

// ── Stairwell ─────────────────────────────────────────────────────────────────
function Stairwell({ floorCount }: { floorCount: number }) {
  const SX = B.w / 2 - 4.5
  const SZ = B.d / 2 - 4.5
  const STEPS = 12
  const stepH = FLOOR_H / STEPS
  const stepD = 0.36
  const totalH = floorCount * FLOOR_H

  return (
    <group position={[SX, 0, SZ]}>
      <mesh position={[0, totalH / 2, -2.4]}>
        <boxGeometry args={[5, totalH, 0.2]} />
        <meshStandardMaterial color="#243b55" roughness={0.7} />
      </mesh>
      <mesh position={[-2.4, totalH / 2, 0]}>
        <boxGeometry args={[0.2, totalH, 5]} />
        <meshStandardMaterial color="#243b55" roughness={0.7} />
      </mesh>
      {Array.from({ length: floorCount }, (_, f) =>
        Array.from({ length: STEPS }, (__, s) => (
          <mesh key={`${f}-${s}`} position={[-0.5, f * FLOOR_H + s * stepH + stepH / 2, -2.0 + s * stepD]}>
            <boxGeometry args={[1.4, 0.07, stepD]} />
            <meshStandardMaterial color="#334155" roughness={0.6} metalness={0.35} />
          </mesh>
        ))
      )}
    </group>
  )
}

// ── Room partitions (data-driven) ──────────────────────────────────────────────
function RoomPartitions({
  fi, rooms, statusByRoom, onRoomClick,
}: {
  fi: number; rooms: RoomRect[]
  statusByRoom: Map<string, DeviceStatus>
  onRoomClick?: (name: string) => void
}) {
  const baseY = fi * FLOOR_H
  const WALL_H = 1.5
  return (
    <group>
      {rooms.map((r) => {
        const st = statusByRoom.get(r.name) ?? 'offline'
        const tint = STATUS_HEX[st]
        const alarm = st === 'alarm'
        return (
          <group key={r.name}>
            {/* Floor tint */}
            <mesh
              rotation={[-Math.PI / 2, 0, 0]}
              position={[r.x, baseY + 0.04, r.z]}
              onClick={onRoomClick ? () => onRoomClick(r.name) : undefined}
            >
              <planeGeometry args={[r.w, r.d]} />
              <meshBasicMaterial color={tint} transparent opacity={alarm ? 0.3 : 0.14} side={THREE.DoubleSide} depthWrite={false} />
            </mesh>
            {/* Low partition walls (4 edges) */}
            {([
              { pos: [r.x, baseY + WALL_H / 2, r.z - r.d / 2] as [number,number,number], sz: [r.w, WALL_H, 0.12] as [number,number,number] },
              { pos: [r.x, baseY + WALL_H / 2, r.z + r.d / 2] as [number,number,number], sz: [r.w, WALL_H, 0.12] as [number,number,number] },
              { pos: [r.x - r.w / 2, baseY + WALL_H / 2, r.z] as [number,number,number], sz: [0.12, WALL_H, r.d] as [number,number,number] },
              { pos: [r.x + r.w / 2, baseY + WALL_H / 2, r.z] as [number,number,number], sz: [0.12, WALL_H, r.d] as [number,number,number] },
            ]).map((w, i) => (
              <mesh key={i} position={w.pos}>
                <boxGeometry args={w.sz} />
                <meshStandardMaterial color={floorColor(fi)} transparent opacity={0.32} roughness={0.5} metalness={0.3} />
              </mesh>
            ))}
            {/* Room label */}
            <Billboard position={[r.x, baseY + WALL_H + 0.6, r.z]}>
              <Text fontSize={0.6} color={alarm ? '#ff6666' : '#cfe3ff'} anchorX="center" maxWidth={r.w} textAlign="center" outlineWidth={0.02} outlineColor="#000">
                {r.name}
              </Text>
            </Billboard>
          </group>
        )
      })}
    </group>
  )
}

// ── Racks inside rooms (one InstancedMesh per floor) ────────────────────────────
function FloorRoomRacks({ fi, rooms }: { fi: number; rooms: RoomRect[] }) {
  const baseY = fi * FLOOR_H
  const ref = useRef<THREE.InstancedMesh>(null!)

  const positions = useMemo(() => {
    const pts: [number, number][] = []
    for (const r of rooms) {
      const cols = Math.max(1, Math.min(8, Math.floor(r.w / 1.4)))
      const rows = Math.max(1, Math.min(4, Math.floor(r.d / 2.4)))
      const iw = r.w - 1.6, id = r.d - 1.6
      for (let row = 0; row < rows; row++)
        for (let c = 0; c < cols; c++) {
          const x = r.x - iw / 2 + (cols === 1 ? iw / 2 : (c / (cols - 1)) * iw)
          const z = r.z - id / 2 + (rows === 1 ? id / 2 : (row / (rows - 1)) * id)
          pts.push([x, z])
        }
    }
    return pts
  }, [rooms])

  useEffect(() => {
    const mesh = ref.current
    if (!mesh || positions.length === 0) return
    const d = new THREE.Object3D()
    positions.forEach(([x, z], i) => {
      d.position.set(x, baseY + RACK.h / 2, z)
      d.updateMatrix()
      mesh.setMatrixAt(i, d.matrix)
    })
    mesh.instanceMatrix.needsUpdate = true
  }, [positions, baseY])

  if (positions.length === 0) return null
  return (
    <instancedMesh ref={ref} args={[undefined, undefined, positions.length]}>
      <boxGeometry args={[RACK.w, RACK.h, RACK.d]} />
      <meshStandardMaterial color={floorColor(fi)} roughness={0.4} metalness={0.5} />
    </instancedMesh>
  )
}

// ── VESDA pipes per floor ──────────────────────────────────────────────────────
const VESDA_ROWS  = [-15, -8.5, -2, 4.5, 11]
const VESDA_DET_X = [-26, -18, -9, 0, 9, 18, 26]
const VESDA_SPHERES = VESDA_ROWS.length * VESDA_DET_X.length

function FloorVESDA({ fi }: { fi: number }) {
  const ceilY   = fi * FLOOR_H + FLOOR_H - 0.45
  const pipeRef = useRef<THREE.InstancedMesh>(null!)
  const dotRef  = useRef<THREE.InstancedMesh>(null!)

  useEffect(() => {
    const d = new THREE.Object3D()
    VESDA_ROWS.forEach((z, i) => {
      d.position.set(0, ceilY, z); d.rotation.set(0, 0, Math.PI / 2)
      d.updateMatrix(); pipeRef.current.setMatrixAt(i, d.matrix)
    })
    pipeRef.current.instanceMatrix.needsUpdate = true

    let idx = 0
    for (const z of VESDA_ROWS) for (const x of VESDA_DET_X) {
      d.position.set(x, ceilY - 0.1, z); d.rotation.set(0, 0, 0)
      d.updateMatrix(); dotRef.current.setMatrixAt(idx++, d.matrix)
    }
    dotRef.current.instanceMatrix.needsUpdate = true
  }, [ceilY])

  return (
    <group>
      <instancedMesh ref={pipeRef} args={[undefined, undefined, VESDA_ROWS.length]}>
        <cylinderGeometry args={[0.065, 0.065, B.w - 5, 8]} />
        <meshStandardMaterial color="#dde8f0" metalness={0.75} roughness={0.15} />
      </instancedMesh>
      <instancedMesh ref={dotRef} args={[undefined, undefined, VESDA_SPHERES]}>
        <sphereGeometry args={[0.07, 6, 6]} />
        <meshBasicMaterial color="#ff3333" />
      </instancedMesh>
    </group>
  )
}

// ── FM-200 nozzles per floor ───────────────────────────────────────────────────
const FM200_PTS: [number, number][] = []
for (const x of [-24, -16, -8, 0, 8, 16, 24])
  for (const z of [-16, -9, -2, 5, 12])
    FM200_PTS.push([x, z])

function FloorFM200({ fi }: { fi: number }) {
  const cy      = fi * FLOOR_H + FLOOR_H - 0.18
  const coneRef = useRef<THREE.InstancedMesh>(null!)
  const stemRef = useRef<THREE.InstancedMesh>(null!)

  useEffect(() => {
    const d = new THREE.Object3D()
    FM200_PTS.forEach(([x, z], i) => {
      d.position.set(x, cy, z);        d.updateMatrix(); coneRef.current.setMatrixAt(i, d.matrix)
      d.position.set(x, cy - 0.22, z); d.updateMatrix(); stemRef.current.setMatrixAt(i, d.matrix)
    })
    coneRef.current.instanceMatrix.needsUpdate = true
    stemRef.current.instanceMatrix.needsUpdate = true
  }, [cy])

  return (
    <group>
      <instancedMesh ref={coneRef} args={[undefined, undefined, FM200_PTS.length]}>
        <cylinderGeometry args={[0.1, 0.16, 0.24, 8]} />
        <meshStandardMaterial color="#f0f4f8" metalness={0.85} roughness={0.1} />
      </instancedMesh>
      <instancedMesh ref={stemRef} args={[undefined, undefined, FM200_PTS.length]}>
        <cylinderGeometry args={[0.025, 0.025, 0.2, 6]} />
        <meshStandardMaterial color="#94a3b8" metalness={0.9} roughness={0.1} />
      </instancedMesh>
    </group>
  )
}

// ── Emergency lighting + exit signs per floor ───────────────────────────────────
function FloorEmergencyLighting({ fi }: { fi: number }) {
  const cy = fi * FLOOR_H + FLOOR_H - 0.5
  const positions: [number, number, number][] = [
    [-B.w / 2 + 0.4, cy, -9], [-B.w / 2 + 0.4, cy,  5],
    [ B.w / 2 - 0.4, cy, -9], [ B.w / 2 - 0.4, cy,  5],
    [0, cy, -B.d / 2 + 0.4],  [0, cy,  B.d / 2 - 0.4],
  ]
  return (
    <group>
      {positions.map((p, i) => (
        <mesh key={i} position={p}>
          <boxGeometry args={[0.18, 0.12, 0.1]} />
          <meshStandardMaterial color="#ffe066" emissive="#ffe066" emissiveIntensity={2.5} />
        </mesh>
      ))}
    </group>
  )
}

function FloorExitSigns({ fi }: { fi: number }) {
  const h = fi * FLOOR_H + FLOOR_H - 1.2
  return (
    <group>
      {([
        { pos: [-10, h, -B.d / 2 + 0.2] as [number,number,number], rot: [0, 0, 0] as [number,number,number] },
        { pos: [ 10, h, -B.d / 2 + 0.2] as [number,number,number], rot: [0, 0, 0] as [number,number,number] },
        { pos: [-B.w / 2 + 0.2, h, -7]  as [number,number,number], rot: [0,  Math.PI / 2, 0] as [number,number,number] },
        { pos: [ B.w / 2 - 0.2, h, -7]  as [number,number,number], rot: [0, -Math.PI / 2, 0] as [number,number,number] },
      ]).map(({ pos, rot }, i) => (
        <group key={i} position={pos} rotation={rot}>
          <mesh><boxGeometry args={[1.1, 0.42, 0.06]} /><meshBasicMaterial color="#00cc44" /></mesh>
          <mesh position={[0, 0, 0.04]}><boxGeometry args={[1.06, 0.38, 0.01]} /><meshBasicMaterial color="#00ff55" /></mesh>
        </group>
      ))}
    </group>
  )
}

// ── Exit gates ──────────────────────────────────────────────────────────────────
function ExitGate({ position, rotY = 0 }: { position: [number, number, number]; rotY?: number }) {
  const DW = 1.85, DH = 2.5, FT = 0.16, PW = DW / 2 - 0.03
  const SWING = Math.PI * 0.42
  return (
    <group position={position} rotation={[0, rotY, 0]}>
      <mesh position={[0, DH + FT / 2, 0]}><boxGeometry args={[DW + FT * 2, FT, FT]} /><meshStandardMaterial color="#2a4a6b" metalness={0.6} roughness={0.3} /></mesh>
      <mesh position={[-(DW / 2 + FT / 2), DH / 2, 0]}><boxGeometry args={[FT, DH, FT]} /><meshStandardMaterial color="#2a4a6b" metalness={0.6} roughness={0.3} /></mesh>
      <mesh position={[DW / 2 + FT / 2, DH / 2, 0]}><boxGeometry args={[FT, DH, FT]} /><meshStandardMaterial color="#2a4a6b" metalness={0.6} roughness={0.3} /></mesh>
      <group position={[-DW / 2, DH / 2, 0]} rotation={[0, SWING, 0]}>
        <mesh position={[PW / 2, 0, 0]}><boxGeometry args={[PW, DH, 0.05]} /><meshStandardMaterial color="#4a7db5" metalness={0.55} roughness={0.3} transparent opacity={0.88} /></mesh>
      </group>
      <group position={[DW / 2, DH / 2, 0]} rotation={[0, -SWING, 0]}>
        <mesh position={[-PW / 2, 0, 0]}><boxGeometry args={[PW, DH, 0.05]} /><meshStandardMaterial color="#4a7db5" metalness={0.55} roughness={0.3} transparent opacity={0.88} /></mesh>
      </group>
      <mesh position={[0, DH + FT + 0.12, 0]}><boxGeometry args={[0.45, 0.16, 0.08]} /><meshStandardMaterial color="#00ff55" emissive="#00ff55" emissiveIntensity={2.0} /></mesh>
    </group>
  )
}

function FloorExitGates({ fi }: { fi: number }) {
  const fy = fi * FLOOR_H
  return (
    <group>
      <ExitGate position={[-10, fy, -B.d / 2 + 0.15]} rotY={0}            />
      <ExitGate position={[ 10, fy, -B.d / 2 + 0.15]} rotY={0}            />
      <ExitGate position={[-B.w / 2 + 0.15, fy, -7]}  rotY={Math.PI / 2}  />
      <ExitGate position={[ B.w / 2 - 0.15, fy, -7]}  rotY={-Math.PI / 2} />
    </group>
  )
}

// ── CRAC / cooling units ─────────────────────────────────────────────────────────
function CRACUnit({ position, rotY = 0 }: { position: [number, number, number]; rotY?: number }) {
  return (
    <group position={position} rotation={[0, rotY, 0]}>
      <mesh><boxGeometry args={[1.2, 2.0, 0.75]} /><meshStandardMaterial color="#1a3350" metalness={0.65} roughness={0.25} /></mesh>
      <mesh position={[0, 0, 0.40]}><boxGeometry args={[1.0, 1.7, 0.05]} /><meshStandardMaterial color="#2d5a8e" emissive="#00aacc" emissiveIntensity={0.6} metalness={0.85} roughness={0.12} /></mesh>
    </group>
  )
}

function FloorCRACUnits({ fi }: { fi: number }) {
  const fy = fi * FLOOR_H + 1.0
  const lx = -B.w / 2 + 0.55
  const rx =  B.w / 2 - 0.55
  return (
    <group>
      {[-13, -4, 5, 14].map((z) => (
        <group key={z}>
          <CRACUnit position={[lx, fy, z]} rotY={-Math.PI / 2} />
          <CRACUnit position={[rx, fy, z]} rotY={ Math.PI / 2} />
        </group>
      ))}
    </group>
  )
}

// ── Device marker (one per real device) ──────────────────────────────────────────
function DeviceMarker({ device, position, selected, onClick }: {
  device: SceneDevice; position: [number, number, number]; selected: boolean; onClick: () => void
}) {
  const glowRef = useRef<THREE.Mesh>(null!)
  const isAlarm = device.status === 'alarm' || device.status === 'fault'
  const color   = STATUS_HEX[device.status]
  const threeCol = useMemo(() => new THREE.Color(color), [color])

  useFrame(() => {
    if (!glowRef.current) return
    const t = performance.now() / 1000
    const pulse = isAlarm ? 0.6 + 0.4 * Math.abs(Math.sin(t * 5)) : 0.3 + 0.12 * Math.abs(Math.sin(t * 1.5))
    ;(glowRef.current.material as THREE.MeshBasicMaterial).opacity = pulse
    if (isAlarm) glowRef.current.scale.setScalar(1 + 0.45 * Math.abs(Math.sin(t * 4)))
  })

  const valLabel = device.value != null ? `${device.value.toFixed(1)}${device.unit ?? ''}` : '—'
  return (
    <group position={position}>
      {/* ceiling stem */}
      <mesh position={[0, 0.28, 0]}><cylinderGeometry args={[0.02, 0.02, 0.55, 6]} /><meshStandardMaterial color="#64748b" metalness={0.7} roughness={0.3} /></mesh>
      <mesh position={[0, -0.04, 0]} onClick={onClick}>
        <cylinderGeometry args={[0.17, 0.17, 0.1, 16]} />
        <meshStandardMaterial color="#2a3f55" roughness={0.3} metalness={0.7} />
      </mesh>
      <mesh ref={glowRef} position={[0, -0.16, 0]}>
        <sphereGeometry args={[0.21, 16, 16]} />
        <meshBasicMaterial color={threeCol} transparent opacity={0.5} />
      </mesh>
      <mesh position={[0, -0.16, 0]} onClick={onClick}>
        <sphereGeometry args={[0.1, 12, 12]} />
        <meshBasicMaterial color={threeCol} />
      </mesh>
      {selected && (
        <mesh position={[0, -0.16, 0]}>
          <ringGeometry args={[0.3, 0.36, 24]} />
          <meshBasicMaterial color="#ffffff" side={THREE.DoubleSide} />
        </mesh>
      )}
      <Billboard position={[0, -0.7, 0]}>
        <Text fontSize={0.26} color={color} anchorX="center" outlineWidth={0.022} outlineColor="#000">
          {device.name.length > 16 ? device.name.slice(-15) : device.name}
        </Text>
        <Text position={[0, -0.3, 0]} fontSize={0.22} color="#cbd5e1" anchorX="center" outlineWidth={0.018} outlineColor="#000">
          {valLabel}
        </Text>
      </Billboard>
    </group>
  )
}

// ── Manual sensor marker ──────────────────────────────────────────────────────
function SensorMarker({ sensor, position, onClick }: {
  sensor: SceneSensor; position: [number,number,number]; onClick: () => void
}) {
  const glowRef = useRef<THREE.Mesh>(null!)
  const isAlarm = sensor.status === 'alarm' || sensor.status === 'fault'
  const isCeil  = sensor.type !== 'water-leak'
  const color   = STATUS_HEX[sensor.status]
  const threeCol = useMemo(() => new THREE.Color(color), [color])

  useFrame(() => {
    if (!glowRef.current) return
    const t = performance.now() / 1000
    const pulse = isAlarm ? 0.6 + 0.4 * Math.abs(Math.sin(t * 5)) : 0.35 + 0.15 * Math.abs(Math.sin(t * 1.5))
    ;(glowRef.current.material as THREE.MeshBasicMaterial).opacity = pulse
    if (isAlarm) glowRef.current.scale.setScalar(1 + 0.5 * Math.abs(Math.sin(t * 4)))
  })

  const yBody = isCeil ? -0.16 : 0.16
  const yGlow = isCeil ? -0.26 : 0.26
  return (
    <group position={position}>
      <mesh position={[0, yBody, 0]} onClick={onClick}>
        <boxGeometry args={[0.28, 0.1, 0.28]} />
        <meshStandardMaterial color="#3a2f55" roughness={0.3} metalness={0.6} />
      </mesh>
      <mesh ref={glowRef} position={[0, yGlow, 0]}>
        <sphereGeometry args={[0.2, 16, 16]} />
        <meshBasicMaterial color={threeCol} transparent opacity={0.5} />
      </mesh>
      <mesh position={[0, yGlow, 0]} onClick={onClick}>
        <sphereGeometry args={[0.09, 12, 12]} />
        <meshBasicMaterial color={threeCol} />
      </mesh>
      <Billboard position={[0, isCeil ? -0.72 : 0.72, 0]}>
        <Text fontSize={0.25} color={color} anchorX="center" outlineWidth={0.022} outlineColor="#000">
          {sensor.name.length > 14 ? sensor.name.slice(-12) : sensor.name}
        </Text>
      </Billboard>
    </group>
  )
}

// ── Alarm beacon ──────────────────────────────────────────────────────────────
function AlarmBeacon({ position }: { position: [number,number,number] }) {
  const lightRef = useRef<THREE.PointLight>(null!)
  const meshRef  = useRef<THREE.Mesh>(null!)
  useFrame(() => {
    const t = performance.now() / 1000
    if (lightRef.current) lightRef.current.intensity = 4 + 4 * Math.abs(Math.sin(t * 4))
    if (meshRef.current)  meshRef.current.rotation.y += 0.06
  })
  return (
    <group position={position}>
      <pointLight ref={lightRef} color="#ff2244" intensity={6} distance={18} />
      <mesh ref={meshRef}><sphereGeometry args={[0.26, 16, 16]} /><meshBasicMaterial color="#ff2244" /></mesh>
    </group>
  )
}

// ── Lighting ──────────────────────────────────────────────────────────────────
function SceneLights({ floorCount, hasAlarm }: { floorCount: number; hasAlarm: boolean }) {
  return (
    <>
      <ambientLight intensity={1.5} color="#c8dff5" />
      <hemisphereLight args={['#b8d4f0', '#1e3a5f', 1.1]} />
      <directionalLight position={[15, 35, 20]} intensity={2.2} color="#ffffff" />
      <directionalLight position={[-15, 20, -20]} intensity={1.0} color="#9bc4e8" />
      {Array.from({ length: floorCount }, (_, f) => (
        <pointLight key={`fl-${f}`} position={[0, f * FLOOR_H + FLOOR_H - 0.7, 0]} intensity={8} distance={60} color="#d0e8ff" />
      ))}
      {hasAlarm && <ambientLight intensity={0.5} color="#6b1a1a" />}
    </>
  )
}

// ── Camera controller ─────────────────────────────────────────────────────────
function CameraController({ preset, floorCount }: { preset: string; floorCount: number }) {
  const { camera } = useThree()
  useEffect(() => {
    const totalH = floorCount * FLOOR_H
    const map: Record<string, [number,number,number]> = {
      overview: [0, totalH + 28, B.d + 30],
      topdown:  [0, totalH + 55, 3],
      front:    [0, totalH / 2, B.d + 36],
    }
    for (let f = 0; f < floorCount; f++) map[`floor-${f}`] = [0, f * FLOOR_H + 14, B.d + 6]
    const p = map[preset] ?? map.overview
    camera.position.set(...p)
  }, [preset, camera, floorCount])
  return null
}

// ══════════════════════════════════════════════════════════════════════════════
// EXPORTED SCENE
// ══════════════════════════════════════════════════════════════════════════════

export default function FireSafety3DScene({ facility, sensors = [], onSensorClick, onDeviceClick }: Props) {
  const { floors, devices } = facility
  const floorCount = Math.max(floors.length, 1)
  const totalH = floorCount * FLOOR_H

  const [showDevices, setShowDevices] = useState(true)
  const [showSensors, setShowSensors] = useState(true)
  const [showRooms,   setShowRooms]   = useState(true)
  const [showRacks,   setShowRacks]   = useState(true)
  const [showVESDA,   setShowVESDA]   = useState(true)
  const [showFM200,   setShowFM200]   = useState(true)
  const [showAC,      setShowAC]      = useState(false)
  const [showExits,   setShowExits]   = useState(true)
  const [preset,      setPreset]      = useState('overview')
  const [activeFloor, setActiveFloor] = useState<number | null>(null)
  const [selDevice,   setSelDevice]   = useState<SceneDevice | null>(null)

  // Per-floor room rectangles (memoised by room-name signature).
  const roomLayouts = useMemo(
    () => floors.map((fl) => layoutRooms(fl.rooms)),
    [floors],
  )

  // floor label → index for placing devices.
  const floorIndex = useMemo(() => {
    const m = new Map<string, number>()
    floors.forEach((fl, i) => m.set(fl.label, i))
    return m
  }, [floors])

  // Devices placed into their room slot. Devices for an unknown room/floor are skipped.
  const placedDevices = useMemo(() => {
    type Placed = { device: SceneDevice; pos: [number, number, number] }
    const out: Placed[] = []
    // group devices by floor+room to lay them out in a grid per room
    const byFloorRoom = new Map<string, SceneDevice[]>()
    for (const d of devices) {
      const k = `${d.floor} ${d.room}`
      if (!byFloorRoom.has(k)) byFloorRoom.set(k, [])
      byFloorRoom.get(k)!.push(d)
    }
    for (const [k, list] of byFloorRoom) {
      const [fl, room] = k.split(' ')
      const fi = floorIndex.get(fl)
      if (fi == null) continue
      const rect = roomLayouts[fi]?.find((r) => r.name === room)
      if (!rect) continue
      const slots = slotsInRoom(rect, list.length)
      const ceilY = fi * FLOOR_H + FLOOR_H - 0.6
      list.forEach((d, i) => {
        const [x, z] = slots[i] ?? [rect.x, rect.z]
        out.push({ device: d, pos: [x, ceilY, z] })
      })
    }
    return out
  }, [devices, roomLayouts, floorIndex])

  // Aggregate worst status per floor+room for the partition tint.
  const statusByFloorRoom = useMemo(() => {
    const m = floors.map(() => new Map<string, DeviceStatus>())
    for (const d of devices) {
      const fi = floorIndex.get(d.floor)
      if (fi == null) continue
      const cur = m[fi].get(d.room)
      if (!cur || STATUS_RANK[d.status] > STATUS_RANK[cur]) m[fi].set(d.room, d.status)
    }
    return m
  }, [devices, floors, floorIndex])

  // Manual sensors matched into a room (search every floor, prefer ground).
  const placedSensors = useMemo(() => {
    type PS = { sensor: SceneSensor; pos: [number, number, number] }
    const out: PS[] = []
    sensors.forEach((s, idx) => {
      let fi = 0, rect: RoomRect | undefined
      for (let f = 0; f < roomLayouts.length; f++) {
        const r = matchRoom(s.room ?? s.zone, roomLayouts[f])
        if (r) { fi = f; rect = r; break }
      }
      if (!rect) { rect = roomLayouts[0]?.[idx % Math.max(roomLayouts[0]?.length ?? 1, 1)]; fi = 0 }
      if (!rect) return
      const isCeil = s.type !== 'water-leak'
      const y = fi * FLOOR_H + (isCeil ? FLOOR_H - 0.4 : 0.1)
      // place toward a room corner so it doesn't collide with device grid
      out.push({ sensor: s, pos: [rect.x - rect.w / 2 + 0.8, y, rect.z - rect.d / 2 + 0.8] })
    })
    return out
  }, [sensors, roomLayouts])

  const hasAlarm = useMemo(
    () => devices.some((d) => d.status === 'alarm') || sensors.some((s) => s.status === 'alarm'),
    [devices, sensors],
  )

  const handleDevice = useCallback((d: SceneDevice) => {
    setSelDevice((p) => (p?.id === d.id ? null : d))
    onDeviceClick?.(d.id)
  }, [onDeviceClick])

  const floorVisible = (fi: number) => activeFloor === null || activeFloor === fi
  const orbitTarget: [number,number,number] = activeFloor !== null
    ? [0, activeFloor * FLOOR_H + FLOOR_H / 2, 0]
    : [0, totalH / 2, 0]

  type LayerToggle = [string, boolean, React.Dispatch<React.SetStateAction<boolean>>]
  const layerToggles: LayerToggle[] = [
    ['Devices',     showDevices, setShowDevices],
    ['Rooms',       showRooms,   setShowRooms],
    ['Racks',       showRacks,   setShowRacks],
    ['Sensors',     showSensors, setShowSensors],
    ['VESDA',       showVESDA,   setShowVESDA],
    ['FM-200',      showFM200,   setShowFM200],
    ['Cooling',     showAC,      setShowAC],
    ['Exit Gates',  showExits,   setShowExits],
  ]

  const floorOptions: [number | null, string][] = [
    [null, 'All'],
    ...floors.map((fl, i) => [i, fl.label] as [number, string]),
  ]

  const cameraPresets: [string, string][] = [
    ['overview', 'Overview'],
    ['topdown',  'Top-Down'],
    ['front',    'Front'],
  ]

  const deviceCount = devices.length
  const roomCount = floors.reduce((s, f) => s + f.rooms.length, 0)

  return (
    <WebGLErrorBoundary>
    <div className="relative w-full rounded-xl overflow-hidden border border-slate-600" style={{ height: 680 }}>
      <Canvas
        camera={{ position: [0, totalH + 28, B.d + 30], fov: 50, near: 0.5, far: 900 }}
        gl={{ antialias: true, alpha: false, powerPreference: 'high-performance' }}
        dpr={[1, 1.5]}
        style={{ background: 'linear-gradient(180deg, #0a1628 0%, #0f2240 60%, #1a3a5f 100%)' }}
        onCreated={({ gl }) => { gl.setClearColor('#0a1628') }}
      >
        <CameraController preset={preset} floorCount={floorCount} />
        <SceneLights floorCount={floorCount} hasAlarm={hasAlarm} />

        <MultiFloorShell floors={floors} />
        <Stairwell floorCount={floorCount} />

        {/* Per-floor data-driven content */}
        {floors.map((_, f) => (
          <group key={f} visible={floorVisible(f)}>
            {showRooms && (
              <RoomPartitions fi={f} rooms={roomLayouts[f]} statusByRoom={statusByFloorRoom[f]} />
            )}
            {showRacks && <FloorRoomRacks fi={f} rooms={roomLayouts[f]} />}
            {showVESDA && <FloorVESDA fi={f} />}
            {showFM200 && <FloorFM200 fi={f} />}
            <FloorEmergencyLighting fi={f} />
            <FloorExitSigns fi={f} />
            {showExits && <FloorExitGates fi={f} />}
            {showAC    && <FloorCRACUnits fi={f} />}
          </group>
        ))}

        {/* Devices */}
        {showDevices && placedDevices.map(({ device, pos }) => {
          const fi = floorIndex.get(device.floor) ?? 0
          if (!floorVisible(fi)) return null
          return (
            <DeviceMarker
              key={device.id}
              device={device}
              position={pos}
              selected={selDevice?.id === device.id}
              onClick={() => handleDevice(device)}
            />
          )
        })}

        {/* Manual sensors */}
        {showSensors && placedSensors.map(({ sensor, pos }) => {
          const fi = Math.floor(pos[1] / FLOOR_H)
          if (!floorVisible(fi)) return null
          return (
            <SensorMarker
              key={sensor.id}
              sensor={sensor}
              position={pos}
              onClick={() => onSensorClick?.(sensor.id, sensor.status)}
            />
          )
        })}

        {/* Alarm beacons over alarming devices */}
        {hasAlarm && placedDevices
          .filter(({ device }) => device.status === 'alarm')
          .map(({ device, pos }) => {
            const fi = floorIndex.get(device.floor) ?? 0
            if (!floorVisible(fi)) return null
            return <AlarmBeacon key={`beacon-${device.id}`} position={[pos[0], fi * FLOOR_H + FLOOR_H - 0.9, pos[2]]} />
          })}

        <OrbitControls
          enableDamping
          dampingFactor={0.08}
          minDistance={5}
          maxDistance={220}
          maxPolarAngle={Math.PI / 1.65}
          target={orbitTarget}
        />
      </Canvas>

      {/* Status legend */}
      <div className="absolute top-3 left-3 bg-black/60 backdrop-blur-sm rounded-lg p-2.5 flex flex-col gap-1.5 pointer-events-none">
        {(Object.entries(STATUS_HEX) as [DeviceStatus, string][]).map(([s, hex]) => (
          <div key={s} className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: hex, boxShadow: `0 0 6px ${hex}` }} />
            <span className="text-xs text-slate-200 capitalize">{s}</span>
          </div>
        ))}
        <div className="mt-1 pt-1 border-t border-slate-600">
          <span className="text-xs text-slate-400">{deviceCount} devices · {roomCount} rooms · {floorCount} floor{floorCount > 1 ? 's' : ''}</span>
        </div>
      </div>

      {/* Alarm banner */}
      {hasAlarm && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 flex items-center gap-2 bg-red-600 border border-red-400 rounded-lg px-4 py-2 shadow-lg shadow-red-900/60 animate-pulse z-10">
          <div className="w-2.5 h-2.5 rounded-full bg-white" />
          <span className="text-sm font-bold text-white tracking-wider">
            ⚠ ALARM — {devices.filter((d) => d.status === 'alarm').map((d) => d.name).slice(0, 4).join(' · ')}
          </span>
        </div>
      )}

      {/* Layer toggles */}
      <div className="absolute top-3 right-3 bg-black/60 backdrop-blur-sm rounded-lg p-2 flex flex-col gap-1.5">
        {layerToggles.map(([label, val, set]) => (
          <button
            key={label}
            onClick={() => set((v) => !v)}
            className={`text-xs px-3 py-1 rounded text-left transition-all ${
              val ? 'bg-blue-600/70 text-white border border-blue-400/50' : 'bg-white/5 text-slate-400 border border-white/10'
            }`}
          >
            {val ? '◉' : '○'}  {label}
          </button>
        ))}
      </div>

      {/* Floor selector */}
      <div className="absolute bottom-14 right-3 flex flex-col gap-1.5 items-end max-h-[55%] overflow-y-auto">
        <span className="text-xs text-slate-500 pr-1">Floor</span>
        {floorOptions.map(([fi, label]) => (
          <button
            key={String(fi)}
            onClick={() => {
              setActiveFloor(fi)
              setPreset(fi !== null ? `floor-${fi}` : 'overview')
            }}
            className={`text-xs px-3 py-1.5 rounded-lg border transition-all shadow-md min-w-16 text-center ${
              activeFloor === fi ? 'text-white shadow-lg' : 'bg-black/50 border-slate-600 text-slate-300 hover:border-slate-400'
            }`}
            style={activeFloor === fi && fi !== null ? { backgroundColor: floorColor(fi) + 'cc', borderColor: floorColor(fi) }
              : activeFloor === fi ? { backgroundColor: '#2563ebcc', borderColor: '#2563eb' } : {}}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Camera presets */}
      <div className="absolute bottom-3 right-3 flex gap-1.5 flex-wrap justify-end">
        {cameraPresets.map(([key, label]) => (
          <button
            key={key}
            onClick={() => setPreset(key)}
            className={`text-xs px-3 py-1.5 rounded-lg border transition-all shadow-md ${
              preset === key ? 'bg-blue-600 border-blue-400 text-white shadow-blue-900/50'
                : 'bg-black/50 border-slate-600 text-slate-300 hover:border-slate-400 hover:text-white'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Device info card */}
      {selDevice && (
        <div className="absolute bottom-3 left-3 bg-slate-900/95 border border-slate-500 rounded-xl p-4 min-w-[220px] shadow-xl">
          <div className="flex items-center justify-between mb-3">
            <span className="font-semibold text-white text-sm">{selDevice.name}</span>
            <button onClick={() => setSelDevice(null)} className="text-slate-400 hover:text-white ml-3">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="space-y-1.5 text-xs">
            {[['Floor', selDevice.floor], ['Room', selDevice.room],
              ['Reading', selDevice.value != null ? `${selDevice.value.toFixed(1)} ${selDevice.unit ?? ''}` : '—']].map(([k, v]) => (
              <div key={k} className="flex justify-between gap-4">
                <span className="text-slate-400">{k}</span>
                <span className="text-slate-200 text-right">{v}</span>
              </div>
            ))}
            <div className="flex justify-between">
              <span className="text-slate-400">Status</span>
              <span className="font-bold capitalize" style={{ color: STATUS_HEX[selDevice.status], textShadow: `0 0 8px ${STATUS_HEX[selDevice.status]}` }}>
                {selDevice.status}
              </span>
            </div>
            {selDevice.lastSeen && (
              <div className="flex justify-between">
                <span className="text-slate-400">Last seen</span>
                <span className="text-slate-400 text-right">{new Date(selDevice.lastSeen).toLocaleTimeString()}</span>
              </div>
            )}
          </div>
        </div>
      )}

      <div className="absolute bottom-3 left-1/2 -translate-x-1/2 text-xs text-slate-500 pointer-events-none select-none">
        drag · scroll · right-click to navigate
      </div>
    </div>
    </WebGLErrorBoundary>
  )
}
