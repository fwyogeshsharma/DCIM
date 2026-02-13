import type { Agent, ServerConfig } from './types'

export interface LayoutNode {
  id: string
  name: string
  type: 'server' | 'agent'
  status: 'online' | 'offline'
  position: [number, number, number]
  color: string
  ip?: string
  serverId?: string
  metrics?: number
  alerts?: number
}

export interface LayoutLink {
  sourceId: string
  targetId: string
  sourcePos: [number, number, number]
  targetPos: [number, number, number]
  connected: boolean
}

export interface LayoutResult {
  nodes: LayoutNode[]
  links: LayoutLink[]
}

const SERVER_Y = 20
const AGENT_Y = -8
const SERVER_SPACING = 55

export function computeHierarchicalLayout(
  servers: ServerConfig[],
  agents: Agent[]
): LayoutResult {
  const enabledServers = servers.filter((s) => s.enabled)
  const nodes: LayoutNode[] = []
  const links: LayoutLink[] = []

  // Position servers along X axis, centered at origin
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
    if (!agentsByServer.has(sid)) {
      agentsByServer.set(sid, [])
    }
    agentsByServer.get(sid)!.push(agent)
  })

  // Position agents in semicircular arcs below their parent server
  agentsByServer.forEach((serverAgents, serverId) => {
    const parentPos = serverPositions.get(serverId)
    if (!parentPos) return

    const count = serverAgents.length
    const arcRadius = Math.max(15, count * 6)

    serverAgents.forEach((agent, i) => {
      // Spread agents in a semicircular arc (PI radians) below the server
      const angle = count === 1
        ? Math.PI / 2
        : (Math.PI * (i + 1)) / (count + 1)

      const x = parentPos[0] + arcRadius * Math.cos(angle) * (i % 2 === 0 ? 1 : -1)
      const z = arcRadius * Math.sin(angle) * 0.5
      const agentPos: [number, number, number] = [
        parentPos[0] + (count === 1 ? 0 : arcRadius * Math.cos(angle) - arcRadius / 2),
        AGENT_Y,
        z,
      ]

      // For cleaner spreading, use even distribution
      const spread = arcRadius * 2
      const step = count === 1 ? 0 : spread / (count - 1)
      const agentX = count === 1
        ? parentPos[0]
        : parentPos[0] - spread / 2 + i * step
      const agentZ = Math.sin((i / Math.max(count - 1, 1)) * Math.PI) * (arcRadius * 0.4)

      const finalPos: [number, number, number] = [agentX, AGENT_Y, agentZ]

      const isConnected = agent.status === 'online' &&
        nodes.find((n) => n.id === serverId)?.status === 'online'

      nodes.push({
        id: agent.agent_id,
        name: agent.hostname,
        type: 'agent',
        status: agent.status === 'online' ? 'online' : 'offline',
        position: finalPos,
        color: agent.status === 'online' ? '#10b981' : '#ef4444',
        ip: agent.ip_address,
        serverId: agent.server_id,
        metrics: agent.total_metrics,
        alerts: agent.total_alerts,
      })

      links.push({
        sourceId: agent.agent_id,
        targetId: serverId,
        sourcePos: finalPos,
        targetPos: parentPos,
        connected: isConnected,
      })
    })
  })

  return { nodes, links }
}
