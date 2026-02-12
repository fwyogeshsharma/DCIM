const express = require('express');
const { exec } = require('child_process');
const path = require('path');
const cors = require('cors');

const app = express();
const PORT = 3000;

// Enable CORS for Angular dev server
app.use(cors({
  origin: 'http://localhost:4201',
  credentials: true
}));

app.use(express.json({ limit: '10mb' }));

// Proxy endpoint
app.post('/api/proxy/cooling-metrics', (req, res) => {
  const certsPath = path.join(__dirname, 'src', 'assets', 'certs');
  const clientCert = path.join(certsPath, 'client.crt').replace(/\\/g, '/');
  const clientKey = path.join(certsPath, 'client.key').replace(/\\/g, '/');
  const caCert = path.join(certsPath, 'ca.crt').replace(/\\/g, '/');

  // Convert request body to JSON string and escape for command line
  const jsonData = JSON.stringify(req.body);
  const escapedJson = jsonData.replace(/"/g, '\\"');

  // Extract agent_id from payload for X-Agent-ID header
  const agentId = req.body.agent_id || 'System_Sim_1';

  // Build curl command (using double quotes for Windows compatibility)
  const curlCmd = `curl -k --cert "${clientCert}" --key "${clientKey}" --cacert "${caCert}" ` +
    `-X POST https://FABER:8443/api/v1/cooling-metrics ` +
    `-H "Content-Type: application/json" ` +
    `-H "X-Agent-ID: ${agentId}" ` +
    `-d "${escapedJson}" ` +
    `--max-time 30`;

  console.log(`[${new Date().toISOString()}] Sending metrics to FABER:8443 (Agent: ${agentId}, Size: ${jsonData.length} bytes)`);

  // Add verbose flag to curl for detailed output
  const curlCmdVerbose = curlCmd + ' -w "\\nHTTP_CODE:%{http_code}\\n"';

  // Use Git Bash shell for better curl compatibility on Windows
  const execOptions = {
    maxBuffer: 10 * 1024 * 1024,
    shell: 'C:\\Program Files\\Git\\bin\\bash.exe'
  };

  exec(curlCmdVerbose, execOptions, (error, stdout, stderr) => {
    if (error) {
      console.error(`[${new Date().toISOString()}] ❌ Error: ${error.message}`);
      return res.status(500).json({
        success: false,
        message: 'Proxy error',
        error: error.message
      });
    }

    // Extract HTTP code from curl output
    const httpCodeMatch = stdout.match(/HTTP_CODE:(\d+)/);
    const httpCode = httpCodeMatch ? httpCodeMatch[1] : 'unknown';

    // Remove curl metadata from response
    const responseBody = stdout.replace(/HTTP_CODE:\d+\n/, '');

    // Try to parse the response as JSON
    try {
      const response = JSON.parse(responseBody);
      console.log(`[${new Date().toISOString()}] ✅ Success: HTTP ${httpCode} - ${response.message || 'OK'}`);
      res.json(response);
    } catch (parseError) {
      console.log(`[${new Date().toISOString()}] ⚠️  HTTP ${httpCode} - Response is not JSON`);
      res.send(responseBody);
    }
  });
});

// Health check endpoint
app.get('/health', (req, res) => {
  res.json({ status: 'ok', message: 'Proxy server running' });
});

app.listen(PORT, () => {
  console.log('='.repeat(60));
  console.log('🚀 DCIM Simulator Proxy Server');
  console.log('='.repeat(60));
  console.log(`✓ Server running on http://localhost:${PORT}`);
  console.log(`✓ Proxy endpoint: http://localhost:${PORT}/api/proxy/cooling-metrics`);
  console.log(`✓ Target API: https://FABER:8443/api/v1/cooling-metrics`);
  console.log('='.repeat(60));
});
