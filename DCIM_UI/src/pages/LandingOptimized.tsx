import { useEffect, useRef, useState, useCallback } from 'react';
import { motion, useScroll, useTransform } from 'framer-motion';
import { useNavigate } from 'react-router-dom';
import {
  Activity,
  Server,
  Zap,
  Shield,
  BarChart3,
  Clock,
  ArrowRight,
  CheckCircle2,
  Menu,
  X
} from 'lucide-react';

export default function LandingOptimized() {
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
  const [mobileMenuOpen, setMobileMenuOpen] = useState(false);
  const navigate = useNavigate();

  useEffect(() => {
    const handleScroll = () => {
      setScrolled(window.scrollY > 20);
    };
    window.addEventListener('scroll', handleScroll);
    return () => window.removeEventListener('scroll', handleScroll);
  }, []);

  const scrollToSection = (id: string) => {
    const element = document.getElementById(id);
    if (element) {
      element.scrollIntoView({ behavior: 'smooth', block: 'start' });
      setMobileMenuOpen(false);
    }
  };

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

          {/* Desktop Menu */}
          <div className="hidden md:flex items-center gap-8">
            <button onClick={() => scrollToSection('features')} className="text-slate-300 hover:text-white transition-colors cursor-pointer">Features</button>
            <button onClick={() => scrollToSection('benefits')} className="text-slate-300 hover:text-white transition-colors cursor-pointer">Benefits</button>
            <button onClick={() => scrollToSection('pricing')} className="text-slate-300 hover:text-white transition-colors cursor-pointer">Pricing</button>
          </div>

          <div className="hidden md:flex items-center gap-4">
            <button onClick={() => navigate('/app/dashboard')} className="text-slate-300 hover:text-white transition-colors cursor-pointer">
              Sign In
            </button>
            <button onClick={() => navigate('/app/dashboard')} className="bg-blue-600 hover:bg-blue-700 px-6 py-2 rounded-lg transition-colors cursor-pointer">
              Get Started
            </button>
          </div>

          {/* Mobile Menu Button */}
          <button
            onClick={() => setMobileMenuOpen(!mobileMenuOpen)}
            className="md:hidden text-white cursor-pointer"
          >
            {mobileMenuOpen ? <X className="w-6 h-6" /> : <Menu className="w-6 h-6" />}
          </button>
        </div>

        {/* Mobile Menu */}
        {mobileMenuOpen && (
          <motion.div
            initial={{ opacity: 0, y: -20 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: -20 }}
            className="md:hidden mt-4 pb-4 border-t border-white/10 pt-4"
          >
            <div className="flex flex-col gap-4">
              <button onClick={() => scrollToSection('features')} className="text-slate-300 hover:text-white transition-colors cursor-pointer text-left">Features</button>
              <button onClick={() => scrollToSection('benefits')} className="text-slate-300 hover:text-white transition-colors cursor-pointer text-left">Benefits</button>
              <button onClick={() => scrollToSection('pricing')} className="text-slate-300 hover:text-white transition-colors cursor-pointer text-left">Pricing</button>
              <div className="h-px bg-white/10 my-2" />
              <button onClick={() => navigate('/app/dashboard')} className="text-slate-300 hover:text-white transition-colors cursor-pointer text-left">Sign In</button>
              <button onClick={() => navigate('/app/dashboard')} className="bg-blue-600 hover:bg-blue-700 px-6 py-2 rounded-lg transition-colors cursor-pointer">
                Get Started
              </button>
            </div>
          </motion.div>
        )}
      </div>
    </nav>
  );
}

function HeroSection() {
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
    const images: HTMLImageElement[] = [];
    let loadedCount = 0;

    for (let i = 1; i <= frameCount; i++) {
      const img = new Image();
      img.src = `/ezgif-3631117e4f262e86-png-split/ezgif-frame-${i.toString().padStart(3, '0')}.png`;

      img.onload = () => {
        loadedCount++;
        setLoadProgress(Math.round((loadedCount / frameCount) * 100));
        if (loadedCount === frameCount) {
          setImagesLoaded(true);
        }
      };

      img.onerror = () => {
        loadedCount++;
        setLoadProgress(Math.round((loadedCount / frameCount) * 100));
        if (loadedCount === frameCount) {
          setImagesLoaded(true);
        }
      };

      images.push(img);
    }

    imagesRef.current = images;
  }, []);

  const renderFrame = useCallback((frameIndex: number) => {
    if (!canvasRef.current || !imagesRef.current[frameIndex]) return;

    const canvas = canvasRef.current;
    const context = canvas.getContext('2d');
    if (!context) return;

    const img = imagesRef.current[frameIndex];
    if (!img.complete) return;

    // Clear canvas
    context.clearRect(0, 0, canvas.width, canvas.height);

    // Calculate scale to fit image in canvas while maintaining aspect ratio
    const scale = Math.min(
      canvas.width / img.width,
      canvas.height / img.height
    );

    // Center the image
    const x = (canvas.width - img.width * scale) / 2;
    const y = (canvas.height - img.height * scale) / 2;

    context.drawImage(img, x, y, img.width * scale, img.height * scale);
  }, []);

  useEffect(() => {
    if (!imagesLoaded || !canvasRef.current) return;

    const canvas = canvasRef.current;

    const handleScroll = () => {
      if (!containerRef.current) return;

      const scrollTop = window.scrollY;
      const maxScroll = window.innerHeight * 2; // 2 viewport heights for full animation
      const scrollFraction = Math.min(scrollTop / maxScroll, 1);
      const frameIndex = Math.min(
        Math.floor(scrollFraction * (frameCount - 1)),
        frameCount - 1
      );

      setCurrentFrame(frameIndex);
      renderFrame(frameIndex);
    };

    const handleResize = () => {
      if (!canvasRef.current) return;
      const canvas = canvasRef.current;
      const rect = canvas.getBoundingClientRect();
      canvas.width = rect.width * window.devicePixelRatio;
      canvas.height = rect.height * window.devicePixelRatio;
      renderFrame(currentFrame);
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
  }, [imagesLoaded, currentFrame, renderFrame]);

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

        {/* Gradient overlay for better text readability */}
        <div className="absolute inset-0 bg-gradient-to-b from-slate-950/60 via-transparent to-slate-950/60 pointer-events-none" />

        {/* Overlay content */}
        <div className="relative z-10 max-w-7xl mx-auto px-6 text-center">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: imagesLoaded ? 1 : 0, y: imagesLoaded ? 0 : 20 }}
            transition={{ duration: 0.8 }}
          >
            <h1 className="text-4xl md:text-6xl lg:text-7xl font-bold mb-6 bg-gradient-to-r from-blue-400 to-cyan-400 bg-clip-text text-transparent">
              Data Center Infrastructure
              <br />
              Monitoring Made Simple
            </h1>
            <p className="text-lg md:text-xl lg:text-2xl text-slate-200 mb-8 max-w-3xl mx-auto drop-shadow-lg">
              Real-time monitoring, intelligent analytics, and predictive maintenance
              for enterprise data centers
            </p>

            <div className="flex flex-col sm:flex-row items-center justify-center gap-4">
              <button
                onClick={() => navigate('/app/dashboard')}
                className="bg-blue-600 hover:bg-blue-700 px-8 py-4 rounded-lg text-lg font-semibold transition-colors cursor-pointer flex items-center gap-2 group w-full sm:w-auto justify-center"
              >
                Start Free Trial
                <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
              </button>
              <button className="border border-white/30 hover:border-white/50 hover:bg-white/5 px-8 py-4 rounded-lg text-lg font-semibold transition-colors cursor-pointer backdrop-blur-sm w-full sm:w-auto">
                Watch Demo
              </button>
            </div>

          </motion.div>
        </div>

        {/* Loading indicator */}
        {!imagesLoaded && (
          <div className="absolute inset-0 flex items-center justify-center bg-slate-950 z-20">
            <div className="flex flex-col items-center gap-4 max-w-md w-full px-6">
              <div className="w-16 h-16 border-4 border-blue-500/30 border-t-blue-500 rounded-full animate-spin" />
              <p className="text-slate-400 text-lg">Loading experience...</p>

              {/* Progress bar */}
              <div className="w-full bg-slate-800 rounded-full h-2 overflow-hidden">
                <motion.div
                  className="h-full bg-gradient-to-r from-blue-500 to-cyan-500"
                  initial={{ width: '0%' }}
                  animate={{ width: `${loadProgress}%` }}
                  transition={{ duration: 0.3 }}
                />
              </div>
              <p className="text-slate-500 text-sm">{loadProgress}%</p>
            </div>
          </div>
        )}

        {/* Scroll indicator */}
        <motion.div
          className="absolute bottom-8 left-1/2 -translate-x-1/2"
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: imagesLoaded ? 1 : 0, y: imagesLoaded ? 0 : -10 }}
          transition={{ delay: 1, duration: 0.8 }}
        >
          <div className="flex flex-col items-center gap-2 text-slate-300">
            <span className="text-sm">Scroll to explore</span>
            <motion.div
              animate={{ y: [0, 8, 0] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            >
              <div className="w-6 h-10 border-2 border-slate-300 rounded-full flex items-start justify-center p-2">
                <div className="w-1.5 h-1.5 bg-slate-300 rounded-full" />
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

  const colorMap: Record<string, string> = {
    blue: 'bg-blue-500/10 text-blue-500 group-hover:bg-blue-500/20',
    cyan: 'bg-cyan-500/10 text-cyan-500 group-hover:bg-cyan-500/20',
    indigo: 'bg-indigo-500/10 text-indigo-500 group-hover:bg-indigo-500/20',
    violet: 'bg-violet-500/10 text-violet-500 group-hover:bg-violet-500/20',
    purple: 'bg-purple-500/10 text-purple-500 group-hover:bg-purple-500/20',
    fuchsia: 'bg-fuchsia-500/10 text-fuchsia-500 group-hover:bg-fuchsia-500/20',
  };

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
              className="bg-slate-800/50 backdrop-blur-sm border border-white/10 rounded-xl p-8 hover:border-white/20 transition-all duration-300 cursor-pointer group"
            >
              <div className={`w-14 h-14 rounded-lg flex items-center justify-center mb-6 transition-all duration-300 ${colorMap[feature.color]}`}>
                <feature.icon className="w-7 h-7" />
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
            className="bg-gradient-to-br from-blue-600/20 to-cyan-600/20 border border-blue-500/30 rounded-2xl p-8 md:p-12"
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
  const navigate = useNavigate();

  return (
    <section id="pricing" className="py-32 px-6 bg-gradient-to-r from-blue-600 to-cyan-600">
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
            <button
              onClick={() => navigate('/app/dashboard')}
              className="bg-white text-blue-600 hover:bg-blue-50 px-8 py-4 rounded-lg text-lg font-semibold transition-colors cursor-pointer flex items-center gap-2 group w-full sm:w-auto justify-center"
            >
              Get Started Free
              <ArrowRight className="w-5 h-5 group-hover:translate-x-1 transition-transform" />
            </button>
            <button className="border-2 border-white text-white hover:bg-white/10 px-8 py-4 rounded-lg text-lg font-semibold transition-colors cursor-pointer w-full sm:w-auto">
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
              <li><a href="#features" className="hover:text-white transition-colors cursor-pointer">Features</a></li>
              <li><a href="#pricing" className="hover:text-white transition-colors cursor-pointer">Pricing</a></li>
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
