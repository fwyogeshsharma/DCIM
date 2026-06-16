import { useState, type FormEvent, useRef, useMemo } from 'react'
import { useNavigate, Link } from 'react-router-dom'
import { motion } from 'framer-motion'
import { Canvas, useFrame } from '@react-three/fiber'
import * as THREE from 'three'
import { Server, Lock, User, Mail } from 'lucide-react'
import { useAuthStore } from '../stores/useAuthStore'

// ─── Three.js network background ────────────────────────────────────────────

interface NodeData {
  position: THREE.Vector3
  velocity: THREE.Vector3
  phase: number
}

function NetworkGraph() {
  const nodeCount = 60
  const connectionDistance = 3.2

  const nodes = useMemo<NodeData[]>(() => {
    return Array.from({ length: nodeCount }, () => ({
      position: new THREE.Vector3(
        (Math.random() - 0.5) * 18,
        (Math.random() - 0.5) * 12,
        (Math.random() - 0.5) * 8,
      ),
      velocity: new THREE.Vector3(
        (Math.random() - 0.5) * 0.008,
        (Math.random() - 0.5) * 0.008,
        (Math.random() - 0.5) * 0.004,
      ),
      phase: Math.random() * Math.PI * 2,
    }))
  }, [])

  const nodeRefs = useRef<THREE.Mesh[]>([])
  const lineRef = useRef<THREE.LineSegments>(null!)
  const positionsAttr = useRef<THREE.BufferAttribute>(null!)

  // Pre-allocate max edge positions buffer (nodeCount * nodeCount / 2 edges max, 2 verts each, 3 floats)
  const maxEdges = nodeCount * nodeCount
  const linePositions = useMemo(() => new Float32Array(maxEdges * 6), [maxEdges])

  useFrame((_, delta) => {
    const t = delta
    nodes.forEach((node, i) => {
      node.position.addScaledVector(node.velocity, 1)
      // Bounce off soft bounds
      if (Math.abs(node.position.x) > 9) node.velocity.x *= -1
      if (Math.abs(node.position.y) > 6) node.velocity.y *= -1
      if (Math.abs(node.position.z) > 4) node.velocity.z *= -1
      node.phase += t * 1.2

      const mesh = nodeRefs.current[i]
      if (mesh) {
        mesh.position.copy(node.position)
        const scale = 0.85 + 0.15 * Math.sin(node.phase)
        mesh.scale.setScalar(scale)
      }
    })

    // Rebuild edge line positions
    let edgeCount = 0
    for (let i = 0; i < nodeCount; i++) {
      for (let j = i + 1; j < nodeCount; j++) {
        const dist = nodes[i].position.distanceTo(nodes[j].position)
        if (dist < connectionDistance && edgeCount < maxEdges) {
          const base = edgeCount * 6
          linePositions[base + 0] = nodes[i].position.x
          linePositions[base + 1] = nodes[i].position.y
          linePositions[base + 2] = nodes[i].position.z
          linePositions[base + 3] = nodes[j].position.x
          linePositions[base + 4] = nodes[j].position.y
          linePositions[base + 5] = nodes[j].position.z
          edgeCount++
        }
      }
    }

    if (positionsAttr.current) {
      positionsAttr.current.array = linePositions
      positionsAttr.current.count = edgeCount * 2
      positionsAttr.current.needsUpdate = true
    }
    if (lineRef.current) {
      lineRef.current.geometry.setDrawRange(0, edgeCount * 2)
    }
  })

  return (
    <>
      {/* Edge lines */}
      <lineSegments ref={lineRef}>
        <bufferGeometry>
          <bufferAttribute
            ref={positionsAttr}
            attach="attributes-position"
            array={linePositions}
            count={0}
            itemSize={3}
          />
        </bufferGeometry>
        <lineBasicMaterial color="#1d4ed8" transparent opacity={0.25} />
      </lineSegments>

      {/* Nodes */}
      {nodes.map((node, i) => (
        <mesh
          key={i}
          ref={(el) => { if (el) nodeRefs.current[i] = el }}
          position={node.position}
        >
          <sphereGeometry args={[i % 7 === 0 ? 0.14 : 0.07, 8, 8]} />
          <meshBasicMaterial
            color={i % 7 === 0 ? '#60a5fa' : i % 3 === 0 ? '#38bdf8' : '#1e40af'}
            transparent
            opacity={i % 7 === 0 ? 0.9 : 0.6}
          />
        </mesh>
      ))}
    </>
  )
}

function NetworkCanvas() {
  return (
    <Canvas
      camera={{ position: [0, 0, 14], fov: 55 }}
      style={{ position: 'absolute', inset: 0 }}
      gl={{ antialias: false, alpha: true }}
    >
      <NetworkGraph />
    </Canvas>
  )
}

// ─── Sign Up form ────────────────────────────────────────────────────────────

export default function SignUp() {
  const register = useAuthStore((s) => s.register)
  const navigate = useNavigate()

  const [username, setUsername] = useState('')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [confirm, setConfirm] = useState('')
  const [error, setError] = useState('')
  const [loading, setLoading] = useState(false)

  async function handleSubmit(e: FormEvent) {
    e.preventDefault()
    setError('')

    if (password !== confirm) { setError('Passwords do not match'); return }
    if (password.length < 6) { setError('Password must be at least 6 characters'); return }

    setLoading(true)
    await new Promise((r) => setTimeout(r, 400))
    const result = register(username, email, password)
    setLoading(false)

    if (result.success) {
      navigate('/app/dashboard', { replace: true })
    } else {
      setError(result.error ?? 'Registration failed')
    }
  }

  return (
    <div className="min-h-screen bg-slate-950 flex items-center justify-center px-4 relative overflow-hidden">

      {/* Three.js animated network */}
      <div className="absolute inset-0 pointer-events-none">
        <NetworkCanvas />
      </div>

      {/* Radial vignette so form stays readable */}
      <div className="absolute inset-0 bg-radial-[at_50%_50%] from-transparent via-slate-950/60 to-slate-950/95 pointer-events-none" />

      {/* Form card */}
      <motion.div
        initial={{ opacity: 0, y: 28 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.45 }}
        className="relative z-10 w-full max-w-sm"
      >
        <div className="bg-slate-900/80 backdrop-blur-xl border border-white/10 rounded-2xl p-8 shadow-2xl shadow-black/50">
          {/* Logo */}
          <Link to="/" className="flex items-center gap-2.5 mb-7 cursor-pointer hover:opacity-80 transition-opacity w-fit">
            <div className="w-9 h-9 rounded-lg bg-blue-600 flex items-center justify-center shadow-lg shadow-blue-600/30">
              <Server className="w-5 h-5 text-white" />
            </div>
            <span className="text-lg font-bold text-white">FWDCIM Enterprise</span>
          </Link>

          <h1 className="text-xl font-bold text-white mb-1">Create your account</h1>
          <p className="text-slate-400 text-sm mb-6">Join the DCIM monitoring platform</p>

          <form onSubmit={handleSubmit} className="space-y-3.5">
            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wide">Username</label>
              <div className="relative">
                <User className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  required
                  autoFocus
                  className="w-full bg-slate-800/60 border border-white/10 rounded-lg pl-9 pr-3 py-2.5 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 transition-colors"
                  placeholder="choose a username"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wide">Email</label>
              <div className="relative">
                <Mail className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="email"
                  value={email}
                  onChange={(e) => setEmail(e.target.value)}
                  required
                  className="w-full bg-slate-800/60 border border-white/10 rounded-lg pl-9 pr-3 py-2.5 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 transition-colors"
                  placeholder="you@example.com"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wide">Password</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  required
                  className="w-full bg-slate-800/60 border border-white/10 rounded-lg pl-9 pr-3 py-2.5 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 transition-colors"
                  placeholder="min. 6 characters"
                />
              </div>
            </div>

            <div>
              <label className="block text-xs font-medium text-slate-400 mb-1.5 uppercase tracking-wide">Confirm password</label>
              <div className="relative">
                <Lock className="absolute left-3 top-1/2 -translate-y-1/2 w-4 h-4 text-slate-500" />
                <input
                  type="password"
                  value={confirm}
                  onChange={(e) => setConfirm(e.target.value)}
                  required
                  className="w-full bg-slate-800/60 border border-white/10 rounded-lg pl-9 pr-3 py-2.5 text-sm text-white placeholder:text-slate-600 focus:outline-none focus:ring-1 focus:ring-blue-500 focus:border-blue-500 transition-colors"
                  placeholder="••••••••"
                />
              </div>
            </div>

            {error && (
              <motion.p
                initial={{ opacity: 0, y: -4 }}
                animate={{ opacity: 1, y: 0 }}
                className="text-xs text-red-400 bg-red-500/10 border border-red-500/20 rounded-lg px-3 py-2"
              >
                {error}
              </motion.p>
            )}

            <button
              type="submit"
              disabled={loading}
              className="w-full mt-1 bg-blue-600 hover:bg-blue-500 disabled:opacity-60 disabled:cursor-not-allowed text-white font-medium rounded-lg py-2.5 text-sm transition-colors flex items-center justify-center gap-2 shadow-lg shadow-blue-600/20"
            >
              {loading ? (
                <span className="w-4 h-4 border-2 border-white/30 border-t-white rounded-full animate-spin" />
              ) : 'Create account'}
            </button>
          </form>

          <p className="mt-5 text-center text-sm text-slate-500">
            Already have an account?{' '}
            <Link to="/login" className="text-blue-400 hover:text-blue-300 font-medium transition-colors">
              Sign in
            </Link>
          </p>
        </div>
      </motion.div>
    </div>
  )
}