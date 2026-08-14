/**
 * Shared colour maps for anything TypeScript has to colour by hand.
 *
 * These hold `var(--x)` references, never hex — the values themselves live in
 * index.css `:root`, which stays the one place a colour is defined. Inline
 * `style` resolves custom properties normally, and React Flow forwards edge
 * strokes, node fills and the <Background> dot colour through `style` too, so
 * every consumer here works with var() as-is.
 *
 * Why this file exists: the device-type palette used to be copy-pasted into
 * four components and had drifted between them — floor PDUs and UPSes were
 * literally different colours on the topology canvas than in the device list
 * and status bar, so the legend disagreed with the drawing. Same story for the
 * layer palette, where the layer-filter buttons were tinted with values the
 * edge renderer never used. Import from here instead of re-declaring a map.
 */

/** Colour per `device_type`. Fall back to NODE_DEFAULT for unknown types. */
export const NODE_COLOR: Record<string, string> = {
  router:         'var(--node-router)',
  switch:         'var(--node-switch)',
  server:         'var(--node-server)',
  firewall:       'var(--node-firewall)',
  load_balancer:  'var(--node-lb)',
  oob_switch:     'var(--node-oob)',
  pdu:            'var(--node-pdu)',
  floor_pdu:      'var(--node-floor-pdu)',
  rpp:            'var(--node-rpp)',
  generator:      'var(--node-generator)',
  ups:            'var(--node-ups)',
  sensor:         'var(--node-sensor)',
  utility_feed:   'var(--node-utility-feed)',
  switchgear:     'var(--node-switchgear)',
  ats:            'var(--node-ats)',
  mcc:            'var(--node-mcc)',
  mpp:            'var(--node-mpp)',
  energy_monitor: 'var(--node-energy-monitor)',
  modbus_gateway: 'var(--node-modbus-gateway)',
  bacnet_router:  'var(--node-bacnet-router)',
  crah:           'var(--node-crah)',
  chiller:        'var(--node-chiller)',
  pump:           'var(--node-pump)',
  cooling_tower:  'var(--node-cooling-tower)',
  valve:          'var(--node-valve)',
  cdu:            'var(--node-cdu)',
}

export const NODE_DEFAULT = 'var(--node-default)'

export function nodeColor(deviceType: string): string {
  return NODE_COLOR[deviceType] || NODE_DEFAULT
}

/**
 * The same device-type colour, lightened enough to be legible AS TEXT.
 *
 * NODE_COLOR values are fills: dark enough to carry a white label, which makes
 * them far too dark to be a label themselves. Live Metrics used to keep a
 * separate, equally dark palette for its type chips and paint it as text —
 * PDU landed at 2.3:1 against the row, floor PDU 1.8, RPP 1.2. Ten of the
 * seventeen were effectively invisible.
 *
 * Deriving the text colour from the fill instead of maintaining a second
 * palette keeps one device-type identity across the canvas, the device list
 * and the metrics table, and keeps it correct in both roles.
 */
export function nodeInk(deviceType: string): string {
  return `color-mix(in srgb, ${nodeColor(deviceType)} 50%, white)`
}

/** Topology layer colour — what LinkEdge strokes with, and what the layer
 *  filter buttons use as their swatch, so legend and canvas agree. */
export const LAYER_COLOR: Record<string, string> = {
  production: 'var(--layer-prod)',
  management: 'var(--layer-mgmt)',
  power:      'var(--layer-power)',
  cooling:    'var(--layer-cooling)',
  fieldbus:   'var(--layer-fieldbus)',
}

export const LAYER_DEFAULT = 'var(--layer-prod)'

/** Cooling loop flow direction. */
export const FLOW_COLD = 'var(--flow-cold)'   // chilled supply / cooled condenser return
export const FLOW_HOT  = 'var(--flow-hot)'    // warm return / hot condenser water

/**
 * Trap / rule severity ramp. MIRRORS core/trap_definitions.py SEVERITY_COLOR —
 * a backend contract, not a styling choice. Change the Python first, then the
 * --sev-* tokens in index.css.
 */
export const SEVERITY_COLOR: Record<string, string> = {
  informational: 'var(--sev-informational)',
  minor:         'var(--sev-minor)',
  major:         'var(--sev-major)',
  critical:      'var(--sev-critical)',
}

/** UI status colours — a crossed threshold, a link down. Distinct from
 *  SEVERITY_COLOR, which reports what the simulator emitted. */
export const STATUS = {
  ok:   'var(--ok)',
  warn: 'var(--warn)',
  crit: 'var(--crit)',
  info: 'var(--info)',
} as const
