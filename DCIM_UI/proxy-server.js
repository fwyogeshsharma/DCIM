/**
 * mTLS Proxy Server for DCIM UI
 *
 * This proxy server handles mutual TLS authentication between the browser and DCIM server.
 * The browser connects to this proxy over HTTP, and the proxy forwards requests to the
 * DCIM server using client certificates.
 *
 * Usage:
 *   node proxy-server.js
 */

import express from 'express';
import http from 'http';
import https from 'https';
// import fs from 'fs';   // CERTIFICATE CHECKS DISABLED
// import path from 'path'; // CERTIFICATE CHECKS DISABLED
import cors from 'cors';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

// ES module equivalent of __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Configuration — honour environment variables so Docker can override
const _dcimUrl = process.env.DCIM_SERVER_URL || 'http://localhost:8080';
const _parsed  = new URL(_dcimUrl);

const CONFIG = {
  // Proxy server settings
  proxy: {
    port: parseInt(process.env.PROXY_PORT || '3001'),
    host: '0.0.0.0'
  },

  // DCIM server settings
  dcim: {
    host: _parsed.hostname,
    port: parseInt(_parsed.port || (_parsed.protocol === 'https:' ? '443' : '80')),
    baseUrl: '/api/v1'
  },

  // Certificate paths (relative to this file)
  certs: {
    // Client certificate for authenticating to DCIM server
    clientCert: './certs/client.crt',
    clientKey: './certs/client.key',

    // CA certificate to verify DCIM server
    ca: './certs/ca.crt',

    // Alternative: Use .pfx/.p12 file (comment out above and uncomment below)
    // pfx: './certs/client.pfx',
    // passphrase: 'your-passphrase-here'
  }
};

// Create Express app
const app = express();

// Middleware
app.use(cors({
  origin: true, // Allow all origins in development
  credentials: true
}));
app.use(express.json());
app.use(express.urlencoded({ extended: true }));

// Request logging
app.use((req, res, next) => {
  console.log(`[${new Date().toISOString()}] ${req.method} ${req.path}`);
  next();
});

// CERTIFICATE CHECKS DISABLED - cert loading commented out
// let httpsAgent;
// try {
//   const certOptions = {
//     // Verify server certificate
//     ca: fs.readFileSync(path.resolve(__dirname, CONFIG.certs.ca)),
//
//     // Client certificate authentication
//     cert: fs.readFileSync(path.resolve(__dirname, CONFIG.certs.clientCert)),
//     key: fs.readFileSync(path.resolve(__dirname, CONFIG.certs.clientKey)),
//
//     // Alternative: Use .pfx file
//     // pfx: fs.readFileSync(path.resolve(__dirname, CONFIG.certs.pfx)),
//     // passphrase: CONFIG.certs.passphrase,
//
//     // TLS options
//     rejectUnauthorized: true, // Verify server certificate
//     requestCert: true, // Request client certificate
//     agent: false,
//
//     // Keep connections alive
//     keepAlive: true,
//     keepAliveMsecs: 30000
//   };
//
//   httpsAgent = new https.Agent(certOptions);
//
//   console.log('✅ Certificates loaded successfully');
//   console.log('   Client Cert:', path.resolve(__dirname, CONFIG.certs.clientCert));
//   console.log('   Client Key:', path.resolve(__dirname, CONFIG.certs.clientKey));
//   console.log('   CA Cert:', path.resolve(__dirname, CONFIG.certs.ca));
// } catch (error) {
//   console.error('❌ Failed to load certificates:', error.message);
//   console.error('\n📋 Certificate Setup Instructions:');
//   console.error('   1. Copy certificates to DCIM_UI/certs/ directory');
//   console.error('   2. Ensure you have:');
//   console.error('      - client.crt (client certificate)');
//   console.error('      - client.key (client private key)');
//   console.error('      - ca.crt (CA certificate)');
//   console.error('\n   Or use .pfx/.p12 format by uncommenting the pfx option');
//   process.exit(1);
// }
console.log('ℹ️  Certificate checks disabled - connecting without mTLS');

// Synthesise /api/v1/servers from live agent data so the topology gets
// real server_ids (DCIM-assigned) mapped to friendly names and colours.
app.get('/api/v1/servers', async (req, res) => {
  try {
    const agentUrl = `http://${CONFIG.dcim.host}:${CONFIG.dcim.port}/api/v1/agents`;
    const agentRes = await new Promise((resolve, reject) => {
      http.get(agentUrl, { headers: { 'X-Agent-ID': 'ui-dashboard' } }, resolve).on('error', reject);
    });
    let body = '';
    for await (const chunk of agentRes) body += chunk;
    const { data: agents = [] } = JSON.parse(body);

    // Group agents by server_id and derive server name from agent_id prefix
    const groups = {};
    for (const a of agents) {
      if (!groups[a.server_id]) groups[a.server_id] = [];
      groups[a.server_id].push(a);
    }

    const COLORS = ['#3b82f6', '#8b5cf6', '#10b981'];
    const servers = Object.entries(groups).map(([id, agts], i) => {
      // agent_ids look like "network-a-SRV-A1-01" — take first two segments
      const prefix = agts[0].agent_id.split('-').slice(0, 2).join('-');
      const letter = prefix.replace('network-', '');
      const name = `dcim-server-${letter}`;
      return {
        id,
        name,
        url: `http://${name}:8080`,
        enabled: true,
        metadata: { color: COLORS[i % COLORS.length], network: prefix },
        health: { status: 'healthy' },
        hasCerts: false,
      };
    });

    res.json({ success: true, data: servers });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Helper: fetch all agents from DCIM server (reused by multiple routes)
async function fetchAgents() {
  const url = `http://${CONFIG.dcim.host}:${CONFIG.dcim.port}/api/v1/agents`;
  const raw = await new Promise((resolve, reject) => {
    http.get(url, { headers: { 'X-Agent-ID': 'ui-dashboard' } }, (r) => {
      let b = ''; r.on('data', c => b += c); r.on('end', () => resolve(b)); r.on('error', reject);
    }).on('error', reject);
  });
  return JSON.parse(raw).data || [];
}

// Synthesise /api/v1/snmp/devices from agents so the topology "Devices" count
// shows the simulated network devices instead of always 0.
app.get('/api/v1/snmp/devices', async (req, res) => {
  try {
    const agents = await fetchAgents();
    const { agent_id: filterAgent, server_id: filterServer } = req.query;
    const devices = agents
      .filter(a => (!filterAgent || a.agent_id === filterAgent) && (!filterServer || a.server_id === filterServer))
      .map(a => ({
        device_name: a.hostname,
        device_ip:   a.ip_address,
        agent_id:    a.agent_id,
        server_id:   a.server_id,
        last_seen:   a.last_seen,
      }));
    res.json({ success: true, data: devices, count: devices.length });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Synthesise /api/v1/agents/stats/by-server so the Dashboard server summary works.
app.get('/api/v1/agents/stats/by-server', async (req, res) => {
  try {
    const agents = await fetchAgents();
    const limit = parseInt(req.query.limit) || 5;

    const serverMap = {};
    for (const a of agents) {
      const sid = a.server_id;
      if (!serverMap[sid]) {
        const prefix = a.agent_id.split('-').slice(0, 2).join('-');
        const letter = prefix.replace('network-', '');
        serverMap[sid] = { server_id: sid, server_name: `dcim-server-${letter}`, color: null, total: 0, online: 0, offline: 0 };
      }
      serverMap[sid].total++;
      if (a.status === 'online') serverMap[sid].online++;
      else serverMap[sid].offline++;
    }

    const servers = Object.values(serverMap).slice(0, limit);
    const totals = servers.reduce((t, s) => ({
      total: t.total + s.total, online: t.online + s.online,
      offline: t.offline + s.offline, servers: t.servers + 1,
    }), { total: 0, online: 0, offline: 0, servers: 0 });

    res.json({ success: true, data: { totals, servers } });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Synthesise /api/v1/servers/health/summary for the Dashboard server health widget.
app.get('/api/v1/servers/health/summary', async (req, res) => {
  try {
    const agents = await fetchAgents();
    const groups = {};
    for (const a of agents) {
      if (!groups[a.server_id]) groups[a.server_id] = a;
    }
    const COLORS = ['#3b82f6', '#8b5cf6', '#10b981'];
    const serverList = Object.entries(groups).map(([id, a], i) => {
      const prefix = a.agent_id.split('-').slice(0, 2).join('-');
      const name = `dcim-server-${prefix.replace('network-', '')}`;
      return { id, name, url: `http://${name}:8080`, color: COLORS[i % COLORS.length], status: 'healthy', error: null, responseTime: 10 };
    });
    res.json({ success: true, data: { total: serverList.length, healthy: serverList.length, offline: 0, tls_error: 0, unknown: 0, needs_attention: [] } });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// Synthesise /api/v1/dashboard/stats
app.get('/api/v1/dashboard/stats', async (req, res) => {
  try {
    const agents = await fetchAgents();
    const online = agents.filter(a => a.status === 'online').length;
    const serverIds = new Set(agents.map(a => a.server_id));
    res.json({ success: true, data: { servers: serverIds.size, agents: { total: agents.length, online, offline: agents.length - online }, activeAlerts: 0 } });
  } catch (err) {
    res.status(500).json({ success: false, error: err.message });
  }
});

// ── Aggregator proxy helpers ───────────────────────────────────────────────
// Some endpoints live in the aggregator (port 3002), not the DCIM server.
// Route these BEFORE the catch-all that forwards to dcim-server-a.
const _aggUrl = process.env.AGGREGATOR_URL || 'http://aggregator:3002'
const _aggParsed = new URL(_aggUrl)
const AGG_HOST = _aggParsed.hostname
const AGG_PORT = parseInt(_aggParsed.port || '3002')

function proxyToAggregator(req, res) {
  const queryString = req.url.includes('?') ? req.url.split('?').slice(1).join('?') : ''
  const targetUrl = `http://${AGG_HOST}:${AGG_PORT}${req.path}${queryString ? '?' + queryString : ''}`
  console.log(`  → Aggregator: ${targetUrl}`)

  const headers = { ...req.headers, host: `${AGG_HOST}:${AGG_PORT}` }
  delete headers['host']
  delete headers['connection']
  // Strip conditional cache headers so the aggregator always returns fresh data (200),
  // never a 304 that the browser would serve from a potentially stale cache.
  delete headers['if-none-match']
  delete headers['if-modified-since']
  delete headers['if-match']
  delete headers['if-unmodified-since']
  delete headers['if-range']

  const proxyReq = http.request(targetUrl, { method: req.method, headers }, (proxyRes) => {
    console.log(`  ← Aggregator response: ${proxyRes.statusCode}`)
    res.status(proxyRes.statusCode)
    Object.keys(proxyRes.headers).forEach(k => res.setHeader(k, proxyRes.headers[k]))
    proxyRes.pipe(res)
  })

  proxyReq.on('error', (err) => {
    console.error('  ✗ Aggregator proxy error:', err.message)
    if (!res.headersSent) res.status(503).json({ error: 'Aggregator unavailable', message: err.message })
  })

  if (req.method !== 'GET' && req.method !== 'HEAD') {
    proxyReq.write(JSON.stringify(req.body))
  }
  proxyReq.end()
}

// SSE event stream — must stream without buffering
app.get('/api/v1/events', (req, res) => {
  const targetUrl = `http://${AGG_HOST}:${AGG_PORT}/api/v1/events`
  console.log(`  → SSE stream: ${targetUrl}`)

  res.setHeader('Content-Type', 'text/event-stream')
  res.setHeader('Cache-Control', 'no-cache')
  res.setHeader('Connection', 'keep-alive')
  res.setHeader('X-Accel-Buffering', 'no')

  const aggReq = http.get(targetUrl, (aggRes) => {
    aggRes.pipe(res)
  })

  aggReq.on('error', (err) => {
    console.error('  ✗ SSE proxy error:', err.message)
    res.end()
  })

  req.on('close', () => aggReq.destroy())
})

// Agents — aggregator JOINs with servers table so server_name is populated
app.all('/api/v1/agents*', (req, res) => proxyToAggregator(req, res))

// Alerts — aggregator has deduplication, counts, latest endpoints
app.all('/api/v1/alerts*', (req, res) => proxyToAggregator(req, res))

// Topology nodes + links — served from aggregator DB
app.all('/api/v1/topology*', (req, res) => proxyToAggregator(req, res))

// SNMP traps
app.all('/api/v1/traps*', (req, res) => proxyToAggregator(req, res))

// Dashboard full data — aggregator has the aggregated view
app.get('/api/v1/dashboard', (req, res) => proxyToAggregator(req, res))

// Server management — aggregator owns the servers DB, CRUD, and health checks.
// These must be before the DCIM catch-all below, but after the synthesized
// GET /api/v1/servers and GET /api/v1/servers/health/summary routes above.
app.get('/api/v1/servers/:id/health', (req, res) => proxyToAggregator(req, res))
app.get('/api/v1/servers/:id', (req, res) => proxyToAggregator(req, res))
app.post('/api/v1/servers/:id/toggle', (req, res) => proxyToAggregator(req, res))
app.delete('/api/v1/servers/:id', (req, res) => proxyToAggregator(req, res))
app.post('/api/v1/servers', (req, res) => proxyToAggregator(req, res))
app.put('/api/v1/servers/:id', (req, res) => proxyToAggregator(req, res))

// Proxy all /api/v1/* requests to DCIM server
// CERTIFICATE CHECKS DISABLED - using plain HTTP instead of HTTPS
app.all('/api/v1/*', async (req, res) => {
  const targetPath = req.path;
  const queryString = req.url.split('?')[1] || '';
  const targetUrl = `http://${CONFIG.dcim.host}:${CONFIG.dcim.port}${targetPath}${queryString ? '?' + queryString : ''}`;

  console.log(`  → Forwarding to: ${targetUrl}`);

  const options = {
    method: req.method,
    headers: {
      ...req.headers,
      host: `${CONFIG.dcim.host}:${CONFIG.dcim.port}`,
      'X-Agent-ID': 'ui-dashboard'
    },
    // agent: httpsAgent // CERTIFICATE CHECKS DISABLED
  };

  // Remove headers that shouldn't be forwarded
  delete options.headers['host'];
  delete options.headers['connection'];
  // Strip conditional cache headers so backend always returns fresh data (200 not 304)
  delete options.headers['if-none-match'];
  delete options.headers['if-modified-since'];
  delete options.headers['if-match'];
  delete options.headers['if-unmodified-since'];
  delete options.headers['if-range'];

  const proxyReq = http.request(targetUrl, options, (proxyRes) => {
    console.log(`  ← Response: ${proxyRes.statusCode}`);

    // Forward response headers
    res.status(proxyRes.statusCode);
    Object.keys(proxyRes.headers).forEach(key => {
      res.setHeader(key, proxyRes.headers[key]);
    });

    // Forward response body
    proxyRes.pipe(res);
  });

  // Handle errors
  proxyReq.on('error', (error) => {
    console.error('  ✗ Proxy error:', error.message);

    if (error.code === 'DEPTH_ZERO_SELF_SIGNED_CERT') {
      res.status(500).json({
        error: 'Certificate verification failed',
        message: 'Server certificate is self-signed. You may need to add it to trusted certificates.'
      });
    } else if (error.code === 'UNABLE_TO_VERIFY_LEAF_SIGNATURE') {
      res.status(500).json({
        error: 'Certificate verification failed',
        message: 'Unable to verify server certificate signature.'
      });
    } else if (error.code === 'ECONNREFUSED') {
      res.status(503).json({
        error: 'Connection refused',
        message: `Cannot connect to DCIM server at ${CONFIG.dcim.host}:${CONFIG.dcim.port}`
      });
    } else {
      res.status(500).json({
        error: 'Proxy error',
        message: error.message
      });
    }
  });

  // Forward request body
  if (req.method !== 'GET' && req.method !== 'HEAD') {
    proxyReq.write(JSON.stringify(req.body));
  }

  proxyReq.end();
});

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({
    status: 'ok',
    proxy: 'running',
    dcimServer: `${CONFIG.dcim.host}:${CONFIG.dcim.port}`,
    // CERTIFICATE CHECKS DISABLED
    // certificates: {
    //   loaded: !!httpsAgent,
    //   clientCert: CONFIG.certs.clientCert,
    //   ca: CONFIG.certs.ca
    // }
    certificates: { loaded: false, note: 'certificate checks disabled' }
  });
});

// Certificate info endpoint - CERTIFICATE CHECKS DISABLED
app.get('/cert-info', (req, res) => {
  res.json({ note: 'Certificate checks are disabled' });
  // CERTIFICATE CHECKS DISABLED - original cert-info code below
  // try {
  //   const certPem = fs.readFileSync(path.resolve(__dirname, CONFIG.certs.clientCert), 'utf8');
  //   const certLines = certPem.split('\n');
  //   res.json({
  //     certPath: CONFIG.certs.clientCert,
  //     keyPath: CONFIG.certs.clientKey,
  //     caPath: CONFIG.certs.ca,
  //     preview: certLines.slice(0, 5).join('\n') + '\n...'
  //   });
  // } catch (error) {
  //   res.status(500).json({ error: error.message });
  // }
});

// Start server
app.listen(CONFIG.proxy.port, CONFIG.proxy.host, () => {
  console.log('\n╔══════════════════════════════════════════════════════════════╗');
  console.log('║                                                              ║');
  console.log('║               DCIM Proxy Server (no mTLS)                    ║');
  console.log('║                                                              ║');
  console.log('╚══════════════════════════════════════════════════════════════╝\n');
  console.log(`✅ Proxy server running on http://${CONFIG.proxy.host}:${CONFIG.proxy.port}`);
  console.log(`→  Forwarding to DCIM server: http://${CONFIG.dcim.host}:${CONFIG.dcim.port}`);
  console.log('\n📋 Configuration:');
  console.log(`   Proxy URL: http://localhost:${CONFIG.proxy.port}/api/v1`);
  console.log(`   DCIM Server: http://${CONFIG.dcim.host}:${CONFIG.dcim.port}`);
  console.log(`   mTLS: Disabled (certificate checks commented out)`);
  console.log('\n🌐 Update your UI to use: http://localhost:3001/api/v1');
  console.log('   Set VITE_API_URL=http://localhost:3001/api/v1 in .env\n');
});

// Graceful shutdown
process.on('SIGTERM', () => {
  console.log('\n👋 Shutting down proxy server...');
  process.exit(0);
});

process.on('SIGINT', () => {
  console.log('\n👋 Shutting down proxy server...');
  process.exit(0);
});
