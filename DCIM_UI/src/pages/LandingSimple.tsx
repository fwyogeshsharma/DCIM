import { useEffect, useRef, useState } from 'react';
import { Server, ArrowRight } from 'lucide-react';
import { useNavigate } from 'react-router-dom';

/**
 * Simplified Landing Page with just the hero scroll animation
 * Use this if you want to test the frame animation in isolation
 */
export default function LandingSimple() {
  const navigate = useNavigate();

  return (
    <div className="min-h-screen bg-slate-950 text-white">
      {/* Simple Navigation */}
      <nav className="fixed top-0 left-0 right-0 z-50 bg-slate-950/80 backdrop-blur-xl border-b border-white/10">
        <div className="max-w-7xl mx-auto px-6 py-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Server className="w-8 h-8 text-blue-500" />
            <span className="text-xl font-bold">DCIM Enterprise</span>
          </div>
          <button
            onClick={() => navigate('/app/dashboard')}
            className="bg-blue-600 hover:bg-blue-700 px-6 py-2 rounded-lg transition-colors cursor-pointer"
          >
            Get Started
          </button>
        </div>
      </nav>

      {/* Hero with Frame Animation */}
      <ScrollFrameHero />

      {/* Simple Footer */}
      <footer className="py-8 px-6 text-center text-slate-400 text-sm border-t border-white/10">
        <p>&copy; 2025 DCIM Enterprise. All rights reserved.</p>
      </footer>
    </div>
  );
}

function ScrollFrameHero() {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [currentFrame, setCurrentFrame] = useState(0);
  const [loadProgress, setLoadProgress] = useState(0);
  const frameCount = 240;
  const navigate = useNavigate();

  // Preload images
  const imagesRef = useRef<HTMLImageElement[]>([]);
  const [imagesLoaded, setImagesLoaded] = useState(false);

  useEffect(() => {
    console.log('🎬 Starting frame preload...');
    const images: HTMLImageElement[] = [];
    let loadedCount = 0;

    for (let i = 1; i <= frameCount; i++) {
      const img = new Image();
      img.src = `/ezgif-3631117e4f262e86-png-split/ezgif-frame-${i.toString().padStart(3, '0')}.png`;

      img.onload = () => {
        loadedCount++;
        const progress = Math.round((loadedCount / frameCount) * 100);
        setLoadProgress(progress);

        if (loadedCount === 1) {
          console.log('✅ First frame loaded');
        }
        if (loadedCount === frameCount) {
          console.log(`🎉 All ${frameCount} frames loaded!`);
          setImagesLoaded(true);
        }
      };

      img.onerror = (e) => {
        console.error(`❌ Failed to load frame ${i}:`, e);
        loadedCount++;
        setLoadProgress(Math.round((loadedCount / frameCount) * 100));
      };

      images.push(img);
    }

    imagesRef.current = images;
  }, []);

  // Handle scroll and canvas updates
  useEffect(() => {
    if (!imagesLoaded || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const context = canvas.getContext('2d');
    if (!context) {
      console.error('❌ Canvas context not available');
      return;
    }

    console.log('🎨 Canvas ready, setting up scroll listener');

    const handleScroll = () => {
      if (!containerRef.current) return;

      const scrollTop = window.scrollY;
      const maxScroll = window.innerHeight * 2; // 2 viewport heights
      const scrollFraction = Math.min(scrollTop / maxScroll, 1);
      const frameIndex = Math.min(
        Math.floor(scrollFraction * (frameCount - 1)),
        frameCount - 1
      );

      if (frameIndex !== currentFrame) {
        setCurrentFrame(frameIndex);

        const img = imagesRef.current[frameIndex];
        if (img && img.complete) {
          // Clear canvas
          context.clearRect(0, 0, canvas.width, canvas.height);

          // Calculate scale to fit image
          const scale = Math.min(
            canvas.width / img.width,
            canvas.height / img.height
          );

          // Center image
          const x = (canvas.width - img.width * scale) / 2;
          const y = (canvas.height - img.height * scale) / 2;

          // Draw frame
          context.drawImage(img, x, y, img.width * scale, img.height * scale);
        }
      }
    };

    const handleResize = () => {
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * window.devicePixelRatio;
      canvas.height = rect.height * window.devicePixelRatio;
      handleScroll(); // Redraw current frame
    };

    handleResize();
    window.addEventListener('scroll', handleScroll, { passive: true });
    window.addEventListener('resize', handleResize);

    // Draw first frame
    handleScroll();

    return () => {
      window.removeEventListener('scroll', handleScroll);
      window.removeEventListener('resize', handleResize);
    };
  }, [imagesLoaded, currentFrame]);

  return (
    <section ref={containerRef} className="relative h-[300vh]">
      <div className="sticky top-0 h-screen flex items-center justify-center overflow-hidden bg-slate-950">
        {/* Canvas */}
        <canvas
          ref={canvasRef}
          className="absolute inset-0 w-full h-full"
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'contain'
          }}
        />

        {/* Gradient overlay */}
        <div className="absolute inset-0 bg-gradient-to-b from-slate-950/70 via-transparent to-slate-950/70 pointer-events-none" />

        {/* Content overlay */}
        <div className="relative z-10 max-w-4xl mx-auto px-6 text-center">
          <h1 className="text-5xl md:text-7xl font-bold mb-6 bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">
            Data Center Infrastructure
            <br />
            Monitoring Made Simple
          </h1>
          <p className="text-xl md:text-2xl text-slate-200 mb-8">
            Real-time monitoring, intelligent analytics, and predictive maintenance
          </p>

          <button
            onClick={() => navigate('/app/dashboard')}
            className="bg-blue-600 hover:bg-blue-700 px-8 py-4 rounded-lg text-lg font-semibold transition-colors cursor-pointer flex items-center gap-2 group mx-auto"
          >
            Start Free Trial
            <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
          </button>

        </div>

        {/* Loading state */}
        {!imagesLoaded && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950 z-20">
            <div className="flex flex-col items-center gap-4 max-w-md w-full px-6">
              {/* Spinner */}
              <div className="w-16 h-16 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />

              {/* Progress text */}
              <p className="text-slate-400 text-lg">Loading frames...</p>

              {/* Progress bar */}
              <div className="w-full bg-slate-800 rounded-full h-3 overflow-hidden">
                <div
                  className="h-full bg-gradient-to-r from-blue-500 to-cyan-500 transition-all duration-300"
                  style={{ width: `${loadProgress}%` }}
                />
              </div>

              {/* Progress percentage */}
              <p className="text-slate-500 text-sm font-mono">
                {loadProgress}% ({Math.round((loadProgress / 100) * frameCount)} / {frameCount} frames)
              </p>
            </div>
          </div>
        )}

        {/* Scroll indicator */}
        {imagesLoaded && currentFrame < 10 && (
          <div className="absolute bottom-8 left-1/2 -translate-x-1/2 animate-bounce">
            <div className="flex flex-col items-center gap-2 text-slate-300">
              <span className="text-sm">Scroll to animate</span>
              <div className="w-6 h-10 border-2 border-slate-300 rounded-full flex items-start justify-center p-2">
                <div className="w-1.5 h-1.5 bg-slate-300 rounded-full animate-pulse" />
              </div>
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
