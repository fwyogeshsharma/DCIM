import type { Agent, ServerConfig, SNMPDevice } from './types'

export interface LayoutNode {
  id: string
  name: string
  type: 'server' | 'agent' | 'network'
  status: 'online' | 'offline'
  position: [number, number, number]
  color: string
  ip?: string
  serverId?: string
  agentId?: string
  serverName?: string
  agentName?: string
  metrics?: number
  alerts?: number
  lastSeen?: string
}

export interface LayoutLink {
  sourceId: string
  targetId: string
  sourcePos: [number, number, number]
  targetPos: [number, number, number]
  connected: boolean
  linkType?: 'agent-server' | 'device-agent'
}

export interface LayoutResult {
  nodes: LayoutNode[]
  links: LayoutLink[]
}

const SERVER_Y = 20
const AGENT_Y = -8
const DEVICE_Y = -22
const SERVER_SPACING = 55
const TWO_HOURS_MS = 2 * 60 * 60 * 1000

export function computeHierarchicalLayout(
  servers: ServerConfig[],
  agents: Agent[],
  snmpDevices: SNMPDevice[] = []
): LayoutResult {
  const enabledServers = servers.filter((s) => s.enabled)
  const nodes: LayoutNode[] = []
  const links: LayoutLink[] = []

  const totalWidth = (enabledServers.length - 1) * SERVER_SPACING
  const startX = -totalWidth / 2

  const serverPositions = new Map<string, [number, number, number]>()

  enabledServers.forEach((server, i) => {
    const x = enabledServers.length === 1 ? 0 : startX + i * SERVER_SPACING
    const pos: [number, number, number] = [x, SERVER_Y, 0]
    const serverId = `server-${server.id}`

    serverPositions.set(serverId, pos)

    nodes.push({
      id: serverId,
      name: server.name,
      type: 'server',
      status: server.health?.status === 'healthy' ? 'online' : 'offline',
      position: pos,
      color: server.metadata?.color || '#8b5cf6',
      ip: server.url,
    })
  })

  // Group agents by their server
  const agentsByServer = new Map<string, Agent[]>()
  agents.forEach((agent) => {
    const sid = `server-${agent.server_id}`
    if (!agentsByServer.has(sid)) agentsByServer.set(sid, [])
    agentsByServer.get(sid)!.push(agent)
  })

  // Group SNMP devices by compound agent key
  const devicesByAgent = new Map<string, SNMPDevice[]>()
  snmpDevices.forEach((device) => {
    const key = `${device.server_id}:${device.agent_id}`
    if (!devicesByAgent.has(key)) devicesByAgent.set(key, [])
    devicesByAgent.get(key)!.push(device)
  })

  // Position agents in arcs below their parent server
  agentsByServer.forEach((serverAgents, serverId) => {
    const parentPos = serverPositions.get(serverId)
    if (!parentPos) return

    const count = serverAgents.length
    const arcRadius = Math.max(15, count * 6)
    const spread = arcRadius * 2
    const step = count === 1 ? 0 : spread / (count - 1)

    serverAgents.forEach((agent, i) => {
      const agentX = count === 1 ? parentPos[0] : parentPos[0] - spread / 2 + i * step
      const agentZ = Math.sin((i / Math.max(count - 1, 1)) * Math.PI) * (arcRadius * 0.4)
      const finalPos: [number, number, number] = [agentX, AGENT_Y, agentZ]

      const isConnected =
        agent.status === 'online' &&
        nodes.find((n) => n.id === serverId)?.status === 'online'

      const compoundId = `${agent.server_id}:${agent.agent_id}`
      const parentServer = enabledServers.find((s) => `server-${s.id}` === serverId)

      nodes.push({
        id: compoundId,
        name: agent.hostname,
        type: 'agent',
        status: agent.status === 'online' ? 'online' : 'offline',
        position: finalPos,
        color: agent.status === 'online' ? '#10b981' : '#ef4444',
        ip: agent.ip_address,
        serverId: agent.server_id,
        agentId: agent.agent_id,
        serverName: parentServer?.name,
        metrics: agent.total_metrics,
        alerts: agent.total_alerts,
      })

      links.push({
        sourceId: compoundId,
        targetId: serverId,
        sourcePos: finalPos,
        targetPos: parentPos,
        connected: isConnected,
        linkType: 'agent-server',
      })

      // Position SNMP devices below this agent
      const agentDevices = devicesByAgent.get(compoundId) || []
      const deviceCount = agentDevices.length
      if (deviceCount === 0) return

      const deviceSpread = Math.max(deviceCount * 4, 10)
      const deviceStep = deviceCount === 1 ? 0 : deviceSpread / (deviceCount - 1)

      agentDevices.forEach((device, j) => {
        const deviceX =
          deviceCount === 1 ? finalPos[0] : finalPos[0] - deviceSpread / 2 + j * deviceStep
        const deviceZ = finalPos[2] + Math.sin((j / Math.max(deviceCount - 1, 1)) * Math.PI) * 4
        const devicePos: [number, number, number] = [deviceX, DEVICE_Y, deviceZ]

        const isActive = new Date(device.last_seen).getTime() > Date.now() - TWO_HOURS_MS
        const deviceId = `device-${device.server_id}-${device.agent_id}-${device.device_ip || device.device_name}`

        // Avoid duplicates
        if (nodes.find((n) => n.id === deviceId)) return

        nodes.push({
          id: deviceId,
          name: device.device_name || device.device_ip,
          type: 'network',
          status: isActive ? 'online' : 'offline',
          position: devicePos,
          color: isActive ? '#06b6d4' : '#475569',
          ip: device.device_ip,
          serverId: device.server_id,
          agentId: device.agent_id,
          agentName: agent.hostname,
          lastSeen: device.last_seen,
        })

        links.push({
          sourceId: deviceId,
          targetId: compoundId,
          sourcePos: devicePos,
          targetPos: finalPos,
          connected: isActive,
          linkType: 'device-agent',
        })
      })
    })
  })

  return { nodes, links }
}