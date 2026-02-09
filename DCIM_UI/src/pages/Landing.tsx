import { useEffect, useRef, useState } from 'react';
import { motion, useScroll, useTransform } from 'framer-motion';
import {
  Activity,
  Server,
  Zap,
  Shield,
  BarChart3,
  Clock,
  ArrowRight,
  CheckCircle2
} from 'lucide-react';

export default function Landing() {
  return (
    <div className="min-h-screen bg-slate-950 text-white">
      <Navigation />
      <HeroSection />
      <FeaturesSection />
      <BenefitsSection />
      <CTASection />
      <Footer />
    </div>
  );
}

function Navigation() {
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  return (
    <nav
      className={`fixed top-0 left-0 right-0 z-50 transition-all duration-300 ${
        scrolled ? 'bg-slate-950/80 backdrop-blur-xl border-b border-white/10' : 'bg-transparent'
      }`}
    >
      <div className="max-w-7xl mx-auto px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Server className="w-8 h-8 text-blue-500" />
            <span className="text-xl font-bold">DCIM Enterprise</span>
          </div>

          <div className="hidden md:flex items-center gap-8">
            <a href="#features" className="text-slate-300 hover:text-white transition-colors cursor-pointer">Features</a>
            <a href="#benefits" className="text-slate-300 hover:text-white transition-colors cursor-pointer">Benefits</a>
            <a href="#pricing" className="text-slate-300 hover:text-white transition-colors cursor-pointer">Pricing</a>
          </div>

          <div className="flex items-center gap-4">
            <button className="text-slate-300 hover:text-white transition-colors cursor-pointer">
              Sign In
            </button>
            <button className="bg-blue-600 hover:bg-blue-700 px-6 py-2 rounded-lg transition-colors cursor-pointer">
              Get Started
            </button>
          </div>
        </div>
      </div>
    </nav>
  );
}

function HeroSection() {
  const containerRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const [currentFrame, setCurrentFrame] = useState(0);
  const frameCount = 240;

  // Preload images
  const imagesRef = useRef<HTMLImageElement[]>([]);
  const [imagesLoaded, setImagesLoaded] = useState(false);

  useEffect(() => {
    const images: HTMLImageElement[] = [];
    let loadedCount = 0;

    for (let i = 1; i <= frameCount; i++) {
      const img = new Image();
      img.src = `/ezgif-3631117e4f262e86-png-split/ezgif-frame-${i.toString().padStart(3, '0')}.png`;
      img.onload = () => {
        loadedCount++;
        if (loadedCount === frameCount) {
          setImagesLoaded(true);
        }
      };
      images.push(img);
    }

    imagesRef.current = images;
  }, []);

  useEffect(() => {
    if (!imagesLoaded || !canvasRef.current) return;

    const canvas = canvasRef.current;
    const context = canvas.getContext('2d');
    if (!context) return;

    const handleScroll = () => {
      if (!containerRef.current) return;

      const scrollTop = window.scrollY;
      const maxScroll = window.innerHeight * 2; // 2 viewport heights for full animation
      const scrollFraction = Math.min(scrollTop / maxScroll, 1);
      const frameIndex = Math.floor(scrollFraction * (frameCount - 1));

      setCurrentFrame(frameIndex);

      const img = imagesRef.current[frameIndex];
      if (img && img.complete) {
        // Set canvas size to match image aspect ratio
        const scale = Math.min(
          canvas.width / img.width,
          canvas.height / img.height
        );
        const x = (canvas.width / 2) - (img.width / 2) * scale;
        const y = (canvas.height / 2) - (img.height / 2) * scale;

        context.clearRect(0, 0, canvas.width, canvas.height);
        context.drawImage(img, x, y, img.width * scale, img.height * scale);
      }
    };

    const handleResize = () => {
      if (!canvasRef.current) return;
      const canvas = canvasRef.current;
      canvas.width = canvas.offsetWidth * window.devicePixelRatio;
      canvas.height = canvas.offsetHeight * window.devicePixelRatio;
      handleScroll(); // Redraw at current position
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
  }, [imagesLoaded]);

  return (
    <section ref={containerRef} className="relative h-[300vh]">
      <div className="sticky top-0 h-screen flex items-center justify-center overflow-hidden">
        {/* Canvas for frame animation */}
        <canvas
          ref={canvasRef}
          className="absolute inset-0 w-full h-full"
          style={{
            width: '100%',
            height: '100%',
            objectFit: 'contain'
          }}
        />

        {/* Overlay content */}
        <div className="relative z-10 max-w-7xl mx-auto px-6 text-center">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.8 }}
          >
            <h1 className="text-5xl md:text-7xl font-bold mb-6 bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">
              Data Center Infrastructure
              <br />
              Monitoring Made Simple
            </h1>
            <p className="text-xl md:text-2xl text-slate-300 mb-8 max-w-3xl mx-auto">
              Real-time monitoring, intelligent analytics, and predictive maintenance
              for enterprise data centers
            </p>

            <div className="flex items-center justify-center gap-4">
              <button className="bg-blue-600 hover:bg-blue-700 px-8 py-4 rounded-lg text-lg font-semibold transition-colors cursor-pointer flex items-center gap-2 group">
                Start Free Trial
                <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
              </button>
              <button className="border border-white/20 hover:border-white/40 px-8 py-4 rounded-lg text-lg font-semibold transition-colors cursor-pointer backdrop-blur-sm">
                Watch Demo
              </button>
            </div>
          </motion.div>
        </div>

        {/* Loading indicator */}
        {!imagesLoaded && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950">
            <div className="flex flex-col items-center gap-4">
              <div className="w-16 h-16 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
              <p className="text-slate-400">Loading experience...</p>
            </div>
          </div>
        )}

        {/* Scroll indicator */}
        <motion.div
          className="absolute bottom-8 left-1/2 -translate-x-1/2"
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ delay: 1, duration: 0.8 }}
        >
          <div className="flex flex-col items-center gap-2 text-slate-400">
            <span className="text-sm">Scroll to explore</span>
            <motion.div
              animate={{ y: [0, 8, 0] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            >
              <div className="w-6 h-10 border-2 border-slate-400 rounded-full flex items-start justify-center p-2">
                <div className="w-1.5 h-1.5 bg-slate-400 rounded-full" />
              </div>
            </motion.div>
          </div>
        </motion.div>
      </div>
    </section>
  );
}

function FeaturesSection() {
  const features = [
    {
      icon: Activity,
      title: 'Real-Time Monitoring',
      description: 'Monitor power, temperature, humidity, and network performance across all your infrastructure in real-time.',
      color: 'blue'
    },
    {
      icon: BarChart3,
      title: 'Advanced Analytics',
      description: 'AI-powered insights and predictive analytics to optimize resource utilization and prevent failures.',
      color: 'cyan'
    },
    {
      icon: Shield,
      title: 'Enterprise Security',
      description: 'TLS encryption, role-based access control, and comprehensive audit logging for compliance.',
      color: 'indigo'
    },
    {
      icon: Zap,
      title: 'Automated Alerts',
      description: 'Intelligent alerting system with customizable thresholds and multi-channel notifications.',
      color: 'violet'
    },
    {
      icon: Server,
      title: 'Agent-Based Architecture',
      description: 'Lightweight agents with secure communication and automatic failover capabilities.',
      color: 'purple'
    },
    {
      icon: Clock,
      title: 'Historical Trends',
      description: 'Long-term data retention with powerful visualization and reporting capabilities.',
      color: 'fuchsia'
    }
  ];

  return (
    <section id="features" className="py-32 px-6 bg-slate-900">
      <div className="max-w-7xl mx-auto">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="text-center mb-16"
        >
          <h2 className="text-4xl md:text-5xl font-bold mb-4">
            Comprehensive Infrastructure Visibility
          </h2>
          <p className="text-xl text-slate-400 max-w-3xl mx-auto">
            Everything you need to monitor, analyze, and optimize your data center infrastructure
          </p>
        </motion.div>

        <div className="grid md:grid-cols-2 lg:grid-cols-3 gap-8">
          {features.map((feature, index) => (
            <motion.div
              key={feature.title}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6, delay: index * 0.1 }}
              className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-8 hover:border-white/20 transition-colors cursor-pointer group"
            >
              <div className={`w-14 h-14 bg-${feature.color}-500/10 rounded-lg flex items-center justify-center mb-6 group-hover:bg-${feature.color}-500/20 transition-colors`}>
                <feature.icon className={`w-7 h-7 text-${feature.color}-500`} />
              </div>
              <h3 className="text-xl font-semibold mb-3">{feature.title}</h3>
              <p className="text-slate-400 leading-relaxed">{feature.description}</p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}

function BenefitsSection() {
  const benefits = [
    'Reduce downtime by up to 80% with predictive maintenance',
    'Cut energy costs by 30% through intelligent power optimization',
    'Improve capacity planning with accurate resource utilization data',
    'Ensure compliance with automated audit logging and reporting',
    'Scale seamlessly from single rack to multi-site deployments',
    'Integrate with existing tools via REST API and webhooks'
  ];

  return (
    <section id="benefits" className="py-32 px-6 bg-gradient-to-b from-slate-900 to-slate-950">
      <div className="max-w-7xl mx-auto">
        <div className="grid lg:grid-cols-2 gap-16 items-center">
          <motion.div
            initial={{ opacity: 0, x: -20 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6 }}
          >
            <h2 className="text-4xl md:text-5xl font-bold mb-6">
              Transform Your Data Center Operations
            </h2>
            <p className="text-xl text-slate-400 mb-8 leading-relaxed">
              DCIM Enterprise delivers measurable ROI through improved efficiency,
              reduced operational costs, and enhanced reliability.
            </p>

            <div className="space-y-4">
              {benefits.map((benefit, index) => (
                <motion.div
                  key={index}
                  initial={{ opacity: 0, x: -20 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{ duration: 0.6, delay: index * 0.1 }}
                  className="flex items-start gap-3"
                >
                  <CheckCircle2 className="w-6 h-6 text-blue-500 flex-shrink-0 mt-0.5" />
                  <span className="text-slate-300">{benefit}</span>
                </motion.div>
              ))}
            </div>
          </motion.div>

          <motion.div
            initial={{ opacity: 0, x: 20 }}
            whileInView={{ opacity: 1, x: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.6 }}
            className="bg-gradient-to-br from-blue-600/20 to-cyan-600/20 border border-blue-500/30 rounded-2xl p-12"
          >
            <div className="space-y-8">
              <div>
                <div className="text-5xl font-bold text-blue-400 mb-2">99.99%</div>
                <div className="text-slate-300">System Uptime</div>
              </div>
              <div>
                <div className="text-5xl font-bold text-cyan-400 mb-2">30%</div>
                <div className="text-slate-300">Energy Savings</div>
              </div>
              <div>
                <div className="text-5xl font-bold text-indigo-400 mb-2">10k+</div>
                <div className="text-slate-300">Monitored Devices</div>
              </div>
              <div>
                <div className="text-5xl font-bold text-violet-400 mb-2">&lt;1min</div>
                <div className="text-slate-300">Alert Response Time</div>
              </div>
            </div>
          </motion.div>
        </div>
      </div>
    </section>
  );
}

function CTASection() {
  return (
    <section className="py-32 px-6 bg-gradient-to-r from-blue-600 to-cyan-600">
      <div className="max-w-4xl mx-auto text-center">
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
        >
          <h2 className="text-4xl md:text-5xl font-bold mb-6">
            Ready to Optimize Your Data Center?
          </h2>
          <p className="text-xl mb-8 text-blue-50">
            Start your 30-day free trial. No credit card required.
          </p>

          <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
            <button className="bg-white text-blue-600 hover:bg-blue-50 px-8 py-4 rounded-lg text-lg font-semibold transition-colors cursor-pointer flex items-center gap-2 group">
              Get Started Free
              <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
            </button>
            <button className="border-2 border-white text-white hover:bg-white/10 px-8 py-4 rounded-lg text-lg font-semibold transition-colors cursor-pointer">
              Schedule Demo
            </button>
          </div>
        </motion.div>
      </div>
    </section>
  );
}

function Footer() {
  return (
    <footer className="bg-slate-950 border-t border-white/10 py-12 px-6">
      <div className="max-w-7xl mx-auto">
        <div className="grid md:grid-cols-4 gap-8 mb-8">
          <div>
            <div className="flex items-center gap-2 mb-4">
              <Server className="w-6 h-6 text-blue-500" />
              <span className="text-lg font-bold">DCIM Enterprise</span>
            </div>
            <p className="text-slate-400 text-sm">
              Enterprise-grade data center infrastructure monitoring and management.
            </p>
          </div>

          <div>
            <h4 className="font-semibold mb-4">Product</h4>
            <ul className="space-y-2 text-slate-400 text-sm">
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">Features</a></li>
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">Pricing</a></li>
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">Documentation</a></li>
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">API</a></li>
            </ul>
          </div>

          <div>
            <h4 className="font-semibold mb-4">Company</h4>
            <ul className="space-y-2 text-slate-400 text-sm">
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">About</a></li>
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">Blog</a></li>
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">Careers</a></li>
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">Contact</a></li>
            </ul>
          </div>

          <div>
            <h4 className="font-semibold mb-4">Legal</h4>
            <ul className="space-y-2 text-slate-400 text-sm">
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">Privacy</a></li>
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">Terms</a></li>
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">Security</a></li>
              <li><a href="#" className="hover:text-white transition-colors cursor-pointer">Compliance</a></li>
            </ul>
          </div>
        </div>

        <div className="border-t border-white/10 pt-8 text-center text-slate-400 text-sm">
          <p>&copy; 2025 DCIM Enterprise. All rights reserved.</p>
        </div>
      </div>
    </footer>
  );
}
