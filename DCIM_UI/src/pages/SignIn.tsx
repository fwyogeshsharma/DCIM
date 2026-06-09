import { useState, type FormEvent, useRef, useMemo } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Canvas, useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { Server, Lock, User } from 'lucide-react'
import { useAuthStore } from '../stores/useAuthStore'

// ─── Three.js scene ──────────────────────────────────────────────────────────

/** Fibonacci sphere — evenly distributes `count` points on a unit sphere */
function fibSphere(count: number, r: number): THREE.Vector3[] {
  const golden = (1 + Math.sqrt(5)) / 2
  return Array.from({ length: count }, (_, i) => {
    const phi = Math.acos(1 - (2 * (i + 0.5)) / count)
    const theta = 2 * Math.PI * i * golden
    return new THREE.Vector3(
      r * Math.sin(phi) * Math.cos(theta),
      r * Math.cos(phi),
      r * Math.sin(phi) * Math.sin(theta),
    )
  })
}

function DataGlobe() {
  const COUNT = 130
  const RADIUS = 3.4
  const groupRef = useRef<THREE.Group>(null!)
  const nodeRefs = useRef<THREE.Mesh[]>([])

  const points = useMemo(() => fibSphere(COUNT, RADIUS), [])

  // Static edges — only computed once
  const { linePositions, edgeCount } = useMemo(() => {
    const pairs: number[] = []
    for (let i = 0; i < COUNT; i++) {
      for (let j = i + 1; j < COUNT; j++) {
        if (points[i].distanceTo(points[j]) < 1.55) {
          pairs.push(i, j)
        }
      }
    }
    const edgeCount = pairs.length / 2
    const linePositions = new Float32Array(edgeCount * 6)
    for (let k = 0; k < edgeCount; k++) {
      const i = pairs[k * 2]
      const j = pairs[k * 2 + 1]
      linePositions[k * 6 + 0] = points[i].x
      linePositions[k * 6 + 1] = points[i].y
      linePositions[k * 6 + 2] = points[i].z
      linePositions[k * 6 + 3] = points[j].x
      linePositions[k * 6 + 4] = points[j].y
      linePositions[k * 6 + 5] = points[j].z
    }
    return { linePositions, edgeCount }
  }, [points])

  // Hot nodes that pulse (every 9th node)
  const hotIndices = useMemo(() => new Set(points.map((_, i) => i).filter((i) => i % 9 === 0)), [points])

  useFrame((state) => {
    if (groupRef.current) {
      groupRef.current.rotation.y += 0.0025
      groupRef.current.rotation.x = Math.sin(state.clock.elapsedTime * 0.12) * 0.08
    }
    const t = state.clock.elapsedTime
    nodeRefs.current.forEach((mesh, i) => {
      if (mesh && hotIndices.has(i)) {
        const scale = 1 + 0.4 * Math.abs(Math.sin(t * 1.8 + i))
        mesh.scale.setScalar(scale)
      }
    })
  })

  return (
    <group ref={groupRef}>
      {/* Edges */}
      <lineSegments>
        <bufferGeometry>
          <bufferAttribute
            attach="attributes-position"
            array={linePositions}
            count={edgeCount * 2}
            itemSize={3}
          />
        </bufferGeometry>
        <lineBasicMaterial color="#1d4ed8" transparent opacity={0.22} />
      </lineSegments>

      {/* Nodes */}
      {points.map((p, i) => (
        <mesh
          key={i}
          position={p}
          ref={(el) => { if (el) nodeRefs.current[i] = el }}
        >
          <sphereGeometry args={[hotIndices.has(i) ? 0.12 : 0.055, 8, 8]} />
          <meshBasicMaterial
            color={hotIndices.has(i) ? '#60a5fa' : i % 3 === 0 ? '#3b82f6' : '#1e3a8a'}
            transparent
            opacity={hotIndices.has(i) ? 1 : 0.75}
          />
        </mesh>
      ))}
    </group>
  )
}

function PulseRing({ delay, radius }: { delay: number; radius: number }) {
  const meshRef = useRef<THREE.Mesh>(null!)
  const matRef = useRef<THREE.MeshBasicMaterial>(null!)

  useFrame((state) => {
    const t = ((state.clock.elapsedTime + delay) % 4) / 4 // 0→1 every 4 s
    const scale = 0.3 + t * 3.5
    const opacity = (1 - t) * 0.35
    meshRef.current.scale.setScalar(scale)
    matRef.current.opacity = opacity
  })

  return (
    <mesh ref={meshRef} rotation={[Math.PI / 2, 0, 0]}>
      <torusGeometry args={[radius, 0.018, 6, 80]} />
      <meshBasicMaterial ref={matRef} color="#3b82f6" transparent />
    </mesh>
  )
}

function FloatingParticles() {
  const COUNT = 90
  const positions = useMemo(() => {
    const arr = new Float32Array(COUNT * 3)
    for (let i = 0; i < COUNT; i++) {
      arr[i * 3 + 0] = (Math.random() - 0.5) * 20
      arr[i * 3 + 1] = (Math.random() - 0.5) * 14
      arr[i * 3 + 2] = (Math.random() - 0.5) * 10 - 3
    }
    return arr
  }, [])

  const pointsRef = useRef<THREE.Points>(null!)

  useFrame((state) => {
    if (pointsRef.current) {
      pointsRef.current.rotation.y = state.clock.elapsedTime * 0.015
    }
  })

  return (
    <points ref={pointsRef}>
      <bufferGeometry>
        <bufferAttribute attach="attributes-position" array={positions} count={COUNT} itemSize={3} />
      </bufferGeometry>
      <pointsMaterial color="#1e40af" size={0.06} transparent opacity={0.5} />
    </points>
  )
}

function Scene() {
  return (
    <>
      <ambientLight intensity={0.4} />
      <FloatingParticles />
      <DataGlobe />
      <PulseRing delay={0} radius={1} />
      <PulseRing delay={1.33} radius={1} />
      <PulseRing delay={2.66} radius={1} />
    </>
  )
}

// ─── Sign In page ─────────────────────────────────────────────────────────────

export default function SignIn() {
  const login = useAuthStore((s) => s.login)
  const navigate = useNavigate()

  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')
    setLoading(true)
    await new Promise((r) => setTimeout(r, 400))
    const ok = login(username, password)
    setLoading(false)
    if (ok) {
      navigate('/app/dashboard', { replace: true })
    } else {
      setError('Invalid username or password')
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 flex">

      {/* ── Left panel — Three.js + branding ── */}
      <div className="hidden lg:flex flex-col justify-between w-1/2 relative overflow-hidden border-r border-white/5">

        {/* Three.js canvas fills the panel */}
        <Canvas
          camera={{ position: [0, 0, 9], fov: 50 }}
          style={{ position: 'absolute', inset: 0 }}
          gl={{ antialias: false, alpha: true }}
        >
          <Scene />
        </Canvas>

        {/* Dark gradient so text stays readable */}
        <div className="absolute inset-0 bg-gradient-to-br from-slate-950/70 via-blue-950/30 to-slate-950/80 pointer-events-none" />

        {/* Content on top */}
        <div className="relative z-10 p-12 flex flex-col justify-between h-full">
          {/* Logo */}
          <div className="flex items-center gap-3">
            <div className="w-9 h-9 rounded-lg bg-blue-600 flex items-center justify-center shadow-lg shadow-blue-600/30">
              <Server className="w-5 h-5 text-white" />
            </div>
            <span className="text-xl font-bold text-white">FWDCIM Enterprise</span>
          </div>

          {/* Tagline + stats */}
          <div>
            <motion.div
              initial={{ opacity: 0, y: 24 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.25, duration: 0.5 }}
            >
              <p className="text-4xl font-bold text-white leading-tight mb-4">
                Monitor your<br />
                <span className="text-blue-400">data center</span><br />
                in real time.
              </p>
              <p className="text-slate-400 text-base leading-relaxed max-w-xs">
                Full visibility into infrastructure health, energy usage, and network topology — all in one place.
              </p>
            </motion.div>

            <motion.div
              initial={{ opacity: 0, y: 16 }}
              animate={{ opacity: 1, y: 0 }}
              transition={{ delay: 0.45, duration: 0.5 }}
              className="mt-10 grid grid-cols-2 gap-3"
            >
              {[
                { label: 'Uptime', value: '99.99%' },
                { label: 'Avg response', value: '< 2ms' },
                { label: 'Active agents', value: '1,240+' },
                { label: 'Alerts resolved', value: '98.4%' },
              ].map((stat) => (
                <div key={stat.label} className="bg-white/5 backdrop-blur-sm rounded-xl p-4 border border-white/10">
                  <p className="text-2xl font-bold text-blue-400">{stat.value}</p>
                  <p className="text-sm text-slate-400 mt-1">{stat.label}</p>
                </div>
              ))}
            </motion.div>
          </div>

          <p className="text-slate-600 text-sm">© 2025 Faber Labs · FWDCIM Enterprise</p>
        </div>
      </div>

      {/* ── Right panel — form ── */}
      <div className="flex-1 flex items-center justify-center px-6 py-12 bg-slate-950">
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="w-full max-w-sm"
        >
          {/* Mobile logo */}
          <div className="flex items-center gap-2 mb-8 lg:hidden">
            <div className="w-8 h-8 rounded-lg bg-blue-600 flex items-center justify-center">
              <Server className="w-4 h-4 text-white" />
            </div>
            <span className="text-lg font-bold text-white">FWDCIM</span>
          </div>

          <h1 className="text-2xl font-bold text-white mb-1">Welcome back</h1>
          <p className="text-slate-400 text-sm mb-8">Sign in to your account to continue</p>

          <form onSubmit={handleSubmit} className="space-y-4">
            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Username</label>
              <div className="relative">
                <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  autoFocus
                  className="w-full bg-slate-900 border border-white/10 rounded-lg pl-10 pr-4 py-2.5 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 transition-colors"
                  placeholder="your username"
                />
              </div>
            </div>

            <div>
              <label className="block text-sm font-medium text-slate-300 mb-1.5">Password</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="w-full bg-slate-900 border border-white/10 rounded-lg pl-10 pr-4 py-2.5 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 transition-colors"
                  placeholder="••••••••"
                />
              </div>
            </div>

            {error && (
              <motion.p
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                className="text-sm text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2"
              >
                {error}
              </motion.p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full bg-blue-600 hover:bg-blue-500 disabled:opacity-60 disabled:cursor-not-allowed text-white font-medium rounded-lg py-2.5 text-sm transition-colors flex items-center justify-center gap-2 mt-2 shadow-lg shadow-blue-600/20"
            >
              {loading ? (
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              ) : 'Sign in'}
            </button>
          </form>

          <p className="mt-6 text-center text-sm text-slate-500">
            Don't have an account?{' '}
            <Link to="/register" className="text-blue-400 hover:text-blue-300 font-medium transition-colors">
              Create one
            </Link>
          </p>
        </motion.div>
      </div>
    </div>
  )
}