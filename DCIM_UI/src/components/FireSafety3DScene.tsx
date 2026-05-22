/**
 * FireSafety3DScene – 4-floor datacenter, 1 000 racks (250 / floor via InstancedMesh).
 */

import { useRef, useState, useMemo, useCallback, useEffect, Component, type ReactNode } from 'react'
import { Canvas, useFrame, useThree } from '@react-three/fiber'
import { OrbitControls, Text, Billboard } from '@react-three/drei'
import * as THREE from 'three'

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
export type SensorStatus = 'normal' | 'alarm' | 'fault' | 'offline' | 'testing'
export type SensorType   = 'smoke' | 'heat' | 'water-leak' | 'co2' | 'vesda'

export interface SceneSensor {
  id: string; name: string; type: SensorType; zone: string; status: SensorStatus
}

interface Props {
  sensors: SceneSensor[]
  onSensorClick?: (id: string, status: SensorStatus) => void
}

// ── Constants ──────────────────────────────────────────────────────────────────
const FLOOR_COUNT = 4
const FLOOR_H     = 5.5          // per-floor clear height (m)
const TOTAL_H     = FLOOR_COUNT * FLOOR_H   // 22 m
const B           = { w: 64, d: 44 }

const RACK        = { w: 0.72, d: 1.05, h: 2.2 }
const RACK_COLS   = 25           // racks per row
const RACK_ROWS   = 10           // rows per floor  →  250 / floor × 4 = 1 000
const RACK_GAP_C  = 1.55
const RACK_GAP_R  = 3.6

const FLOOR_COLORS = ['#1d4ed8', '#15803d', '#7c3aed', '#c2410c'] as const
const FLOOR_NAMES  = ['G – Ground', 'L1', 'L2', 'L3 – Top'] as const

const STATUS_HEX: Record<SensorStatus, string> = {
  normal:  '#00ff88',
  alarm:   '#ff2244',
  fault:   '#ffb800',
  offline: '#8899aa',
  testing: '#00aaff',
}

const ZONE_DEFS = [
  { id: 'sra', label: 'Server Room A', x: -16, z: -5,  w: 28, d: 30, color: '#2563eb' },
  { id: 'srb', label: 'Server Room B', x:  16, z: -5,  w: 28, d: 30, color: '#7c3aed' },
  { id: 'noc', label: 'NOC',           x:   0, z: 17,  w: 46, d:  6, color: '#0891b2' },
  { id: 'cor', label: 'Hot Aisle',     x:   0, z: -5,  w:  6, d: 30, color: '#d97706' },
]

function sensorPos(s: SceneSensor, idx: number): [number, number, number] {
  const fi   = idx % FLOOR_COUNT
  const fy   = fi * FLOOR_H
  const ceil = s.type !== 'water-leak'
  const y    = ceil ? fy + FLOOR_H - 0.35 : fy + 0.1
  const map: Record<string, [number, number, number][]> = {
    sra: [[-22, y, -11], [-15, y, -3], [-22, y, 6], [-12, y, 13]],
    srb: [[ 22, y, -11], [ 15, y, -3], [ 22, y, 6], [ 12, y, 13]],
    noc: [[-14, y, 17], [0, y, 17], [14, y, 17]],
    cor: [[0, y, -7], [0, y, 4]],
  }
  const pool = map[s.zone] ?? map.sra
  return pool[idx % pool.length]
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
function MultiFloorShell() {
  return (
    <group>
      {/* Floor slabs */}
      {Array.from({ length: FLOOR_COUNT }, (_, f) => (
        <FloorSlab key={f} y={f * FLOOR_H} fi={f} />
      ))}

      {/* Roof */}
      <mesh rotation={[Math.PI / 2, 0, 0]} position={[0, TOTAL_H, 0]}>
        <planeGeometry args={[B.w, B.d]} />
        <meshStandardMaterial color="#0d1e30" roughness={0.9} side={THREE.DoubleSide} />
      </mesh>

      {/* Exterior walls – full height */}
      {([
        { pos: [0, TOTAL_H / 2, -B.d / 2] as [number,number,number], sz: [B.w, TOTAL_H, 0.4] as [number,number,number] },
        { pos: [0, TOTAL_H / 2,  B.d / 2] as [number,number,number], sz: [B.w, TOTAL_H, 0.4] as [number,number,number] },
        { pos: [-B.w / 2, TOTAL_H / 2, 0] as [number,number,number], sz: [0.4, TOTAL_H, B.d] as [number,number,number] },
        { pos: [ B.w / 2, TOTAL_H / 2, 0] as [number,number,number], sz: [0.4, TOTAL_H, B.d] as [number,number,number] },
      ]).map(({ pos, sz }, i) => (
        <mesh key={i} position={pos}>
          <boxGeometry args={sz} />
          <meshStandardMaterial color="#1e3a5f" roughness={0.7} metalness={0.2} />
        </mesh>
      ))}

      {/* Floor-level fascia bands (exterior) */}
      {Array.from({ length: FLOOR_COUNT }, (_, f) => (
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

      {/* Steel columns – full height */}
      {([-28, -14, 0, 14, 28] as number[]).flatMap((x) =>
        [-18, 0, 18].map((z) => (
          <mesh key={`col-${x}-${z}`} position={[x, TOTAL_H / 2, z]}>
            <cylinderGeometry args={[0.3, 0.3, TOTAL_H, 8]} />
            <meshStandardMaterial color="#4a7db5" metalness={0.8} roughness={0.2} />
          </mesh>
        ))
      )}

      {/* Floor number labels in 3D */}
      {Array.from({ length: FLOOR_COUNT }, (_, f) => (
        <Billboard key={`lbl-${f}`} position={[-B.w / 2 + 1.8, f * FLOOR_H + FLOOR_H / 2, -B.d / 2 + 0.8]}>
          <Text fontSize={1.0} color="#60a5fa" anchorX="left" outlineWidth={0.04} outlineColor="#000">
            {FLOOR_NAMES[f]}
          </Text>
        </Billboard>
      ))}
    </group>
  )
}

// ── Stairwell ─────────────────────────────────────────────────────────────────
function Stairwell() {
  const SX   = B.w / 2 - 4.5
  const SZ   = B.d / 2 - 4.5
  const STEPS = 12
  const stepH = FLOOR_H / STEPS
  const stepD = 0.36

  return (
    <group position={[SX, 0, SZ]}>
      <mesh position={[0, TOTAL_H / 2, -2.4]}>
        <boxGeometry args={[5, TOTAL_H, 0.2]} />
        <meshStandardMaterial color="#243b55" roughness={0.7} />
      </mesh>
      <mesh position={[-2.4, TOTAL_H / 2, 0]}>
        <boxGeometry args={[0.2, TOTAL_H, 5]} />
        <meshStandardMaterial color="#243b55" roughness={0.7} />
      </mesh>
      {/* Stair treads per floor */}
      {Array.from({ length: FLOOR_COUNT }, (_, f) =>
        Array.from({ length: STEPS }, (__, s) => (
          <mesh key={`${f}-${s}`} position={[-0.5, f * FLOOR_H + s * stepH + stepH / 2, -2.0 + s * stepD]}>
            <boxGeometry args={[1.4, 0.07, stepD]} />
            <meshStandardMaterial color="#334155" roughness={0.6} metalness={0.35} />
          </mesh>
        ))
      )}
      {/* Handrail */}
      {Array.from({ length: FLOOR_COUNT }, (_, f) => (
        <mesh key={`rail-${f}`} position={[0.2, f * FLOOR_H + FLOOR_H / 2 + 0.5, -2.0 + (STEPS / 2) * stepD]}>
          <cylinderGeometry args={[0.04, 0.04, FLOOR_H * 0.95, 6]} />
          <meshStandardMaterial color="#64748b" metalness={0.7} roughness={0.2} />
        </mesh>
      ))}
    </group>
  )
}

// ── Instanced racks – 250 per floor ──────────────────────────────────────────
function FloorRacks({ fi, visible }: { fi: number; visible: boolean }) {
  const count  = RACK_COLS * RACK_ROWS
  const ref    = useRef<THREE.InstancedMesh>(null!)
  const startX = -((RACK_COLS - 1) / 2) * RACK_GAP_C
  const startZ = -((RACK_ROWS - 1) / 2) * RACK_GAP_R
  const baseY  = fi * FLOOR_H

  useEffect(() => {
    const mesh = ref.current
    if (!mesh) return
    const dummy = new THREE.Object3D()
    for (let row = 0; row < RACK_ROWS; row++) {
      for (let col = 0; col < RACK_COLS; col++) {
        dummy.position.set(
          startX + col * RACK_GAP_C,
          baseY + RACK.h / 2,
          startZ + row * RACK_GAP_R,
        )
        dummy.updateMatrix()
        mesh.setMatrixAt(row * RACK_COLS + col, dummy.matrix)
      }
    }
    mesh.instanceMatrix.needsUpdate = true
  }, [startX, startZ, baseY])

  // LED status dots (one instanced plane per rack)
  const ledRef = useRef<THREE.InstancedMesh>(null!)
  useEffect(() => {
    const mesh = ledRef.current
    if (!mesh) return
    const dummy = new THREE.Object3D()
    const col   = new THREE.Color()
    for (let row = 0; row < RACK_ROWS; row++) {
      for (let col2 = 0; col2 < RACK_COLS; col2++) {
        const idx = row * RACK_COLS + col2
        dummy.position.set(
          startX + col2 * RACK_GAP_C + RACK.w / 2 - 0.06,
          baseY + RACK.h * 0.75,
          startZ + row * RACK_GAP_R - RACK.d / 2 - 0.02,
        )
        dummy.rotation.y = 0
        dummy.updateMatrix()
        mesh.setMatrixAt(idx, dummy.matrix)
        col.set(idx % 7 === 0 ? '#ffcc00' : '#00ff66')
        mesh.setColorAt(idx, col)
      }
    }
    mesh.instanceMatrix.needsUpdate = true
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true
  }, [startX, startZ, baseY])

  return (
    <group visible={visible}>
      <instancedMesh ref={ref} args={[undefined, undefined, count]}>
        <boxGeometry args={[RACK.w, RACK.h, RACK.d]} />
        <meshStandardMaterial color={FLOOR_COLORS[fi]} roughness={0.35} metalness={0.55} />
      </instancedMesh>
      <instancedMesh ref={ledRef} args={[undefined, undefined, count]}>
        <planeGeometry args={[0.045, 0.09]} />
        <meshBasicMaterial vertexColors />
      </instancedMesh>
    </group>
  )
}

// ── VESDA pipes per floor (InstancedMesh) ─────────────────────────────────────
const VESDA_ROWS   = [-15, -8.5, -2, 4.5, 11]
const VESDA_DET_X  = [-26, -18, -9, 0, 9, 18, 26]
const VESDA_SPHERES = VESDA_ROWS.length * VESDA_DET_X.length  // 35

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

// ── FM-200 nozzles per floor (InstancedMesh) ──────────────────────────────────
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
      d.position.set(x, cy, z);       d.updateMatrix(); coneRef.current.setMatrixAt(i, d.matrix)
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

// ── Emergency lighting per floor ──────────────────────────────────────────────
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

// ── Exit signs per floor ──────────────────────────────────────────────────────
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
          <mesh>
            <boxGeometry args={[1.1, 0.42, 0.06]} />
            <meshBasicMaterial color="#00cc44" />
          </mesh>
          <mesh position={[0, 0, 0.04]}>
            <boxGeometry args={[1.06, 0.38, 0.01]} />
            <meshBasicMaterial color="#00ff55" />
          </mesh>
          {/* exit text removed – green box is sufficient visual cue */}
        </group>
      ))}
    </group>
  )
}

// ── Exit gates ────────────────────────────────────────────────────────────────
function ExitGate({ position, rotY = 0 }: {
  position: [number, number, number]; rotY?: number
}) {
  const DW = 1.85, DH = 2.5, FT = 0.16, PW = DW / 2 - 0.03
  const SWING = Math.PI * 0.42
  return (
    <group position={position} rotation={[0, rotY, 0]}>
      <mesh position={[0, DH + FT / 2, 0]}>
        <boxGeometry args={[DW + FT * 2, FT, FT]} />
        <meshStandardMaterial color="#2a4a6b" metalness={0.6} roughness={0.3} />
      </mesh>
      <mesh position={[-(DW / 2 + FT / 2), DH / 2, 0]}>
        <boxGeometry args={[FT, DH, FT]} />
        <meshStandardMaterial color="#2a4a6b" metalness={0.6} roughness={0.3} />
      </mesh>
      <mesh position={[DW / 2 + FT / 2, DH / 2, 0]}>
        <boxGeometry args={[FT, DH, FT]} />
        <meshStandardMaterial color="#2a4a6b" metalness={0.6} roughness={0.3} />
      </mesh>
      {/* Left panel – hinge at left edge, swung open */}
      <group position={[-DW / 2, DH / 2, 0]} rotation={[0, SWING, 0]}>
        <mesh position={[PW / 2, 0, 0]}>
          <boxGeometry args={[PW, DH, 0.05]} />
          <meshStandardMaterial color="#4a7db5" metalness={0.55} roughness={0.3} transparent opacity={0.88} />
        </mesh>
      </group>
      {/* Right panel – hinge at right edge, swung open */}
      <group position={[DW / 2, DH / 2, 0]} rotation={[0, -SWING, 0]}>
        <mesh position={[-PW / 2, 0, 0]}>
          <boxGeometry args={[PW, DH, 0.05]} />
          <meshStandardMaterial color="#4a7db5" metalness={0.55} roughness={0.3} transparent opacity={0.88} />
        </mesh>
      </group>
      <mesh position={[0, DH + FT + 0.12, 0]}>
        <boxGeometry args={[0.45, 0.16, 0.08]} />
        <meshStandardMaterial color="#00ff55" emissive="#00ff55" emissiveIntensity={2.0} />
      </mesh>
      {/* gate label text removed – emissive indicator is sufficient */}
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

// ── CRAC / AC cooling units ────────────────────────────────────────────────────
function CRACUnit({ position, rotY = 0 }: { position: [number, number, number]; rotY?: number }) {
  return (
    <group position={position} rotation={[0, rotY, 0]}>
      <mesh>
        <boxGeometry args={[1.2, 2.0, 0.75]} />
        <meshStandardMaterial color="#1a3350" metalness={0.65} roughness={0.25} />
      </mesh>
      <mesh position={[0, 0, 0.40]}>
        <boxGeometry args={[1.0, 1.7, 0.05]} />
        <meshStandardMaterial color="#2d5a8e" emissive="#00aacc" emissiveIntensity={0.6} metalness={0.85} roughness={0.12} />
      </mesh>
      {[-0.22, 0, 0.22].map((x, i) => (
        <mesh key={i} position={[x, 0.8, 0.43]}>
          <boxGeometry args={[0.06, 0.06, 0.01]} />
          <meshStandardMaterial
            color={i === 0 ? '#00ff88' : i === 1 ? '#00aaff' : '#22ddff'}
            emissive={i === 0 ? '#00ff88' : i === 1 ? '#00aaff' : '#22ddff'}
            emissiveIntensity={3.0}
          />
        </mesh>
      ))}
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

// ── Animated airflow streams ───────────────────────────────────────────────────
function AirflowStream({ sx, sz, ex, ez, fy, phase: initPhase }: {
  sx: number; sz: number; ex: number; ez: number; fy: number; phase: number
}) {
  const r0 = useRef<THREE.Mesh>(null!)
  const r1 = useRef<THREE.Mesh>(null!)
  const r2 = useRef<THREE.Mesh>(null!)
  const dx = ex - sx, dz = ez - sz
  const seg = Math.hypot(dx, dz) / 3

  // Pulse opacity only — no position mutation, avoids React/Three conflict
  useFrame(() => {
    const t = performance.now() / 1000
    const refs = [r0, r1, r2]
    for (let i = 0; i < 3; i++) {
      const m = refs[i].current
      if (!m) continue
      const phase = (t * 0.7 + initPhase + i / 3) % 1
      ;(m.material as THREE.MeshBasicMaterial).opacity = Math.sin(phase * Math.PI) * 0.32
    }
  })

  return (
    <group>
      <mesh ref={r0} rotation={[-Math.PI / 2, 0, 0]} position={[sx + dx * 0.17, fy, sz + dz * 0.17]}>
        <planeGeometry args={[seg * 0.85, 2.4]} />
        <meshBasicMaterial color="#00ccff" transparent opacity={0.25} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <mesh ref={r1} rotation={[-Math.PI / 2, 0, 0]} position={[sx + dx * 0.50, fy, sz + dz * 0.50]}>
        <planeGeometry args={[seg * 0.85, 2.4]} />
        <meshBasicMaterial color="#00ccff" transparent opacity={0.25} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      <mesh ref={r2} rotation={[-Math.PI / 2, 0, 0]} position={[sx + dx * 0.83, fy, sz + dz * 0.83]}>
        <planeGeometry args={[seg * 0.85, 2.4]} />
        <meshBasicMaterial color="#00ccff" transparent opacity={0.25} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
    </group>
  )
}

function FloorAirflows({ fi }: { fi: number }) {
  const fy  = fi * FLOOR_H + 0.12
  const lx  = -B.w / 2 + 1.3
  const rx  =  B.w / 2 - 1.3
  return (
    <group>
      {[-13, -4, 5, 14].map((z, zi) => (
        <group key={z}>
          <AirflowStream sx={lx}  sz={z} ex={-3.5} ez={z} fy={fy} phase={zi * 0.25} />
          <AirflowStream sx={rx}  sz={z} ex={ 3.5} ez={z} fy={fy} phase={zi * 0.25 + 0.5} />
        </group>
      ))}
    </group>
  )
}

// ── Cooling coverage map overlay ───────────────────────────────────────────────
const COOLING_STRIPS: { cx: number; w: number; color: string; alpha: number; tag?: string }[] = [
  { cx: -28,  w: 5,  color: '#00ffff', alpha: 0.30, tag: 'Very Cold' },
  { cx: -22,  w: 6,  color: '#00ddff', alpha: 0.26 },
  { cx: -16,  w: 6,  color: '#00aaff', alpha: 0.22 },
  { cx: -10,  w: 6,  color: '#2299ff', alpha: 0.18 },
  { cx: -5.5, w: 5,  color: '#ffaa00', alpha: 0.24 },
  { cx:  0,   w: 6,  color: '#ff3300', alpha: 0.32, tag: 'Hot Aisle' },
  { cx:  5.5, w: 5,  color: '#ffaa00', alpha: 0.24 },
  { cx:  10,  w: 6,  color: '#2299ff', alpha: 0.18 },
  { cx:  16,  w: 6,  color: '#00aaff', alpha: 0.22 },
  { cx:  22,  w: 6,  color: '#00ddff', alpha: 0.26 },
  { cx:  28,  w: 5,  color: '#00ffff', alpha: 0.30 },
]

function CoolingMapOverlay({ fi }: { fi: number }) {
  const fy = fi * FLOOR_H + 0.03
  return (
    <group>
      {COOLING_STRIPS.map(({ cx, w, color, alpha, tag }, i) => (
        <group key={i}>
          <mesh rotation={[-Math.PI / 2, 0, 0]} position={[cx, fy, -5]}>
            <planeGeometry args={[w, 28]} />
            <meshBasicMaterial color={color} transparent opacity={alpha} side={THREE.DoubleSide} depthWrite={false} />
          </mesh>
          {/* cooling strip tags removed – UI legend covers this */}
        </group>
      ))}
      {/* NOC zone – insufficient cooling */}
      <mesh rotation={[-Math.PI / 2, 0, 0]} position={[0, fy, 17]}>
        <planeGeometry args={[44, 6]} />
        <meshBasicMaterial color="#ffcc00" transparent opacity={0.22} side={THREE.DoubleSide} depthWrite={false} />
      </mesh>
      {/* NOC label removed – UI legend covers this */}
    </group>
  )
}

// ── Walking people (ground floor only) ───────────────────────────────────────
const PEOPLE_DEFS = [
  {
    id: 'eng1', color: '#f97316', helmetColor: '#facc15', speed: 2.8, startOffset: 0.0,
    waypoints: [[0,0,-14],[0,0,-7],[0,0,0],[0,0,7],[0,0,12],[0,0,7],[0,0,0],[0,0,-7]] as [number,number,number][],
  },
  {
    id: 'tech1', color: '#3b82f6', helmetColor: '#f1f5f9', speed: 2.2, startOffset: 0.28,
    waypoints: [[-14,0,-13],[-14,0,-6],[-14,0,0],[-14,0,6],[-14,0,0],[-14,0,-6]] as [number,number,number][],
  },
  {
    id: 'tech2', color: '#22c55e', helmetColor: '#f1f5f9', speed: 2.4, startOffset: 0.55,
    waypoints: [[18,0,-13],[18,0,-6],[18,0,0],[18,0,6],[18,0,0],[18,0,-6]] as [number,number,number][],
  },
  {
    id: 'noc1', color: '#a855f7', helmetColor: '#64748b', speed: 1.6, startOffset: 0.1,
    waypoints: [[-12,0,15],[-6,0,15],[0,0,15],[6,0,15],[12,0,15],[6,0,15],[0,0,15],[-6,0,15]] as [number,number,number][],
  },
  {
    id: 'sup1', color: '#ef4444', helmetColor: '#fb923c', speed: 1.9, startOffset: 0.72,
    waypoints: [[-24,0,-13],[0,0,-13],[22,0,-13],[22,0,6],[0,0,6],[-24,0,6]] as [number,number,number][],
  },
] as const

function WalkingPerson({ waypoints, color, helmetColor, speed, startOffset }: {
  waypoints: readonly [number,number,number][]
  color: string; helmetColor: string; speed: number; startOffset: number
}) {
  const rootRef     = useRef<THREE.Group>(null!)
  const lLegRef     = useRef<THREE.Group>(null!)
  const rLegRef     = useRef<THREE.Group>(null!)
  const lArmRef     = useRef<THREE.Group>(null!)
  const rArmRef     = useRef<THREE.Group>(null!)
  const tRef        = useRef(startOffset)
  const phaseRef    = useRef(startOffset * Math.PI * 6)
  const segLens     = useMemo(() => waypoints.map((p, i) => {
    const n = waypoints[(i + 1) % waypoints.length]
    return Math.hypot(n[0] - p[0], n[2] - p[2])
  }), [waypoints])
  const totalLen    = useMemo(() => segLens.reduce((a, b) => a + b, 0), [segLens])

  useFrame((_, dt) => {
    if (!rootRef.current) return
    tRef.current    = (tRef.current + speed * dt / totalLen) % 1
    phaseRef.current += speed * dt * 5
    let rem = tRef.current * totalLen, seg = 0
    for (; seg < segLens.length - 1 && rem > segLens[seg]; seg++) rem -= segLens[seg]
    const u    = segLens[seg] > 0.001 ? Math.min(rem / segLens[seg], 1) : 0
    const from = waypoints[seg], to = waypoints[(seg + 1) % waypoints.length]
    const bob  = Math.abs(Math.sin(phaseRef.current)) * 0.032
    rootRef.current.position.set(from[0]+(to[0]-from[0])*u, bob, from[2]+(to[2]-from[2])*u)
    rootRef.current.rotation.y = Math.atan2(to[0]-from[0], to[2]-from[2])
    const swing = Math.sin(phaseRef.current) * 0.44
    if (lLegRef.current) lLegRef.current.rotation.x  =  swing
    if (rLegRef.current) rLegRef.current.rotation.x  = -swing
    if (lArmRef.current) lArmRef.current.rotation.x  = -swing * 0.6
    if (rArmRef.current) rArmRef.current.rotation.x  =  swing * 0.6
  })

  const LL = 0.45, AL = 0.36
  return (
    <group ref={rootRef}>
      <mesh position={[0, 1.42, 0]}>
        <sphereGeometry args={[0.155, 10, 8]} />
        <meshStandardMaterial color="#f5c5a3" roughness={0.8} />
      </mesh>
      <mesh position={[0, 1.485, 0]}>
        <sphereGeometry args={[0.178, 10, 6]} />
        <meshStandardMaterial color={helmetColor} roughness={0.4} metalness={0.2} />
      </mesh>
      <mesh position={[0, 0.9, 0]}>
        <boxGeometry args={[0.3, 0.46, 0.18]} />
        <meshStandardMaterial color={color} roughness={0.5} />
      </mesh>
      <group ref={lLegRef} position={[-0.085, 0.45, 0]}>
        <mesh position={[0, -LL / 2, 0]}><boxGeometry args={[0.11, LL, 0.12]} /><meshStandardMaterial color="#334155" /></mesh>
        <mesh position={[0, -LL - 0.035, 0.05]}><boxGeometry args={[0.1, 0.07, 0.2]} /><meshStandardMaterial color="#0f172a" /></mesh>
      </group>
      <group ref={rLegRef} position={[0.085, 0.45, 0]}>
        <mesh position={[0, -LL / 2, 0]}><boxGeometry args={[0.11, LL, 0.12]} /><meshStandardMaterial color="#334155" /></mesh>
        <mesh position={[0, -LL - 0.035, 0.05]}><boxGeometry args={[0.1, 0.07, 0.2]} /><meshStandardMaterial color="#0f172a" /></mesh>
      </group>
      <group ref={lArmRef} position={[-0.2, 1.1, 0]}>
        <mesh position={[0, -AL / 2, 0]}><boxGeometry args={[0.1, AL, 0.1]} /><meshStandardMaterial color={color} /></mesh>
      </group>
      <group ref={rArmRef} position={[0.2, 1.1, 0]}>
        <mesh position={[0, -AL / 2, 0]}><boxGeometry args={[0.1, AL, 0.1]} /><meshStandardMaterial color={color} /></mesh>
      </group>
    </group>
  )
}

function WalkingPeople({ visible }: { visible: boolean }) {
  if (!visible) return null
  return (
    <group>
      {PEOPLE_DEFS.map((cfg) => <WalkingPerson key={cfg.id} {...cfg} />)}
    </group>
  )
}

// ── NOC room (ground floor) ───────────────────────────────────────────────────
function NOCRoom() {
  return (
    <group>
      <mesh position={[0, FLOOR_H * 0.35, 13.5]}>
        <boxGeometry args={[44, FLOOR_H * 0.7, 0.3]} />
        <meshStandardMaterial color="#1e3a5f" roughness={0.6} metalness={0.3} />
      </mesh>
      {[-14, -7, 0, 7, 14].map((x, i) => (
        <group key={i} position={[x, 0, 16]}>
          <mesh position={[0, 0.8, 0.15]}>
            <boxGeometry args={[1.7, 0.07, 0.85]} />
            <meshStandardMaterial color="#243b55" roughness={0.4} metalness={0.5} />
          </mesh>
          <mesh position={[0, 1.42, -0.1]}>
            <boxGeometry args={[0.8, 0.48, 0.05]} />
            <meshStandardMaterial color="#0ea5e9" emissive="#0ea5e9" emissiveIntensity={2.5} roughness={0.3} />
          </mesh>
        </group>
      ))}
    </group>
  )
}

// ── Evac arrows ───────────────────────────────────────────────────────────────
function EvacArrow({ from, to }: { from: [number,number,number]; to: [number,number,number] }) {
  const dir = new THREE.Vector3(...to).sub(new THREE.Vector3(...from))
  const len = dir.length()
  const mid: [number,number,number] = [(from[0]+to[0])/2, 0.04, (from[2]+to[2])/2]
  return (
    <group position={mid} rotation={[0, Math.atan2(dir.x, dir.z), 0]}>
      <mesh rotation={[Math.PI/2, 0, 0]}>
        <planeGeometry args={[0.3, len * 0.72]} />
        <meshBasicMaterial color="#00ff88" transparent opacity={0.9} side={THREE.DoubleSide} />
      </mesh>
      <mesh position={[0, 0, -(len * 0.36 - 0.25)]} rotation={[Math.PI/2, 0, 0]}>
        <coneGeometry args={[0.22, 0.5, 6]} />
        <meshBasicMaterial color="#00ffaa" />
      </mesh>
    </group>
  )
}

function EvacPaths({ visible }: { visible: boolean }) {
  if (!visible) return null
  const paths: [[number,number,number],[number,number,number]][] = [
    [[-22,0,-11],[-30,0,-6]],[[-22,0,-4],[-30,0,-6]],[[-22,0,4],[-30,0,-6]],
    [[ 22,0,-11],[ 30,0,-6]],[[ 22,0,-4],[ 30,0,-6]],[[ 22,0,4],[ 30,0,-6]],
    [[0,0,-10],[0,0,-20]],  [[0,0,15],[0,0,21]],
    [[-14,0,-7],[0,0,-7]],  [[14,0,-7],[0,0,-7]],
  ]
  return <group>{paths.map(([f, t], i) => <EvacArrow key={i} from={f} to={t} />)}</group>
}

// ── Zone floor overlays (ground floor) ────────────────────────────────────────
function ZoneOverlays({ showZones, alarmZones }: { showZones: boolean; alarmZones: Set<string> }) {
  if (!showZones) return null
  return (
    <group>
      {ZONE_DEFS.map((z) => {
        const alarm = alarmZones.has(z.id)
        return (
          <group key={z.id}>
            <mesh rotation={[-Math.PI / 2, 0, 0]} position={[z.x, 0.02, z.z]}>
              <planeGeometry args={[z.w, z.d]} />
              <meshBasicMaterial color={alarm ? '#ff0000' : z.color} transparent opacity={alarm ? 0.35 : 0.28} side={THREE.DoubleSide} />
            </mesh>
            <Billboard position={[z.x, 0.3, z.z]}>
              <Text fontSize={0.7} color={alarm ? '#ff6666' : '#a8c8ff'} anchorX="center" outlineWidth={0.02} outlineColor="#000">
                {z.label}
              </Text>
            </Billboard>
          </group>
        )
      })}
    </group>
  )
}

// ── Sensor marker ─────────────────────────────────────────────────────────────
function SensorMarker({ sensor, position, onClick }: {
  sensor: SceneSensor; position: [number,number,number]; onClick: () => void
}) {
  const glowRef  = useRef<THREE.Mesh>(null!)
  const isAlarm  = sensor.status === 'alarm' || sensor.status === 'fault'
  const isCeil   = sensor.type !== 'water-leak'
  const color    = STATUS_HEX[sensor.status]
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
        <cylinderGeometry args={[0.18, 0.18, 0.1, 16]} />
        <meshStandardMaterial color="#2a3f55" roughness={0.3} metalness={0.7} />
      </mesh>
      <mesh ref={glowRef} position={[0, yGlow, 0]}>
        <sphereGeometry args={[0.22, 16, 16]} />
        <meshBasicMaterial color={threeCol} transparent opacity={0.5} />
      </mesh>
      <mesh position={[0, yGlow, 0]} onClick={onClick}>
        <sphereGeometry args={[0.1, 12, 12]} />
        <meshBasicMaterial color={threeCol} />
      </mesh>
      <Billboard position={[0, isCeil ? -0.75 : 0.75, 0]}>
        <Text fontSize={0.28} color={color} anchorX="center" outlineWidth={0.025} outlineColor="#000">
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
      <mesh ref={meshRef}>
        <sphereGeometry args={[0.28, 16, 16]} />
        <meshBasicMaterial color="#ff2244" />
      </mesh>
    </group>
  )
}

// ── Lighting ──────────────────────────────────────────────────────────────────
function SceneLights({ hasAlarm }: { hasAlarm: boolean }) {
  return (
    <>
      <ambientLight intensity={1.5} color="#c8dff5" />
      <hemisphereLight args={['#b8d4f0', '#1e3a5f', 1.1]} />
      <directionalLight position={[15, 35, 20]} intensity={2.2} color="#ffffff" />
      <directionalLight position={[-15, 20, -20]} intensity={1.0} color="#9bc4e8" />
      {/* One ceiling fill-light per floor – 4 total */}
      {Array.from({ length: FLOOR_COUNT }, (_, f) => (
        <pointLight
          key={`fl-${f}`}
          position={[0, f * FLOOR_H + FLOOR_H - 0.7, 0]}
          intensity={8}
          distance={60}
          color="#d0e8ff"
        />
      ))}
      {/* NOC area */}
      <pointLight position={[0, FLOOR_H - 0.7, 17]} intensity={3.5} distance={20} color="#b0d8ff" />
      {hasAlarm && <ambientLight intensity={0.5} color="#6b1a1a" />}
    </>
  )
}

// ── Camera controller ─────────────────────────────────────────────────────────
function CameraController({ preset }: { preset: string }) {
  const { camera } = useThree()
  useEffect(() => {
    const map: Record<string, [number,number,number]> = {
      overview:  [0, 55, 70],
      topdown:   [0, 90, 3],
      'floor-0': [0, 18, 48],
      'floor-1': [0, 18 + FLOOR_H, 48],
      'floor-2': [0, 18 + FLOOR_H * 2, 48],
      'floor-3': [0, 18 + FLOOR_H * 3, 48],
      sra:       [-26, 18, 14],
      srb:       [ 26, 18, 14],
      noc:       [0, 16, 34],
    }
    const p = map[preset] ?? map.overview
    camera.position.set(...p)
  }, [preset, camera])
  return null
}

// ══════════════════════════════════════════════════════════════════════════════
// EXPORTED SCENE
// ══════════════════════════════════════════════════════════════════════════════

export default function FireSafety3DScene({ sensors, onSensorClick }: Props) {
  const [showSensors, setShowSensors] = useState(true)
  const [showEvac,    setShowEvac]    = useState(true)
  const [showZones,   setShowZones]   = useState(true)
  const [showVESDA,   setShowVESDA]   = useState(true)
  const [showPeople,  setShowPeople]  = useState(true)
  const [preset,      setPreset]      = useState('overview')
  const [selected,    setSelected]    = useState<SceneSensor | null>(null)
  // null = all floors visible; 0-3 = specific floor
  const [activeFloor,    setActiveFloor]    = useState<number | null>(null)
  const [showExitGates,  setShowExitGates]  = useState(true)
  const [showACFlow,     setShowACFlow]     = useState(true)
  const [showCoolingMap, setShowCoolingMap] = useState(false)

  const alarmZones = useMemo(
    () => new Set(sensors.filter((s) => s.status === 'alarm').map((s) => s.zone)),
    [sensors],
  )
  const hasAlarm = alarmZones.size > 0

  const sensorPosMap = useMemo(
    () => sensors.map((s, i) => ({ sensor: s, pos: sensorPos(s, i) })),
    [sensors],
  )

  const handleSensor = useCallback((s: SceneSensor) => {
    setSelected((p) => (p?.id === s.id ? null : s))
    onSensorClick?.(s.id, s.status)
  }, [onSensorClick])

  const floorVisible = (fi: number) => activeFloor === null || activeFloor === fi

  // Camera target Y for orbit controls
  const orbitTarget: [number,number,number] = activeFloor !== null
    ? [0, activeFloor * FLOOR_H + FLOOR_H / 2, 0]
    : [0, TOTAL_H / 2, 0]

  type LayerToggle = [string, boolean, React.Dispatch<React.SetStateAction<boolean>>]
  const layerToggles: LayerToggle[] = [
    ['Sensors',     showSensors,    setShowSensors],
    ['Evac paths',  showEvac,       setShowEvac],
    ['Zones',       showZones,      setShowZones],
    ['VESDA pipes', showVESDA,      setShowVESDA],
    ['People',      showPeople,     setShowPeople],
    ['Exit Gates',  showExitGates,  setShowExitGates],
    ['AC & Flow',   showACFlow,     setShowACFlow],
    ['Cooling Map', showCoolingMap, setShowCoolingMap],
  ]

  const coolingLegend: [string, string][] = [
    ['#00ffff', 'CRAC outlet (very cold)'],
    ['#00aaff', 'Cooled zone'],
    ['#2299ff', 'Moderate cooling'],
    ['#ffaa00', 'Warm (low coverage)'],
    ['#ff3300', 'Hot aisle'],
    ['#ffcc00', 'Insufficient cooling'],
  ]

  const floorOptions: [number | null, string, string][] = [
    [null, 'All', FLOOR_COLORS[0]],
    [0,    'G',   FLOOR_COLORS[0]],
    [1,    'L1',  FLOOR_COLORS[1]],
    [2,    'L2',  FLOOR_COLORS[2]],
    [3,    'L3',  FLOOR_COLORS[3]],
  ]

  const cameraPresets: [string, string][] = [
    ['overview', 'Overview'],
    ['topdown',  'Top-Down'],
    ['sra',      'Zone A'],
    ['srb',      'Zone B'],
    ['noc',      'NOC'],
  ]

  return (
    <WebGLErrorBoundary>
    <div className="relative w-full rounded-xl overflow-hidden border border-slate-600" style={{ height: 680 }}>
      <Canvas
        camera={{ position: [0, 55, 70], fov: 50, near: 0.5, far: 800 }}
        gl={{ antialias: true, alpha: false, powerPreference: 'high-performance' }}
        dpr={[1, 1.5]}
        style={{ background: 'linear-gradient(180deg, #0a1628 0%, #0f2240 60%, #1a3a5f 100%)' }}
        onCreated={({ gl }) => { gl.setClearColor('#0a1628') }}
      >
        <CameraController preset={preset} />
        <SceneLights hasAlarm={hasAlarm} />

        <MultiFloorShell />
        <Stairwell />

        {/* 1 000 instanced racks — 250 per floor */}
        {Array.from({ length: FLOOR_COUNT }, (_, f) => (
          <FloorRacks key={f} fi={f} visible={floorVisible(f)} />
        ))}

        {/* Per-floor safety systems */}
        {Array.from({ length: FLOOR_COUNT }, (_, f) => (
          <group key={f} visible={floorVisible(f)}>
            {showVESDA && <FloorVESDA fi={f} />}
            <FloorFM200 fi={f} />
            <FloorEmergencyLighting fi={f} />
            <FloorExitSigns fi={f} />
            {showExitGates  && <FloorExitGates  fi={f} />}
            {showACFlow     && <FloorCRACUnits  fi={f} />}
            {showACFlow     && <FloorAirflows   fi={f} />}
            {showCoolingMap && <CoolingMapOverlay fi={f} />}
          </group>
        ))}

        {/* Ground-floor only content */}
        <group visible={floorVisible(0)}>
          <NOCRoom />
          <WalkingPeople visible={showPeople} />
          <EvacPaths visible={showEvac} />
          <ZoneOverlays showZones={showZones} alarmZones={alarmZones} />
        </group>

        {/* Sensors */}
        {showSensors && sensorPosMap.map(({ sensor, pos }) => (
          <SensorMarker key={sensor.id} sensor={sensor} position={pos} onClick={() => handleSensor(sensor)} />
        ))}

        {/* Alarm beacons */}
        {hasAlarm && sensorPosMap
          .filter(({ sensor }) => sensor.status === 'alarm')
          .map(({ pos }, i) => (
            <AlarmBeacon key={i} position={[pos[0], Math.floor(pos[1] / FLOOR_H) * FLOOR_H + FLOOR_H - 0.9, pos[2]]} />
          ))}

        <OrbitControls
          enableDamping
          dampingFactor={0.08}
          minDistance={5}
          maxDistance={160}
          maxPolarAngle={Math.PI / 1.65}
          target={orbitTarget}
        />
      </Canvas>

      {/* Status legend */}
      <div className="absolute top-3 left-3 bg-black/60 backdrop-blur-sm rounded-lg p-2.5 flex flex-col gap-1.5 pointer-events-none">
        {(Object.entries(STATUS_HEX) as [SensorStatus, string][]).map(([s, hex]) => (
          <div key={s} className="flex items-center gap-2">
            <div className="w-3 h-3 rounded-full" style={{ backgroundColor: hex, boxShadow: `0 0 6px ${hex}` }} />
            <span className="text-xs text-slate-200 capitalize">{s}</span>
          </div>
        ))}
        <div className="mt-1 pt-1 border-t border-slate-600">
          <span className="text-xs text-slate-400">1 000 racks · 4 floors</span>
        </div>
      </div>

      {/* Cooling map legend */}
      {showCoolingMap && (
        <div className="absolute top-48 left-3 bg-black/60 backdrop-blur-sm rounded-lg p-2.5 flex flex-col gap-1.5 pointer-events-none">
          <span className="text-xs text-slate-300 font-semibold">Cooling Coverage</span>
          {coolingLegend.map(([col, lbl]) => (
            <div key={lbl} className="flex items-center gap-2">
              <div className="w-3 h-3 rounded-sm border border-white/10" style={{ backgroundColor: col }} />
              <span className="text-xs text-slate-200">{lbl}</span>
            </div>
          ))}
        </div>
      )}

      {/* Alarm banner */}
      {hasAlarm && (
        <div className="absolute top-3 left-1/2 -translate-x-1/2 flex items-center gap-2 bg-red-600 border border-red-400 rounded-lg px-4 py-2 shadow-lg shadow-red-900/60 animate-pulse z-10">
          <div className="w-2.5 h-2.5 rounded-full bg-white" />
          <span className="text-sm font-bold text-white tracking-wider">
            ⚠ ALARM — {sensors.filter((s) => s.status === 'alarm').map((s) => s.name).join(' · ')}
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
      <div className="absolute bottom-14 right-3 flex flex-col gap-1.5 items-end">
        <span className="text-xs text-slate-500 pr-1">Floor</span>
        {floorOptions.map(([fi, label, col]) => (
          <button
            key={String(fi)}
            onClick={() => {
              setActiveFloor(fi)
              if (fi !== null) setPreset(`floor-${fi}`)
              else setPreset('overview')
            }}
            className={`text-xs px-3 py-1.5 rounded-lg border transition-all shadow-md w-16 ${
              activeFloor === fi
                ? 'text-white shadow-lg'
                : 'bg-black/50 border-slate-600 text-slate-300 hover:border-slate-400'
            }`}
            style={activeFloor === fi ? { backgroundColor: col + 'cc', borderColor: col } : {}}
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
              preset === key
                ? 'bg-blue-600 border-blue-400 text-white shadow-blue-900/50'
                : 'bg-black/50 border-slate-600 text-slate-300 hover:border-slate-400 hover:text-white'
            }`}
          >
            {label}
          </button>
        ))}
      </div>

      {/* Sensor info card */}
      {selected && (
        <div className="absolute bottom-3 left-3 bg-slate-900/95 border border-slate-500 rounded-xl p-4 min-w-[210px] shadow-xl">
          <div className="flex items-center justify-between mb-3">
            <span className="font-semibold text-white text-sm">{selected.name}</span>
            <button onClick={() => setSelected(null)} className="text-slate-400 hover:text-white ml-3">
              <svg className="w-3.5 h-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
              </svg>
            </button>
          </div>
          <div className="space-y-1.5 text-xs">
            {[['Type', selected.type], ['Zone', selected.zone.toUpperCase()]].map(([k, v]) => (
              <div key={k} className="flex justify-between">
                <span className="text-slate-400">{k}</span>
                <span className="text-slate-200 capitalize">{v}</span>
              </div>
            ))}
            <div className="flex justify-between">
              <span className="text-slate-400">Status</span>
              <span className="font-bold capitalize" style={{ color: STATUS_HEX[selected.status], textShadow: `0 0 8px ${STATUS_HEX[selected.status]}` }}>
                {selected.status}
              </span>
            </div>
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
