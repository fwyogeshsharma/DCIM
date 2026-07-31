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
 * Device-type chips on the Live Metrics table. Brighter and more saturated
 * than NODE_COLOR because these are small text chips on a dark row rather
 * than large node bodies. Still a second palette for the same concept — a
 * candidate to fold into NODE_COLOR once both pages can be compared live.
 */
export const BADGE_COLOR: Record<string, string> = {
  switch:        'var(--badge-switch)',
  router:        'var(--badge-router)',
  server:        'var(--badge-server)',
  firewall:      'var(--badge-firewall)',
  load_balancer: 'var(--badge-lb)',
  ups:           'var(--badge-ups)',
  pdu:           'var(--badge-pdu)',
  floor_pdu:     'var(--badge-floor-pdu)',
  rpp:           'var(--badge-rpp)',
  generator:     'var(--badge-generator)',
  oob_switch:    'var(--badge-oob)',
  sensor:        'var(--badge-sensor)',
  utility_feed:  'var(--badge-utility-feed)',
  switchgear:    'var(--badge-switchgear)',
  ats:           'var(--badge-ats)',
  mcc:           'var(--badge-mcc)',
  mpp:           'var(--badge-mpp)',
}

export const BADGE_DEFAULT = 'var(--badge-default)'

export function badgeColor(deviceType: string): string {
  return BADGE_COLOR[deviceType] || BADGE_DEFAULT
}

/** Topology layer colour — what LinkEdge strokes with, and what the layer
 *  filter buttons use as their swatch, so legend and canvas agree. */
export const LAYER_COLOR: Record<string, string> = {
  production: 'var(--layer-prod)',
  management: 'var(--layer-mgmt)',
  power:      'var(--layer-power)',
  cooling:    'var(--layer-cooling)',
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
