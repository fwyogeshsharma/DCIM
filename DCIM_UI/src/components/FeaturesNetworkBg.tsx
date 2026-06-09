import { useRef, useMemo } from 'react'
import { Canvas, useFrame } from '@react-three/fiber'
import * as THREE from 'three'

interface Node {
  pos: THREE.Vector3
  vel: THREE.Vector3
  phase: number
}

function Network() {
  const COUNT = 70
  const LINK_DIST = 4.5

  const nodes = useMemo<Node[]>(() =>
    Array.from({ length: COUNT }, () => ({
      pos: new THREE.Vector3(
        (Math.random() - 0.5) * 28,
        (Math.random() - 0.5) * 18,
        (Math.random() - 0.5) * 4,
      ),
      vel: new THREE.Vector3(
        (Math.random() - 0.5) * 0.006,
        (Math.random() - 0.5) * 0.006,
        0,
      ),
      phase: Math.random() * Math.PI * 2,
    })), [])

  const meshRefs = useRef<THREE.Mesh[]>([])
  const lineRef = useRef<THREE.LineSegments>(null!)
  const posAttr = useRef<THREE.BufferAttribute>(null!)
  const maxEdges = COUNT * COUNT
  const lineBuf = useMemo(() => new Float32Array(maxEdges * 6), [maxEdges])

  useFrame((_, dt) => {
    // Move nodes
    nodes.forEach((n, i) => {
      n.pos.addScaledVector(n.vel, 1)
      if (Math.abs(n.pos.x) > 14) n.vel.x *= -1
      if (Math.abs(n.pos.y) > 9) n.vel.y *= -1
      n.phase += dt * 0.9

      const mesh = meshRefs.current[i]
      if (mesh) {
        mesh.position.copy(n.pos)
        mesh.scale.setScalar(0.88 + 0.12 * Math.sin(n.phase))
      }
    })

    // Rebuild edges
    let ec = 0
    for (let i = 0; i < COUNT; i++) {
      for (let j = i + 1; j < COUNT; j++) {
        if (nodes[i].pos.distanceTo(nodes[j].pos) < LINK_DIST && ec < maxEdges) {
          const b = ec * 6
          lineBuf[b]   = nodes[i].pos.x; lineBuf[b+1] = nodes[i].pos.y; lineBuf[b+2] = nodes[i].pos.z
          lineBuf[b+3] = nodes[j].pos.x; lineBuf[b+4] = nodes[j].pos.y; lineBuf[b+5] = nodes[j].pos.z
          ec++
        }
      }
    }
    if (posAttr.current) {
      posAttr.current.array = lineBuf
      posAttr.current.count = ec * 2
      posAttr.current.needsUpdate = true
    }
    if (lineRef.current) lineRef.current.geometry.setDrawRange(0, ec * 2)
  })

  return (
    <>
      <lineSegments ref={lineRef}>
        <bufferGeometry>
          <bufferAttribute
            ref={posAttr}
            attach="attributes-position"
            array={lineBuf}
            count={0}
            itemSize={3}
          />
        </bufferGeometry>
        <lineBasicMaterial color="#1e40af" transparent opacity={0.18} />
      </lineSegments>

      {nodes.map((n, i) => (
        <mesh
          key={i}
          ref={(el) => { if (el) meshRefs.current[i] = el }}
          position={n.pos}
        >
          <sphereGeometry args={[i % 8 === 0 ? 0.13 : 0.06, 6, 6]} />
          <meshBasicMaterial
            color={i % 8 === 0 ? '#3b82f6' : i % 3 === 0 ? '#1d4ed8' : '#1e3a8a'}
            transparent
            opacity={i % 8 === 0 ? 0.55 : 0.35}
          />
        </mesh>
      ))}
    </>
  )
}

export default function FeaturesNetworkBg() {
  return (
    <Canvas
      camera={{ position: [0, 0, 16], fov: 60 }}
      style={{ position: 'absolute', inset: 0, pointerEvents: 'none' }}
      gl={{ antialias: false, alpha: true }}
    >
      <Network />
    </Canvas>
  )
}