import pptxgen from "pptxgenjs";

const pres = new pptxgen();

// Professional color palette - Teal Trust (Tech/Professional)
const colors = {
  primary: "028090",      // Teal
  secondary: "00A896",    // Seafoam
  accent: "02C39A",       // Mint
  dark: "1E2761",         // Navy
  white: "FFFFFF",
  lightGray: "F5F5F5",
  textDark: "2C3E50",
  textLight: "FFFFFF"
};

// Slide 1: Title Slide
const slide1 = pres.addSlide();
slide1.background = { color: colors.dark };

slide1.addText("DCIM Platform", {
  x: 0.5, y: 2.0, w: 9, h: 1.2,
  fontSize: 54, bold: true, color: colors.white,
  align: "center", fontFace: "Arial"
});

slide1.addText("Enterprise Data Center Infrastructure Monitoring", {
  x: 0.5, y: 3.3, w: 9, h: 0.6,
  fontSize: 24, color: colors.accent,
  align: "center", fontFace: "Arial"
});

slide1.addText("Comprehensive • Secure • Scalable", {
  x: 0.5, y: 4.2, w: 9, h: 0.5,
  fontSize: 18, color: colors.white, italic: true,
  align: "center", fontFace: "Arial"
});

// Decorative accent bar
slide1.addShape(pres.ShapeType.rect, {
  x: 3.5, y: 5.0, w: 3, h: 0.08,
  fill: { color: colors.primary }
});

// Slide 2: The Challenge
const slide2 = pres.addSlide();
slide2.background = { color: colors.lightGray };

slide2.addText("The Data Center Challenge", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontSize: 36, bold: true, color: colors.dark,
  fontFace: "Arial"
});

const challenges = [
  { title: "Limited Visibility", desc: "Lack of real-time insights into infrastructure health" },
  { title: "Reactive Management", desc: "Problems discovered after service impact" },
  { title: "Security Concerns", desc: "Unsecured monitoring tools and data transmission" },
  { title: "Fragmented Tools", desc: "Multiple incompatible monitoring solutions" }
];

let yPos = 1.8;
challenges.forEach((item, idx) => {
  const xPos = (idx % 2) * 4.8 + 0.5;
  const row = Math.floor(idx / 2);
  yPos = 1.8 + row * 1.6;

  // Card background
  slide2.addShape(pres.ShapeType.roundRect, {
    x: xPos, y: yPos, w: 4.2, h: 1.3,
    fill: { color: colors.white },
    line: { color: colors.primary, width: 2 }
  });

  slide2.addText(item.title, {
    x: xPos + 0.2, y: yPos + 0.15, w: 3.8, h: 0.4,
    fontSize: 16, bold: true, color: colors.dark,
    fontFace: "Arial"
  });

  slide2.addText(item.desc, {
    x: xPos + 0.2, y: yPos + 0.65, w: 3.8, h: 0.5,
    fontSize: 12, color: colors.textDark,
    fontFace: "Arial"
  });
});

// Slide 3: Our Solution
const slide3 = pres.addSlide();
slide3.background = { color: colors.white };

slide3.addText("Our Solution: DCIM Platform", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontSize: 36, bold: true, color: colors.dark,
  fontFace: "Arial"
});

// Two-column layout
slide3.addText("DCIM Server", {
  x: 0.5, y: 1.5, w: 4.2, h: 0.5,
  fontSize: 24, bold: true, color: colors.primary,
  fontFace: "Arial"
});

slide3.addText("Central monitoring hub with enterprise-grade security and multi-database support", {
  x: 0.5, y: 2.1, w: 4.2, h: 0.6,
  fontSize: 14, color: colors.textDark,
  fontFace: "Arial"
});

const serverFeatures = [
  "mTLS Authentication",
  "Real-time SSE Streaming",
  "Multi-DB Support",
  "RESTful API"
];

serverFeatures.forEach((feat, idx) => {
  slide3.addText("✓ " + feat, {
    x: 0.7, y: 2.9 + idx * 0.4, w: 3.8, h: 0.3,
    fontSize: 12, color: colors.textDark,
    fontFace: "Arial"
  });
});

slide3.addText("DCIM Agent", {
  x: 5.3, y: 1.5, w: 4.2, h: 0.5,
  fontSize: 24, bold: true, color: colors.secondary,
  fontFace: "Arial"
});

slide3.addText("Lightweight monitoring agent with comprehensive hardware and network insights", {
  x: 5.3, y: 2.1, w: 4.2, h: 0.6,
  fontSize: 14, color: colors.textDark,
  fontFace: "Arial"
});

const agentFeatures = [
  "30+ System Metrics",
  "SNMP Monitoring",
  "Anomaly Detection",
  "Root Cause Analysis"
];

agentFeatures.forEach((feat, idx) => {
  slide3.addText("✓ " + feat, {
    x: 5.5, y: 2.9 + idx * 0.4, w: 3.8, h: 0.3,
    fontSize: 12, color: colors.textDark,
    fontFace: "Arial"
  });
});

// Architecture diagram (simplified)
slide3.addShape(pres.ShapeType.roundRect, {
  x: 1.0, y: 4.8, w: 3.0, h: 0.8,
  fill: { color: colors.secondary },
  line: { color: colors.secondary, width: 1 }
});
slide3.addText("Agent", {
  x: 1.0, y: 4.8, w: 3.0, h: 0.8,
  fontSize: 18, bold: true, color: colors.white, align: "center", valign: "middle",
  fontFace: "Arial"
});

slide3.addShape(pres.ShapeType.line, {
  x: 4.0, y: 5.2, w: 2.0, h: 0,
  line: { color: colors.primary, width: 3, endArrowType: "triangle" }
});

slide3.addShape(pres.ShapeType.roundRect, {
  x: 6.0, y: 4.8, w: 3.0, h: 0.8,
  fill: { color: colors.primary },
  line: { color: colors.primary, width: 1 }
});
slide3.addText("Server", {
  x: 6.0, y: 4.8, w: 3.0, h: 0.8,
  fontSize: 18, bold: true, color: colors.white, align: "center", valign: "middle",
  fontFace: "Arial"
});

// Slide 4: Tech Stack
const slide4 = pres.addSlide();
slide4.background = { color: colors.lightGray };

slide4.addText("Enterprise-Grade Technology Stack", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontSize: 36, bold: true, color: colors.dark,
  fontFace: "Arial"
});

const techStack = [
  { category: "Language", tech: "Go 1.21+", benefit: "High performance & concurrency" },
  { category: "Security", tech: "mTLS + X.509", benefit: "Enterprise-grade encryption" },
  { category: "Database", tech: "SQLite / PostgreSQL / MySQL", benefit: "Flexible deployment options" },
  { category: "Protocols", tech: "HTTPS + SNMP v1/v2c/v3", benefit: "Industry standard support" },
  { category: "Real-time", tech: "Server-Sent Events (SSE)", benefit: "Live data streaming" },
  { category: "Platform", tech: "Windows / Linux / macOS", benefit: "Cross-platform compatibility" }
];

techStack.forEach((item, idx) => {
  const row = Math.floor(idx / 2);
  const col = idx % 2;
  const xPos = 0.5 + col * 4.8;
  const yPos = 1.5 + row * 1.3;

  slide4.addShape(pres.ShapeType.roundRect, {
    x: xPos, y: yPos, w: 4.3, h: 1.0,
    fill: { color: colors.white },
    line: { color: colors.primary, width: 1 }
  });

  slide4.addText(item.category, {
    x: xPos + 0.2, y: yPos + 0.1, w: 4.0, h: 0.3,
    fontSize: 14, bold: true, color: colors.primary,
    fontFace: "Arial"
  });

  slide4.addText(item.tech, {
    x: xPos + 0.2, y: yPos + 0.4, w: 4.0, h: 0.25,
    fontSize: 13, color: colors.textDark, bold: true,
    fontFace: "Arial"
  });

  slide4.addText(item.benefit, {
    x: xPos + 0.2, y: yPos + 0.68, w: 4.0, h: 0.2,
    fontSize: 10, color: colors.textDark, italic: true,
    fontFace: "Arial"
  });
});

// Slide 5: DCIM Server Features
const slide5 = pres.addSlide();
slide5.background = { color: colors.white };

slide5.addText("DCIM Server: Central Management Hub", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontSize: 36, bold: true, color: colors.dark,
  fontFace: "Arial"
});

const serverCapabilities = [
  { feature: "mTLS Authentication", detail: "Mutual TLS with client certificate verification" },
  { feature: "Multi-Agent Management", detail: "Centralized monitoring for unlimited agents" },
  { feature: "Real-time Streaming", detail: "Server-Sent Events for live updates" },
  { feature: "Multi-Database Support", detail: "SQLite, PostgreSQL, MySQL compatibility" },
  { feature: "Alert Processing", detail: "Severity-based alerting with retry logic" },
  { feature: "Data Aggregation", detail: "1m, 5m, 1h, 24h interval aggregation" },
  { feature: "License Management", detail: "Agent limits with grace period enforcement" },
  { feature: "RESTful API", detail: "Full API access with CORS support" }
];

serverCapabilities.forEach((item, idx) => {
  const row = Math.floor(idx / 2);
  const col = idx % 2;
  const xPos = 0.5 + col * 4.8;
  const yPos = 1.5 + row * 0.95;

  slide5.addShape(pres.ShapeType.ellipse, {
    x: xPos, y: yPos, w: 0.5, h: 0.5,
    fill: { color: colors.secondary }
  });

  slide5.addText(item.feature, {
    x: xPos + 0.6, y: yPos, w: 3.6, h: 0.25,
    fontSize: 13, bold: true, color: colors.dark,
    fontFace: "Arial"
  });

  slide5.addText(item.detail, {
    x: xPos + 0.6, y: yPos + 0.28, w: 3.6, h: 0.2,
    fontSize: 10, color: colors.textDark,
    fontFace: "Arial"
  });
});

// Slide 6: DCIM Agent Features
const slide6 = pres.addSlide();
slide6.background = { color: colors.lightGray };

slide6.addText("DCIM Agent: Comprehensive Monitoring", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontSize: 36, bold: true, color: colors.dark,
  fontFace: "Arial"
});

const agentCapabilities = [
  { feature: "System Metrics", detail: "CPU, Memory, Disk, Network monitoring" },
  { feature: "Hardware Sensors", detail: "Temperature, voltage, fan speed, power" },
  { feature: "Network Intelligence", detail: "LLDP discovery, link speed, firmware tracking" },
  { feature: "SNMP Monitoring", detail: "Multi-device support (v1/v2c/v3)" },
  { feature: "Anomaly Detection", detail: "Statistical baseline with sensitivity levels" },
  { feature: "Root Cause Analysis", detail: "Correlates metrics and identifies patterns" },
  { feature: "Hyper-V Monitoring", detail: "VM discovery, resource usage, heartbeat" },
  { feature: "Firmware Tracking", detail: "BIOS, NIC, RAID, BMC version management" }
];

agentCapabilities.forEach((item, idx) => {
  const row = Math.floor(idx / 2);
  const col = idx % 2;
  const xPos = 0.5 + col * 4.8;
  const yPos = 1.5 + row * 0.95;

  slide6.addShape(pres.ShapeType.ellipse, {
    x: xPos, y: yPos, w: 0.5, h: 0.5,
    fill: { color: colors.primary }
  });

  slide6.addText(item.feature, {
    x: xPos + 0.6, y: yPos, w: 3.6, h: 0.25,
    fontSize: 13, bold: true, color: colors.dark,
    fontFace: "Arial"
  });

  slide6.addText(item.detail, {
    x: xPos + 0.6, y: yPos + 0.28, w: 3.6, h: 0.2,
    fontSize: 10, color: colors.textDark,
    fontFace: "Arial"
  });
});

// Slide 7: Security & Compliance
const slide7 = pres.addSlide();
slide7.background = { color: colors.white };

slide7.addText("Enterprise Security Built-In", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontSize: 36, bold: true, color: colors.dark,
  fontFace: "Arial"
});

// Left column - Security features
slide7.addShape(pres.ShapeType.roundRect, {
  x: 0.5, y: 1.5, w: 4.3, h: 4.2,
  fill: { color: colors.dark }
});

slide7.addText("Security Features", {
  x: 0.7, y: 1.7, w: 4.0, h: 0.5,
  fontSize: 20, bold: true, color: colors.accent,
  fontFace: "Arial"
});

const securityFeatures = [
  "• Mutual TLS Authentication",
  "• X.509 Certificate Management",
  "• TLS 1.2/1.3 Support",
  "• Client Certificate Verification",
  "• CA Certificate Validation",
  "• Agent Approval Workflow",
  "• Encrypted Data Transmission",
  "• No Plaintext Credentials"
];

securityFeatures.forEach((feat, idx) => {
  slide7.addText(feat, {
    x: 0.9, y: 2.4 + idx * 0.35, w: 3.6, h: 0.3,
    fontSize: 12, color: colors.white,
    fontFace: "Arial"
  });
});

// Right column - Compliance
slide7.addShape(pres.ShapeType.roundRect, {
  x: 5.2, y: 1.5, w: 4.3, h: 4.2,
  fill: { color: colors.primary }
});

slide7.addText("Compliance Ready", {
  x: 5.4, y: 1.7, w: 4.0, h: 0.5,
  fontSize: 20, bold: true, color: colors.white,
  fontFace: "Arial"
});

const complianceFeatures = [
  "• Data Retention Policies",
  "• Audit Trail Logging",
  "• Configurable Thresholds",
  "• License Enforcement",
  "• Role-based Access (via certs)",
  "• Data Encryption at Rest",
  "• Network Segmentation Ready",
  "• Industry Standard Protocols"
];

complianceFeatures.forEach((feat, idx) => {
  slide7.addText(feat, {
    x: 5.6, y: 2.4 + idx * 0.35, w: 3.6, h: 0.3,
    fontSize: 12, color: colors.white,
    fontFace: "Arial"
  });
});

// Slide 8: Monitoring Capabilities
const slide8 = pres.addSlide();
slide8.background = { color: colors.lightGray };

slide8.addText("30+ Monitoring Metrics", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontSize: 36, bold: true, color: colors.dark,
  fontFace: "Arial"
});

const metricsCategories = [
  {
    category: "System Performance",
    metrics: "CPU (per-core), Memory (RAM/Swap), Disk I/O (IOPS, latency), Network (packets, errors), Process count, System uptime"
  },
  {
    category: "Hardware Health",
    metrics: "Temperature sensors, Voltage monitoring, Fan speeds, Power consumption, Water cooling (pump/flow), Battery status"
  },
  {
    category: "Network Intelligence",
    metrics: "LLDP discovery, Link speed/duplex, IPv4/IPv6 addresses, Driver/firmware versions, PCI slot info, MTU size"
  },
  {
    category: "Storage Analytics",
    metrics: "Disk capacity & usage, Inode tracking, I/O throughput, Queue depth, Latency monitoring, Filesystem health"
  },
  {
    category: "SNMP Devices",
    metrics: "System info, CPU/Memory, Load averages, Disk usage, Network interfaces, TCP/UDP stats, Temperature"
  },
  {
    category: "Virtualization",
    metrics: "Hyper-V VMs, VM resource usage, Heartbeat status, Integration services, VM uptime, State monitoring"
  }
];

metricsCategories.forEach((item, idx) => {
  const row = Math.floor(idx / 2);
  const col = idx % 2;
  const xPos = 0.5 + col * 4.8;
  const yPos = 1.5 + row * 1.35;

  slide8.addShape(pres.ShapeType.roundRect, {
    x: xPos, y: yPos, w: 4.3, h: 1.15,
    fill: { color: colors.white },
    line: { color: colors.secondary, width: 2 }
  });

  slide8.addText(item.category, {
    x: xPos + 0.2, y: yPos + 0.1, w: 4.0, h: 0.3,
    fontSize: 14, bold: true, color: colors.primary,
    fontFace: "Arial"
  });

  slide8.addText(item.metrics, {
    x: xPos + 0.2, y: yPos + 0.45, w: 3.9, h: 0.6,
    fontSize: 10, color: colors.textDark,
    fontFace: "Arial"
  });
});

// Slide 9: Architecture & Scalability
const slide9 = pres.addSlide();
slide9.background = { color: colors.white };

slide9.addText("Scalable Architecture", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontSize: 36, bold: true, color: colors.dark,
  fontFace: "Arial"
});

// Architecture diagram
const archComponents = [
  { label: "Agents", x: 1.0, y: 2.0, color: colors.secondary },
  { label: "SNMP Devices", x: 1.0, y: 3.2, color: colors.secondary },
  { label: "Load Balancer", x: 4.0, y: 2.6, color: colors.accent },
  { label: "DCIM Server", x: 7.0, y: 2.0, color: colors.primary },
  { label: "Database", x: 7.0, y: 3.2, color: colors.primary }
];

archComponents.forEach(comp => {
  slide9.addShape(pres.ShapeType.roundRect, {
    x: comp.x, y: comp.y, w: 2.0, h: 0.8,
    fill: { color: comp.color },
    line: { color: comp.color, width: 1 }
  });

  slide9.addText(comp.label, {
    x: comp.x, y: comp.y, w: 2.0, h: 0.8,
    fontSize: 14, bold: true, color: colors.white,
    align: "center", valign: "middle", fontFace: "Arial"
  });
});

// Connection lines
slide9.addShape(pres.ShapeType.line, {
  x: 3.0, y: 2.4, w: 1.0, h: 0,
  line: { color: colors.primary, width: 2, endArrowType: "triangle" }
});

slide9.addShape(pres.ShapeType.line, {
  x: 3.0, y: 3.6, w: 1.0, h: 0,
  line: { color: colors.primary, width: 2, endArrowType: "triangle" }
});

slide9.addShape(pres.ShapeType.line, {
  x: 6.0, y: 2.4, w: 1.0, h: 0,
  line: { color: colors.primary, width: 2, endArrowType: "triangle" }
});

slide9.addShape(pres.ShapeType.line, {
  x: 8.0, y: 2.8, w: 0, h: 0.4,
  line: { color: colors.primary, width: 2, endArrowType: "triangle" }
});

// Key features
const archFeatures = [
  "Worker pools for parallel processing",
  "Batch processing for high throughput",
  "Connection pooling for efficiency",
  "Rate limiting per agent",
  "Horizontal scaling support",
  "Multi-database backend options"
];

archFeatures.forEach((feat, idx) => {
  slide9.addText("✓ " + feat, {
    x: 1.0, y: 4.5 + idx * 0.3, w: 8.0, h: 0.25,
    fontSize: 12, color: colors.textDark,
    fontFace: "Arial"
  });
});

// Slide 10: Use Cases
const slide10 = pres.addSlide();
slide10.background = { color: colors.lightGray };

slide10.addText("Real-World Use Cases", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontSize: 36, bold: true, color: colors.dark,
  fontFace: "Arial"
});

const useCases = [
  {
    title: "Enterprise Data Centers",
    description: "Monitor hundreds of servers with centralized management and real-time alerting"
  },
  {
    title: "Cloud Infrastructure",
    description: "Track VM performance, resource usage, and capacity planning across hybrid clouds"
  },
  {
    title: "Network Operations",
    description: "SNMP monitoring of switches, routers, and network devices with LLDP discovery"
  },
  {
    title: "Colocation Facilities",
    description: "Multi-tenant monitoring with isolated views and license-based agent limits"
  }
];

useCases.forEach((useCase, idx) => {
  const row = Math.floor(idx / 2);
  const col = idx % 2;
  const xPos = 0.5 + col * 4.8;
  const yPos = 1.6 + row * 2.0;

  slide10.addShape(pres.ShapeType.roundRect, {
    x: xPos, y: yPos, w: 4.3, h: 1.5,
    fill: { color: colors.white },
    line: { color: colors.primary, width: 2 }
  });

  slide10.addText(useCase.title, {
    x: xPos + 0.2, y: yPos + 0.3, w: 3.9, h: 0.4,
    fontSize: 16, bold: true, color: colors.primary, align: "center",
    fontFace: "Arial"
  });

  slide10.addText(useCase.description, {
    x: xPos + 0.2, y: yPos + 0.8, w: 3.9, h: 0.5,
    fontSize: 11, color: colors.textDark, align: "center",
    fontFace: "Arial"
  });
});

// Slide 11: Why Choose Us
const slide11 = pres.addSlide();
slide11.background = { color: colors.white };

slide11.addText("Why Choose Our DCIM Platform?", {
  x: 0.5, y: 0.5, w: 9, h: 0.7,
  fontSize: 36, bold: true, color: colors.dark,
  fontFace: "Arial"
});

const advantages = [
  { num: "1", title: "Enterprise Security", desc: "mTLS authentication with certificate-based trust" },
  { num: "2", title: "Comprehensive Monitoring", desc: "30+ metrics covering system, hardware, and network" },
  { num: "3", title: "Intelligent Analytics", desc: "Anomaly detection and root cause analysis built-in" },
  { num: "4", title: "Cross-Platform", desc: "Windows, Linux, macOS support out-of-the-box" },
  { num: "5", title: "Flexible Deployment", desc: "SQLite for small setups, PostgreSQL/MySQL for enterprise" },
  { num: "6", title: "Modern Architecture", desc: "Built with Go for performance and scalability" }
];

advantages.forEach((item, idx) => {
  const row = Math.floor(idx / 2);
  const col = idx % 2;
  const xPos = 0.5 + col * 4.8;
  const yPos = 1.6 + row * 1.3;

  // Number circle
  slide11.addShape(pres.ShapeType.ellipse, {
    x: xPos, y: yPos, w: 0.6, h: 0.6,
    fill: { color: colors.primary }
  });

  slide11.addText(item.num, {
    x: xPos, y: yPos, w: 0.6, h: 0.6,
    fontSize: 20, bold: true, color: colors.white,
    align: "center", valign: "middle", fontFace: "Arial"
  });

  slide11.addText(item.title, {
    x: xPos + 0.75, y: yPos + 0.05, w: 3.4, h: 0.3,
    fontSize: 14, bold: true, color: colors.dark,
    fontFace: "Arial"
  });

  slide11.addText(item.desc, {
    x: xPos + 0.75, y: yPos + 0.35, w: 3.4, h: 0.2,
    fontSize: 11, color: colors.textDark,
    fontFace: "Arial"
  });
});

// Slide 12: Call to Action
const slide12 = pres.addSlide();
slide12.background = { color: colors.dark };

slide12.addText("Ready to Transform Your", {
  x: 0.5, y: 1.8, w: 9, h: 0.6,
  fontSize: 36, color: colors.white, align: "center",
  fontFace: "Arial"
});

slide12.addText("Data Center Monitoring?", {
  x: 0.5, y: 2.5, w: 9, h: 0.8,
  fontSize: 44, bold: true, color: colors.accent, align: "center",
  fontFace: "Arial"
});

// CTA Box
slide12.addShape(pres.ShapeType.roundRect, {
  x: 2.5, y: 3.8, w: 5.0, h: 1.2,
  fill: { color: colors.primary }
});

slide12.addText("Schedule a Demo Today", {
  x: 2.5, y: 4.1, w: 5.0, h: 0.6,
  fontSize: 24, bold: true, color: colors.white,
  align: "center", valign: "middle", fontFace: "Arial"
});

slide12.addText("Contact us to see the DCIM Platform in action", {
  x: 0.5, y: 5.2, w: 9, h: 0.4,
  fontSize: 16, color: colors.white, align: "center", italic: true,
  fontFace: "Arial"
});

// Save presentation
await pres.writeFile({ fileName: "DCIM_Enterprise_Presentation.pptx" });
console.log("✓ Presentation created successfully: DCIM_Enterprise_Presentation.pptx");
