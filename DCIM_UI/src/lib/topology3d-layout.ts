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
const SERVER_SPACING_MIN = 55
const TWO_HOURS_MS = 2 * 60 * 60 * 1000

export function computeHierarchicalLayout(
  servers: ServerConfig[],
  agents: Agent[],
  snmpDevices: SNMPDevice[] = []
): LayoutResult {
  const enabledServers = servers.filter((s) => s.enabled)
  const nodes: LayoutNode[] = []
  const links: LayoutLink[] = []
  const nodeIdSet = new Set<string>()

  // Group agents by their server (computed first to determine spacing)
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

  // Dynamic spacing based on max agents per server to prevent arc overlap
  const maxAgentsPerServer = Math.max(
    ...Array.from(agentsByServer.values()).map(a => a.length),
    1
  )
  const dynamicSpacing = Math.max(SERVER_SPACING_MIN, maxAgentsPerServer * 14 + 30)

  const totalWidth = (enabledServers.length - 1) * dynamicSpacing
  const startX = -totalWidth / 2

  const serverPositions = new Map<string, [number, number, number]>()
  const serverStatusMap = new Map<string, 'online' | 'offline'>()

  enabledServers.forEach((server, i) => {
    const x = enabledServers.length === 1 ? 0 : startX + i * dynamicSpacing
    const pos: [number, number, number] = [x, SERVER_Y, 0]
    const serverId = `server-${server.id}`
    const status = server.health?.status === 'healthy' ? 'online' as const : 'offline' as const

    serverPositions.set(serverId, pos)
    serverStatusMap.set(serverId, status)

    nodes.push({
      id: serverId,
      name: server.name,
      type: 'server',
      status,
      position: pos,
      color: server.metadata?.color || '#8b5cf6',
      ip: server.url,
    })
    nodeIdSet.add(serverId)
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
        serverStatusMap.get(serverId) === 'online'

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
      nodeIdSet.add(compoundId)

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

      const deviceSpread = Math.max(deviceCount * 8, 15)
      const deviceStep = deviceCount === 1 ? 0 : deviceSpread / (deviceCount - 1)

      agentDevices.forEach((device, j) => {
        const deviceX =
          deviceCount === 1 ? finalPos[0] : finalPos[0] - deviceSpread / 2 + j * deviceStep
        const deviceZ = finalPos[2] + Math.sin((j / Math.max(deviceCount - 1, 1)) * Math.PI) * 8
        const devicePos: [number, number, number] = [deviceX, DEVICE_Y, deviceZ]

        const isActive = new Date(device.last_seen).getTime() > Date.now() - TWO_HOURS_MS
        const deviceId = `device-${device.server_id}-${device.agent_id}-${device.device_ip || device.device_name}`

        // Avoid duplicates
        if (nodeIdSet.has(deviceId)) return
        nodeIdSet.add(deviceId)

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

  // SNMP-walker devices (and any device whose agent_id doesn't match a real
  // agent) have no parent agent node — anchor them directly to the server
  // in a fan below it so the 3D view still renders them.
  const agentIdsByServer = new Map<string, Set<string>>()
  agents.forEach((a) => {
    const sid = `server-${a.server_id}`
    if (!agentIdsByServer.has(sid)) agentIdsByServer.set(sid, new Set())
    agentIdsByServer.get(sid)!.add(a.agent_id)
  })

  const serverOrphanDevices = new Map<string, SNMPDevice[]>()
  snmpDevices.forEach((device) => {
    const sid = `server-${device.server_id}`
    if (!serverPositions.has(sid)) return
    if (agentIdsByServer.get(sid)?.has(device.agent_id)) return // already under a real agent
    if (!serverOrphanDevices.has(sid)) serverOrphanDevices.set(sid, [])
    serverOrphanDevices.get(sid)!.push(device)
  })

  serverOrphanDevices.forEach((devices, serverId) => {
    const parentPos = serverPositions.get(serverId)
    if (!parentPos) return
    const serverOnline = serverStatusMap.get(serverId) === 'online'

    const count = devices.length
    const spread = Math.max(count * 10, 20)
    const step = count === 1 ? 0 : spread / (count - 1)

    devices.forEach((device, j) => {
      const deviceId = `device-${device.server_id}-${device.agent_id}-${device.device_ip || device.device_name}`
      if (nodeIdSet.has(deviceId)) return
      nodeIdSet.add(deviceId)

      const dx = count === 1 ? parentPos[0] : parentPos[0] - spread / 2 + j * step
      const dz = parentPos[2] + Math.sin((j / Math.max(count - 1, 1)) * Math.PI) * 10
      const devicePos: [number, number, number] = [dx, DEVICE_Y, dz]

      const isActive = new Date(device.last_seen).getTime() > Date.now() - TWO_HOURS_MS

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
        agentName: device.agent_id,
        lastSeen: device.last_seen,
      })

      links.push({
        sourceId: deviceId,
        targetId: serverId,
        sourcePos: devicePos,
        targetPos: parentPos,
        connected: isActive && serverOnline,
        linkType: 'device-agent',
      })
    })
  })

  return { nodes, links }
}
