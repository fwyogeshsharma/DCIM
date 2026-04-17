import type { ServerConfig, Agent, SNMPDevice } from './types'

// ── Mock Data Generator for Topology Testing ─────────────────────────────────
// Generates 500+ nodes covering all device types and statuses.
// Toggled via USE_MOCK_DATA flag in Topology.tsx and Topology3D.tsx.

const SERVER_COLORS = [
  '#8b5cf6', '#3b82f6', '#10b981', '#f59e0b', '#ef4444',
  '#ec4899', '#06b6d4', '#84cc16', '#6366f1', '#f97316',
]

const LOCATIONS = [
  'US-East-1', 'US-West-2', 'EU-West-1', 'EU-Central-1', 'AP-South-1',
  'AP-Northeast-1', 'SA-East-1', 'CA-Central-1', 'ME-South-1', 'AF-South-1',
]

const ENVIRONMENTS = [
  'production', 'staging', 'development', 'qa', 'dr',
]

const HOSTNAMES_PREFIX = [
  'web', 'api', 'db', 'cache', 'worker', 'gateway', 'proxy', 'monitor',
  'backup', 'storage', 'compute', 'queue', 'scheduler', 'auth', 'search',
  'analytics', 'logs', 'metrics', 'mail', 'dns', 'vpn', 'lb', 'cdn',
  'vault', 'consul', 'kafka', 'redis', 'nginx', 'haproxy', 'jenkins',
]

const DEVICE_TYPES = [
  // Switches
  { prefix: 'sw', names: ['Cisco-Cat-9300', 'Arista-7050X', 'Juniper-EX4300', 'HP-5412R', 'Dell-S5248'] },
  // Routers
  { prefix: 'rt', names: ['Cisco-ISR-4451', 'Juniper-MX204', 'Mikrotik-CCR2004', 'Arista-7280R'] },
  // Firewalls
  { prefix: 'fw', names: ['Palo-FW-5260', 'Fortinet-FG600E', 'Checkpoint-6200', 'Cisco-ASA-5525'] },
  // Access Points
  { prefix: 'ap', names: ['Ubiquiti-U6-Pro', 'Cisco-AP-9120', 'Aruba-AP-535', 'Ruckus-R750'] },
  // UPS/PDU
  { prefix: 'ups', names: ['APC-SRT10K', 'Eaton-9PX', 'Vertiv-GXT5', 'CyberPower-OL6KRT'] },
  // Printers/IoT
  { prefix: 'iot', names: ['HP-LaserJet-M609', 'Env-Sensor-T100', 'CRAC-Unit-A1', 'Camera-PTZ-200'] },
  // Storage
  { prefix: 'nas', names: ['NetApp-FAS8700', 'Synology-RS3621', 'QNAP-TS-h2490FU', 'Dell-PowerStore'] },
]

function randomChoice<T>(arr: T[]): T {
  return arr[Math.floor(Math.random() * arr.length)]
}

function randomIp(): string {
  return `10.${Math.floor(Math.random() * 255)}.${Math.floor(Math.random() * 255)}.${Math.floor(Math.random() * 254) + 1}`
}

function minutesAgo(minutes: number): string {
  return new Date(Date.now() - minutes * 60 * 1000).toISOString()
}

// ── Generate Servers ─────────────────────────────────────────────────────────

function generateServers(count: number): ServerConfig[] {
  const servers: ServerConfig[] = []
  for (let i = 1; i <= count; i++) {
    const isOffline = i === 3 || i === 7 // 2 offline servers
    servers.push({
      id: `mock-server-${i}`,
      name: `DC-${LOCATIONS[i - 1] || LOCATIONS[0]}-Rack${i}`,
      url: `https://dcim-${i}.internal:8443`,
      enabled: true,
      auth_type: 'certificate',
      metadata: {
        location: LOCATIONS[(i - 1) % LOCATIONS.length],
        environment: ENVIRONMENTS[(i - 1) % ENVIRONMENTS.length],
        color: SERVER_COLORS[(i - 1) % SERVER_COLORS.length],
      },
      health: {
        status: isOffline ? 'offline' : 'healthy',
        responseTime: isOffline ? undefined : Math.floor(Math.random() * 200 + 10),
        error: isOffline ? 'Connection refused' : undefined,
        timestamp: new Date().toISOString(),
      },
      created_at: minutesAgo(60 * 24 * 30),
      updated_at: new Date().toISOString(),
    })
  }
  return servers
}

// ── Generate Agents ──────────────────────────────────────────────────────────

function generateAgents(servers: ServerConfig[], totalAgents: number): Agent[] {
  const agents: Agent[] = []
  const agentsPerServer = Math.floor(totalAgents / servers.length)
  const now = new Date().toISOString()
  let agentIndex = 0

  servers.forEach((server, si) => {
    // Last server gets the remainder
    const count = si === servers.length - 1
      ? totalAgents - agentIndex
      : agentsPerServer + (si < totalAgents % servers.length ? 1 : 0)

    for (let j = 0; j < count && agentIndex < totalAgents; j++) {
      agentIndex++
      const prefix = HOSTNAMES_PREFIX[(agentIndex - 1) % HOSTNAMES_PREFIX.length]
      const num = Math.ceil(agentIndex / HOSTNAMES_PREFIX.length)
      // ~20% offline
      const isOffline = agentIndex % 5 === 0
      const isServerOffline = server.health?.status === 'offline'

      agents.push({
        id: agentIndex,
        agent_id: `mock-agent-${agentIndex}`,
        server_id: server.id,
        server_name: server.name,
        server_url: server.url,
        certificate_cn: `${prefix}-${num}.${server.id}.internal`,
        hostname: `${prefix}-${num}.${LOCATIONS[si % LOCATIONS.length].toLowerCase()}`,
        ip_address: randomIp(),
        status: isOffline || isServerOffline ? 'offline' : 'online',
        group: ENVIRONMENTS[si % ENVIRONMENTS.length],
        last_seen: isOffline ? minutesAgo(Math.floor(Math.random() * 120 + 10)) : minutesAgo(Math.floor(Math.random() * 2)),
        first_seen: minutesAgo(60 * 24 * 30),
        registered_at: minutesAgo(60 * 24 * 30),
        approved_at: minutesAgo(60 * 24 * 29),
        approved: true,
        total_metrics: Math.floor(Math.random() * 5000 + 100),
        total_alerts: isOffline ? Math.floor(Math.random() * 20 + 1) : Math.floor(Math.random() * 5),
        created_at: minutesAgo(60 * 24 * 30),
        updated_at: now,
      })
    }
  })

  return agents
}

// ── Generate SNMP Devices ────────────────────────────────────────────────────

function generateSNMPDevices(agents: Agent[], totalDevices: number): SNMPDevice[] {
  const devices: SNMPDevice[] = []
  // Distribute devices among ~60% of agents
  const eligibleAgents = agents.filter((_, i) => i % 3 !== 2)
  const devicesPerAgent = Math.max(1, Math.floor(totalDevices / eligibleAgents.length))
  let deviceIndex = 0

  for (const agent of eligibleAgents) {
    if (deviceIndex >= totalDevices) break

    const count = Math.min(
      devicesPerAgent + (Math.random() > 0.7 ? 2 : 0),
      totalDevices - deviceIndex
    )

    for (let j = 0; j < count; j++) {
      deviceIndex++
      const deviceType = DEVICE_TYPES[deviceIndex % DEVICE_TYPES.length]
      const deviceName = randomChoice(deviceType.names)
      // ~30% inactive (last seen > 2 hours ago)
      const isInactive = deviceIndex % 3 === 0

      devices.push({
        device_name: `${deviceType.prefix}-${deviceName}-${deviceIndex}`,
        device_ip: randomIp(),
        agent_id: agent.agent_id,
        server_id: agent.server_id || '',
        server_name: agent.server_name || '',
        last_seen: isInactive
          ? minutesAgo(Math.floor(Math.random() * 300 + 121)) // > 2h ago
          : minutesAgo(Math.floor(Math.random() * 30)),        // recent
      })
    }
  }

  return devices
}

// ── Public API ────────────────────────────────────────────────────────────────

export interface MockTopologyData {
  servers: ServerConfig[]
  agents: Agent[]
  snmpDevices: SNMPDevice[]
}

let _cached: MockTopologyData | null = null

export function getMockTopologyData(): MockTopologyData {
  if (_cached) return _cached

  const servers = generateServers(10)       // 10 servers (2 offline)
  const agents = generateAgents(servers, 350) // 350 agents (~20% offline)
  const snmpDevices = generateSNMPDevices(agents, 180) // 180 SNMP devices (~30% inactive)

  // Total: 10 + 350 + 180 = 540 nodes

  _cached = { servers, agents, snmpDevices }
  return _cached
}
