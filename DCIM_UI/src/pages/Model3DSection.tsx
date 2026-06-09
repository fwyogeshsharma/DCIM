import { useEffect, useRef } from 'react';
import { motion } from 'framer-motion';
import { Canvas, useFrame, useLoader } from '@react-three/fiber';
import { GLTFLoader } from 'three/examples/jsm/loaders/GLTFLoader.js';
import * as THREE from 'three';

function Model3D() {
  const meshRef = useRef<THREE.Group>(null);
  const scrollRef = useRef(0);

  useEffect(() => {
    const handleScroll = () => {
      const scrollStart = window.innerHeight * 3;
      scrollRef.current = Math.max(0, window.scrollY - scrollStart);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  useFrame(() => {
    if (meshRef.current) {
      meshRef.current.rotation.y = (scrollRef.current / 500) * Math.PI * 2;
    }
  });

  const gltf = useLoader(GLTFLoader, '/sample_2026-02-09T130052.563.glb');

  return (
    <group ref={meshRef}>
      <primitive object={gltf.scene} scale={5} />
    </group>
  );
}

export default function Model3DSection() {
  return (
    <section className="relative h-screen bg-slate-950">
      <div className="absolute inset-0">
        <Canvas
          camera={{ position: [0, 0, 5], fov: 50 }}
          style={{ background: 'linear-gradient(to bottom, #0f172a, #1e293b, #334155)' }}
        >
          <ambientLight intensity={0.5} />
          <directionalLight position={[10, 10, 5]} intensity={1} />
          <directionalLight position={[-10, -10, -5]} intensity={0.5} />
          <Model3D />
        </Canvas>
      </div>
      <div className="relative z-10 h-full flex items-center justify-center px-6">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8 }}
          className="text-center max-w-3xl"
        >
          <h2 className="text-4xl md:text-5xl font-bold mb-6 bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">
            Infrastructure at Scale
          </h2>
          <p className="text-xl text-white">
            Monitor and manage thousands of devices with real-time 3D visualization
          </p>
        </motion.div>
      </div>
    </section>
  );
}