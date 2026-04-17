import type { Agent, ServerConfig, SNMPDevice, TopologyLink } from './types'

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

export interface D2DInfo {
  sourceIp: string
  sourceName: string
  sourcePort?: number
  targetIp: string
  targetName: string
  targetPort?: string
  lastSeen: string
  sourceStatus?: 'online' | 'offline'
  targetStatus?: 'online' | 'offline'
}

export interface LayoutLink {
  sourceId: string
  targetId: string
  sourcePos: [number, number, number]
  targetPos: [number, number, number]
  connected: boolean
  linkType?: 'agent-server' | 'device-agent' | 'device-device'
  d2dInfo?: D2DInfo
}

export interface LayoutResult {
  nodes: LayoutNode[]
  links: LayoutLink[]
}

// Tier heights — one Y plane per logical layer:
//
//   Level 1: Server                         SERVER_Y =  20
//   Level 2: Agent                          AGENT_Y  =  -8   (28m below)
//   Level 3: Source devices (DEVICE_Y)               = -36   (any device that
//            appears as source_ip in topology_links, OR a device with no link
//            row at all — treated as a potential source)
//   Level 4: Target-only devices (DEVICE_Y − DEPTH_STEP_Y)
//                                                    = -64   (appears ONLY
//            as target_ip, never as source_ip — a leaf in the walk)
//
// This is the "source / target" split the user asked for: just two device
// tiers, regardless of BFS depth. A device that answers both roles (seen
// as both source and target in different rows) lands on the source tier,
// because it's a pass-through in the topology, not a leaf.
const SERVER_Y = 20
const AGENT_Y = -8
const DEVICE_Y = -36
const DEPTH_STEP_Y = 28
const SERVER_SPACING_MIN = 55
const TWO_HOURS_MS = 2 * 60 * 60 * 1000

export function computeHierarchicalLayout(
  servers: ServerConfig[],
  agents: Agent[],
  snmpDevices: SNMPDevice[] = [],
  topologyLinks: TopologyLink[] = []
): LayoutResult {
  const enabledServers = servers.filter((s) => s.enabled)
  const nodes: LayoutNode[] = []
  const links: LayoutLink[] = []
  const nodeIdSet = new Set<string>()

  // Map each device IP → newest topology_links.last_seen. Used to decide
  // online/offline status since the walker re-stamps the link rows every
  // sweep — so a stale link row means the device stopped responding.
  const linkLastSeenByIp = new Map<string, number>()
  // Lowest BFS depth for each device IP. Only depth-0 "seed" devices should
  // get a direct edge to the agent — deeper nodes reach the agent via their
  // device↔device chain. Otherwise every device draws a long edge up to the
  // agent and the diagram collapses into a hairball.
  const minDepthByIp = new Map<string, number>()
  topologyLinks.forEach((tl) => {
    const ts = new Date(tl.last_seen).getTime()
    if (!isNaN(ts)) {
      const prevS = linkLastSeenByIp.get(tl.source_ip)
      if (prevS === undefined || ts > prevS) linkLastSeenByIp.set(tl.source_ip, ts)
      const prevT = linkLastSeenByIp.get(tl.target_ip)
      if (prevT === undefined || ts > prevT) linkLastSeenByIp.set(tl.target_ip, ts)
    }
    const sd = Math.max(0, tl.source_depth ?? 0)
    const td = Math.max(0, tl.target_depth ?? 0)
    const prevSD = minDepthByIp.get(tl.source_ip)
    if (prevSD === undefined || sd < prevSD) minDepthByIp.set(tl.source_ip, sd)
    const prevTD = minDepthByIp.get(tl.target_ip)
    if (prevTD === undefined || td < prevTD) minDepthByIp.set(tl.target_ip, td)
  })
  const deviceActiveCutoff = Date.now() - TWO_HOURS_MS
  const isDeviceActive = (device: SNMPDevice): boolean => {
    const linkTs = device.device_ip ? linkLastSeenByIp.get(device.device_ip) : undefined
    const effectiveTs = linkTs ?? new Date(device.last_seen).getTime()
    return effectiveTs > deviceActiveCutoff
  }

  // Two-tier classifier from topology_links:
  //   tier 0 → level 3 (source devices + devices with no link row)
  //   tier 1 → level 4 (devices that appear ONLY as a target, never a source)
  // If an IP shows up as both source and target in different rows, it's a
  // pass-through and stays on tier 0 — only pure leaves drop to tier 1.
  const sourceIps = new Set<string>()
  const targetIps = new Set<string>()
  topologyLinks.forEach((tl) => {
    sourceIps.add(tl.source_ip)
    targetIps.add(tl.target_ip)
  })
  const deviceTierOf = (ip?: string): number => {
    if (!ip) return 0
    if (sourceIps.has(ip)) return 0
    if (targetIps.has(ip)) return 1
    return 0
  }

  // Orphan-agent fallback: agents whose server_id doesn't match any rendered
  // server attach to the first enabled server so their devices flow through
  // them (matches the 2D behavior). Without this, the SNMP Scanner's devices
  // would miss their agent parent and previously fell through to a
  // device→server edge — exactly what the user wants removed.
  const fallbackServerId: string | null =
    enabledServers.length > 0 ? `server-${enabledServers[0].id}` : null
  const agentEffectiveServer = new Map<string, string>() // agent_id → serverId
  agents.forEach((a) => {
    const desired = `server-${a.server_id}`
    if (agentEffectiveServer.has(a.agent_id)) return
    if (enabledServers.some((s) => `server-${s.id}` === desired)) {
      agentEffectiveServer.set(a.agent_id, desired)
    } else if (fallbackServerId) {
      agentEffectiveServer.set(a.agent_id, fallbackServerId)
    }
  })

  // Group agents by their EFFECTIVE server (post-fallback).
  const agentsByServer = new Map<string, Agent[]>()
  agents.forEach((agent) => {
    const sid = agentEffectiveServer.get(agent.agent_id)
    if (!sid) return // no servers at all → nothing to attach to
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

      // Position SNMP devices below this agent, grouped by topology depth.
      // Each depth tier gets its own Y plane — same-depth siblings fan in
      // Z (arc) and X, so parent→child edges now traverse a different
      // vertical level than sibling edges, eliminating the "all at same
      // level → overlapping lines" problem.
      const agentDevices = devicesByAgent.get(compoundId) || []
      if (agentDevices.length === 0) return

      // Split devices into two tiers: sources (level 3) and target-only
      // (level 4). Everything else stays on the source tier.
      const byTier = new Map<number, SNMPDevice[]>()
      agentDevices.forEach((d) => {
        const tier = deviceTierOf(d.device_ip)
        if (!byTier.has(tier)) byTier.set(tier, [])
        byTier.get(tier)!.push(d)
      })

      const sortedTiers = Array.from(byTier.keys()).sort((a, b) => a - b)
      sortedTiers.forEach((tier) => {
        const tierDevices = byTier.get(tier)!
        const tierCount = tierDevices.length
        const tierY = DEVICE_Y - tier * DEPTH_STEP_Y
        const tierSpread = Math.max(tierCount * 8, 15)
        const tierStep = tierCount === 1 ? 0 : tierSpread / (tierCount - 1)

        tierDevices.forEach((device, j) => {
          const deviceX =
            tierCount === 1 ? finalPos[0] : finalPos[0] - tierSpread / 2 + j * tierStep
          const deviceZ = finalPos[2] + Math.sin((j / Math.max(tierCount - 1, 1)) * Math.PI) * 8
          const devicePos: [number, number, number] = [deviceX, tierY, deviceZ]

          const isActive = isDeviceActive(device)
          const deviceId = `device-${device.server_id}-${device.agent_id}-${device.device_ip || device.device_name}`

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

          // Only true seed devices (BFS depth 0 in topology_links, or
          // devices with no link rows at all) connect directly to the agent.
          // Everything deeper is reached transitively through its
          // device↔device chain. The old "source-tier" gate wasn't strict
          // enough — in a Clos fabric most switches appear as source in at
          // least one row, so the agent would still be wired to hundreds of
          // devices.
          const devDepth = device.device_ip ? minDepthByIp.get(device.device_ip) : undefined
          const isSeed = devDepth === undefined || devDepth === 0
          if (isSeed) {
            links.push({
              sourceId: deviceId,
              targetId: compoundId,
              sourcePos: devicePos,
              targetPos: finalPos,
              connected: isActive,
              linkType: 'device-agent',
            })
          }
        })
      })
    })
  })

  // Orphan devices: snmp_devices whose agent_id doesn't resolve to any
  // rendered agent (e.g. the aggregator hasn't synced the virtual SNMP
  // Scanner agent yet). We STILL render them — under whichever server we
  // can identify — so the user sees every device that answered SNMP.
  // We do NOT wire them to the server: no device→server edges, matching
  // the 2D view. Their connectivity is expressed purely via the
  // device↔device `topology_links` pass below.
  const agentIdsByServer = new Map<string, Set<string>>()
  agents.forEach((a) => {
    const effSid = agentEffectiveServer.get(a.agent_id)
    if (!effSid) return
    if (!agentIdsByServer.has(effSid)) agentIdsByServer.set(effSid, new Set())
    agentIdsByServer.get(effSid)!.add(a.agent_id)
  })

  const serverOrphanDevices = new Map<string, SNMPDevice[]>()
  snmpDevices.forEach((device) => {
    // Resolve the device's effective server using the same orphan-fallback
    // rules as agents: prefer its own server_id, else first enabled server.
    const desiredSid = `server-${device.server_id}`
    const sid = serverPositions.has(desiredSid)
      ? desiredSid
      : fallbackServerId ?? desiredSid
    if (!serverPositions.has(sid)) return
    if (agentIdsByServer.get(sid)?.has(device.agent_id)) return // already under a real agent
    if (!serverOrphanDevices.has(sid)) serverOrphanDevices.set(sid, [])
    serverOrphanDevices.get(sid)!.push(device)
  })

  serverOrphanDevices.forEach((devices, serverId) => {
    const parentPos = serverPositions.get(serverId)
    if (!parentPos) return

    // Same two-tier split for orphans: source devices on level 3,
    // target-only devices on level 4.
    const byTier = new Map<number, SNMPDevice[]>()
    devices.forEach((d) => {
      const t = deviceTierOf(d.device_ip)
      if (!byTier.has(t)) byTier.set(t, [])
      byTier.get(t)!.push(d)
    })
    const sortedTiers = Array.from(byTier.keys()).sort((a, b) => a - b)
    sortedTiers.forEach((tier) => {
      const tierDevices = byTier.get(tier)!
      const count = tierDevices.length
      const tierY = DEVICE_Y - tier * DEPTH_STEP_Y
      const spread = Math.max(count * 10, 20)
      const step = count === 1 ? 0 : spread / (count - 1)

      tierDevices.forEach((device, j) => {
        const deviceId = `device-${device.server_id}-${device.agent_id}-${device.device_ip || device.device_name}`
        if (nodeIdSet.has(deviceId)) return
        nodeIdSet.add(deviceId)

        const dx = count === 1 ? parentPos[0] : parentPos[0] - spread / 2 + j * step
        const dz = parentPos[2] + Math.sin((j / Math.max(count - 1, 1)) * Math.PI) * 10
        const devicePos: [number, number, number] = [dx, tierY, dz]

        const isActive = isDeviceActive(device)

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

        // Intentionally NO device→server link here. Orphan devices render
        // as standalone nodes at their computed position; their connections
        // surface only via topology_links (device↔device edges below).
      })
    })
  })

  // Device↔device edges from topology_links (walker LLDP/CDP + discovery
  // deep-scan). Resolve each row's source_ip / target_ip to device nodes
  // we just positioned; drop rows that reference IPs not currently rendered.
  if (topologyLinks.length > 0) {
    const deviceByIp = new Map<string, LayoutNode>()
    nodes.forEach(n => {
      if (n.type === 'network' && n.ip) deviceByIp.set(n.ip, n)
    })
    const seenPairs = new Set<string>()
    topologyLinks.forEach(tl => {
      const srcN = deviceByIp.get(tl.source_ip)
      const tgtN = deviceByIp.get(tl.target_ip)
      if (!srcN || !tgtN || srcN.id === tgtN.id) return
      // Dedup bidirectional pairs.
      const pairKey = [srcN.id, tgtN.id].sort().join('|')
      if (seenPairs.has(pairKey)) return
      seenPairs.add(pairKey)
      const connected = srcN.status === 'online' && tgtN.status === 'online'
      links.push({
        sourceId: srcN.id,
        targetId: tgtN.id,
        sourcePos: srcN.position,
        targetPos: tgtN.position,
        connected,
        linkType: 'device-device',
        d2dInfo: {
          sourceIp: tl.source_ip,
          sourceName: tl.source_name,
          sourcePort: tl.source_port,
          targetIp: tl.target_ip,
          targetName: tl.target_name,
          targetPort: tl.target_port,
          lastSeen: tl.last_seen,
          sourceStatus: srcN.status,
          targetStatus: tgtN.status,
        },
      })
    })
  }

  return { nodes, links }
}
