// Maps OpenConfig / gNMI telemetry paths to human-readable labels.
//
// Telemetry keys arrive as deep paths like `interfaces/interface/state/name`
// (or the dotted form `interfaces.interface.state.name`). This module turns
// them into friendly text — e.g. "Interface name" — for the device detail UIs.
//
// Lookup is separator- and prefix-agnostic: it normalizes `.`/`/`, strips YANG
// module prefixes (`openconfig-if-ip:`) and list keys (`[name=Ethernet1]`)
// before matching, so the same map works regardless of how the path is encoded.

const PATH_LABELS: Record<string, string> = {
  // ── openconfig-interfaces ──────────────────────────────────────────────────
  'interfaces/interface/state/name': 'Interface name',
  'interfaces/interface/state/type': 'Interface type',
  'interfaces/interface/state/mtu': 'MTU',
  'interfaces/interface/state/admin-status': 'Admin status',
  'interfaces/interface/state/oper-status': 'Operational status',
  'interfaces/interface/state/counters/in-octets': 'Bytes received (HC)',
  'interfaces/interface/state/counters/out-octets': 'Bytes sent (HC)',
  'interfaces/interface/state/counters/in-unicast-pkts': 'Packets received unicast',
  'interfaces/interface/state/counters/out-unicast-pkts': 'Packets sent unicast',
  'interfaces/interface/state/counters/in-errors': 'Errors received',
  'interfaces/interface/state/counters/out-errors': 'Errors sent',
  'interfaces/interface/state/counters/in-discards': 'Discards received',
  'interfaces/interface/state/counters/out-discards': 'Discards sent',
  'interfaces/interface/state/counters/last-clear': 'Counter last-clear time',
  'interfaces/interface/ethernet/state/mac-address': 'MAC address',
  'interfaces/interface/ethernet/state/hw-mac-address': 'Hardware MAC address',
  'interfaces/interface/ethernet/state/port-speed': 'Speed',
  'interfaces/interface/ethernet/state/duplex-mode': 'Duplex mode',
  'interfaces/interface/subinterfaces/subinterface/state/admin-status': 'Subinterface admin status',
  'interfaces/interface/subinterfaces/subinterface/state/oper-status': 'Subinterface oper status',
  'interfaces/interface/subinterfaces/subinterface/ipv4/addresses/address/state/ip': 'IPv4 address',
  'interfaces/interface/subinterfaces/subinterface/ipv4/addresses/address/state/prefix-length': 'IPv4 prefix length',
  'interfaces/interface/subinterfaces/subinterface/ipv4/addresses/address/state/origin': 'IPv4 address origin',

  // ── openconfig-platform (temperature) ──────────────────────────────────────
  'components/component/state/name': 'Component name',
  'components/component/state/type': 'Component type',
  'components/component/state/description': 'Component description',
  'components/component/state/temperature/instant': 'Temperature current (°C)',
  'components/component/state/temperature/avg': 'Temperature average',
  'components/component/state/temperature/min': 'Temperature minimum',
  'components/component/state/temperature/max': 'Temperature maximum',
  'components/component/state/temperature/alarm-status': 'Temperature alarm active',
  'components/component/state/temperature/alarm-severity': 'Temperature alarm severity',
  'components/component/state/temperature/alarm-threshold': 'Temperature alarm threshold (°C)',

  // ── openconfig-system ──────────────────────────────────────────────────────
  'system/state/hostname': 'Hostname',
  'system/state/domain-name': 'Domain name',
  'system/state/boot-time': 'Boot time',
  'system/state/uptime': 'Uptime',
  'system/state/software-version': 'Software version',
  'system/state/os-name': 'OS name',
  'system/memory/state/physical': 'Memory total bytes',
  'system/memory/state/reserved': 'Memory used bytes',
  'system/memory/state/free': 'Memory free bytes',
  'system/memory/state/utilized': 'Memory utilization (%)',
  'system/cpus/cpu/state/total/instant': 'CPU utilization current (%)',
  'system/cpus/cpu/state/total/avg': 'CPU utilization average (%)',
  'system/cpus/cpu/state/total/min': 'CPU utilization minimum (%)',
  'system/cpus/cpu/state/total/max': 'CPU utilization maximum (%)',

  // ── openconfig-lldp ────────────────────────────────────────────────────────
  'lldp/state/enabled': 'LLDP enabled',
  'lldp/state/hello-timer': 'LLDP hello timer',
  'lldp/state/hold-multiplier': 'LLDP hold multiplier',
  'lldp/interfaces/interface/neighbors/neighbor/state/id': 'Neighbor id',
  'lldp/interfaces/interface/neighbors/neighbor/state/chassis-id': 'Neighbor chassis-id',
  'lldp/interfaces/interface/neighbors/neighbor/state/chassis-id-type': 'Neighbor chassis-id type',
  'lldp/interfaces/interface/neighbors/neighbor/state/port-id': 'Neighbor port-id',
  'lldp/interfaces/interface/neighbors/neighbor/state/port-id-type': 'Neighbor port-id type',
  'lldp/interfaces/interface/neighbors/neighbor/state/port-description': 'Neighbor port description',
  'lldp/interfaces/interface/neighbors/neighbor/state/system-name': 'Neighbor system name',
  'lldp/interfaces/interface/neighbors/neighbor/state/system-description': 'Neighbor system description',
  'lldp/interfaces/interface/neighbors/neighbor/state/management-address': 'Neighbor management address',
  'lldp/interfaces/interface/neighbors/neighbor/state/management-address-type': 'Neighbor management address type',

  // ── openconfig-network-instance — VLANs / FDB (switch) ─────────────────────
  'network-instances/network-instance/vlans/vlan/state/vlan-id': 'VLAN id',
  'network-instances/network-instance/vlans/vlan/state/name': 'VLAN name',
  'network-instances/network-instance/vlans/vlan/state/status': 'VLAN status',
  'network-instances/network-instance/fdb/mac-table/entries/entry/state/mac-address': 'FDB MAC address',
  'network-instances/network-instance/fdb/mac-table/entries/entry/state/vlan': 'FDB VLAN',
  'network-instances/network-instance/fdb/mac-table/entries/entry/state/entry-type': 'FDB entry type',
  'network-instances/network-instance/fdb/mac-table/entries/entry/state/age': 'FDB age',
  'network-instances/network-instance/fdb/mac-table/entries/entry/interface/interface-ref/state/interface': 'FDB entry interface',
  'network-instances/network-instance/fdb/state/mac-aging-time': 'MAC aging time',
  'network-instances/network-instance/fdb/state/mac-learning': 'MAC learning enabled',

  // ── openconfig-network-instance — BGP (router) ─────────────────────────────
  'network-instances/network-instance/protocols/protocol/bgp/global/state/as': 'Local AS',
  'network-instances/network-instance/protocols/protocol/bgp/global/state/router-id': 'BGP router id',
  'network-instances/network-instance/protocols/protocol/bgp/global/state/total-paths': 'Total paths',
  'network-instances/network-instance/protocols/protocol/bgp/global/state/total-prefixes': 'Total prefixes',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/neighbor-address': 'BGP neighbor address',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/peer-as': 'Neighbor peer AS',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/local-as': 'Neighbor local AS',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/session-state': 'Neighbor session state',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/established-transitions': 'Neighbor established transitions',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/messages/received': 'Neighbor messages received',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/state/messages/sent': 'Neighbor messages sent',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/timers/state/hold-time': 'Neighbor hold-time',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/timers/state/keepalive-interval': 'Neighbor keepalive interval',
  'network-instances/network-instance/protocols/protocol/bgp/neighbors/neighbor/timers/state/negotiated-hold-time': 'Neighbor negotiated hold-time',

  // ── openconfig-network-instance — OSPF (router) ────────────────────────────
  'network-instances/network-instance/protocols/protocol/ospfv2/global/state/router-id': 'OSPF router id',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/state/identifier': 'Area id',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/state/network-type': 'Interface network type',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/state/metric': 'Interface metric',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/neighbors/neighbor/state/neighbor-id': 'OSPF neighbor id',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/neighbors/neighbor/state/neighbor-address': 'OSPF neighbor address',
  'network-instances/network-instance/protocols/protocol/ospfv2/areas/area/interfaces/interface/neighbors/neighbor/state/adjacency-state': 'Neighbor adjacency state',

  // ── openconfig-network-instance — AFT (IPv4 routing table) ─────────────────
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/state/prefix': 'Route prefix',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/state/origin-protocol': 'Route origin protocol',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/state/metric': 'Route metric',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/next-hops/next-hop/state/index': 'Next-hop index',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/next-hops/next-hop/state/ip-address': 'Next-hop IP address',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/next-hops/next-hop/state/weight': 'Next-hop weight',
  'network-instances/network-instance/afts/ipv4-unicast/ipv4-entry/next-hops/next-hop/state/recurse': 'Next-hop recurse',
}

/** Normalize a telemetry path so it can be matched against PATH_LABELS. */
function normalizePath(raw: string): string {
  return raw
    .trim()
    .replace(/\[[^\]]*\]/g, '')   // drop list keys: interface[name=Ethernet1] → interface
    .replace(/[a-z0-9_-]+:/gi, '') // drop YANG module prefixes: openconfig-if-ip:ipv4 → ipv4
    .replace(/\./g, '/')          // dotted form → slashed form
    .replace(/\/+/g, '/')         // collapse repeated slashes
    .replace(/^\/|\/$/g, '')      // trim leading/trailing slash
    .toLowerCase()
}

/** Title-case the final segment of a path as a readable fallback. */
function prettifyLeaf(normalized: string): string {
  const leaf = normalized.split('/').pop() || normalized
  return leaf.replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase())
}

/**
 * Convert an OpenConfig / gNMI path (optionally split into a base `path` and a
 * leaf `key`) into a human-readable label. Falls back to a title-cased leaf for
 * paths not present in the map, so unknown telemetry still reads cleanly.
 */
export function getReadablePath(path: string, key?: string): string {
  // The backend sometimes carries the leaf inside `path` and sometimes splits it
  // out into `key`, so try the combined path first, then `path` on its own.
  const candidates = key ? [`${path}/${key}`, path] : [path]
  for (const candidate of candidates) {
    if (!candidate) continue
    const label = PATH_LABELS[normalizePath(candidate)]
    if (label) return label
  }
  const best = candidates.find(Boolean)
  return best ? prettifyLeaf(normalizePath(best)) : (key || path)
}
