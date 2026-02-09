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
import https from 'https';
import fs from 'fs';
import path from 'path';
import cors from 'cors';
import { fileURLToPath } from 'url';
import { dirname } from 'path';

// ES module equivalent of __dirname
const __filename = fileURLToPath(import.meta.url);
const __dirname = dirname(__filename);

// Configuration
const CONFIG = {
  // Proxy server settings
  proxy: {
    port: 3001,
    host: '0.0.0.0'
  },

  // DCIM server settings
  dcim: {
    host: 'localhost',
    port: 8443,
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

// Load certificates
let httpsAgent;
try {
  const certOptions = {
    // Verify server certificate
    ca: fs.readFileSync(path.resolve(__dirname, CONFIG.certs.ca)),

    // Client certificate authentication
    cert: fs.readFileSync(path.resolve(__dirname, CONFIG.certs.clientCert)),
    key: fs.readFileSync(path.resolve(__dirname, CONFIG.certs.clientKey)),

    // Alternative: Use .pfx file
    // pfx: fs.readFileSync(path.resolve(__dirname, CONFIG.certs.pfx)),
    // passphrase: CONFIG.certs.passphrase,

    // TLS options
    rejectUnauthorized: true, // Verify server certificate
    requestCert: true, // Request client certificate
    agent: false,

    // Keep connections alive
    keepAlive: true,
    keepAliveMsecs: 30000
  };

  httpsAgent = new https.Agent(certOptions);

  console.log('✅ Certificates loaded successfully');
  console.log('   Client Cert:', path.resolve(__dirname, CONFIG.certs.clientCert));
  console.log('   Client Key:', path.resolve(__dirname, CONFIG.certs.clientKey));
  console.log('   CA Cert:', path.resolve(__dirname, CONFIG.certs.ca));
} catch (error) {
  console.error('❌ Failed to load certificates:', error.message);
  console.error('\n📋 Certificate Setup Instructions:');
  console.error('   1. Copy certificates to DCIM_UI/certs/ directory');
  console.error('   2. Ensure you have:');
  console.error('      - client.crt (client certificate)');
  console.error('      - client.key (client private key)');
  console.error('      - ca.crt (CA certificate)');
  console.error('\n   Or use .pfx/.p12 format by uncommenting the pfx option');
  process.exit(1);
}

// Proxy all /api/v1/* requests to DCIM server
app.all('/api/v1/*', async (req, res) => {
  const targetPath = req.path;
  const queryString = req.url.split('?')[1] || '';
  const targetUrl = `https://${CONFIG.dcim.host}:${CONFIG.dcim.port}${targetPath}${queryString ? '?' + queryString : ''}`;

  console.log(`  → Forwarding to: ${targetUrl}`);

  const options = {
    method: req.method,
    headers: {
      ...req.headers,
      host: `${CONFIG.dcim.host}:${CONFIG.dcim.port}`,
      // Add agent ID for UI dashboard authentication
      'X-Agent-ID': 'ui-dashboard'
    },
    agent: httpsAgent
  };

  // Remove headers that shouldn't be forwarded
  delete options.headers['host'];
  delete options.headers['connection'];

  const proxyReq = https.request(targetUrl, options, (proxyRes) => {
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
    certificates: {
      loaded: !!httpsAgent,
      clientCert: CONFIG.certs.clientCert,
      ca: CONFIG.certs.ca
    }
  });
});

// Certificate info endpoint
app.get('/cert-info', (req, res) => {
  try {
    const certPem = fs.readFileSync(path.resolve(__dirname, CONFIG.certs.clientCert), 'utf8');
    const certLines = certPem.split('\n');

    res.json({
      certPath: CONFIG.certs.clientCert,
      keyPath: CONFIG.certs.clientKey,
      caPath: CONFIG.certs.ca,
      preview: certLines.slice(0, 5).join('\n') + '\n...'
    });
  } catch (error) {
    res.status(500).json({ error: error.message });
  }
});

// Start server
app.listen(CONFIG.proxy.port, CONFIG.proxy.host, () => {
  console.log('\n╔══════════════════════════════════════════════════════════════╗');
  console.log('║                                                              ║');
  console.log('║               DCIM mTLS Proxy Server                         ║');
  console.log('║                                                              ║');
  console.log('╚══════════════════════════════════════════════════════════════╝\n');
  console.log(`✅ Proxy server running on http://${CONFIG.proxy.host}:${CONFIG.proxy.port}`);
  console.log(`🔒 Forwarding to DCIM server: https://${CONFIG.dcim.host}:${CONFIG.dcim.port}`);
  console.log(`📜 Using client certificate: ${CONFIG.certs.clientCert}`);
  console.log('\n📋 Configuration:');
  console.log(`   Proxy URL: http://localhost:${CONFIG.proxy.port}/api/v1`);
  console.log(`   DCIM Server: https://${CONFIG.dcim.host}:${CONFIG.dcim.port}`);
  console.log(`   mTLS: Enabled with client certificates`);
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
