// Layered (multipartite) graph layout — NetworkX-style.
//
// Given a set of nodes, each pre-assigned a LAYER index (0 = top), and a set
// of directed edges, this module produces an (x, y) for every node such that:
//
//   * nodes in the same layer share the same y
//   * nodes are evenly spaced horizontally within their layer
//   * the order within each layer is chosen to minimize edge crossings
//     between adjacent layers (barycenter heuristic, the same sweep that
//     underpins Sugiyama and networkx's drawing helpers)
//
// The layout algorithm is intentionally library-free: it's ~2 sweeps *
// iterations over each layer, O(V + E) per sweep, deterministic, and gives
// the caller full control over layer assignment (which is the hard part of
// drawing data-center topologies — a pod-gw always belongs on the "pod-gw
// row", regardless of graph depth).

export interface LayeredInputNode {
  id: string
  /** 0 = top-most row; larger = lower in the diagram. */
  layer: number
}

export interface LayeredInputEdge {
  source: string
  target: string
}

export interface LayeredOptions {
  /** Horizontal spacing between adjacent nodes in the same layer. */
  nodeGapX?: number
  /** Vertical spacing between adjacent layers. */
  layerGapY?: number
  /** Barycenter sweep iterations (down + up = 1 iteration). */
  iterations?: number
  /** Horizontal center the widest layer lands on. */
  originX?: number
  /** Y of layer 0. */
  originY?: number
  /**
   * Minimum per-node horizontal span. A wide layer still gets at least
   * `minLayerSpan` pixels so very-large fan-outs don't compress into
   * overlapping icons. Defaults to `(count - 1) * nodeGapX`.
   */
  minLayerSpan?: number
}

export interface LayeredOutput {
  positions: Map<string, { x: number; y: number }>
  /** Ordered node ids per layer (post-barycenter sort). */
  layers: Map<number, string[]>
  bounds: { minX: number; maxX: number; minY: number; maxY: number }
}

/**
 * Compute a layered graph layout.
 *
 * Same-layer edges (e.g. a dashed peer link between two cluster routers) are
 * intentionally *ignored* for ordering purposes: they can't push a node out
 * of its own layer without collapsing the hierarchy. The caller should still
 * render them using the returned positions.
 */
export function computeLayeredLayout(
  inputNodes: LayeredInputNode[],
  inputEdges: LayeredInputEdge[],
  opts: LayeredOptions = {},
): LayeredOutput {
  const nodeGapX = opts.nodeGapX ?? 120
  const layerGapY = opts.layerGapY ?? 200
  const iterations = opts.iterations ?? 24
  const originX = opts.originX ?? 0
  const originY = opts.originY ?? 0

  // ── Bucket nodes by layer ────────────────────────────────────────────
  const layerOf = new Map<string, number>()
  const buckets = new Map<number, string[]>()
  inputNodes.forEach((n) => {
    layerOf.set(n.id, n.layer)
    if (!buckets.has(n.layer)) buckets.set(n.layer, [])
    buckets.get(n.layer)!.push(n.id)
  })

  const sortedLayerKeys = Array.from(buckets.keys()).sort((a, b) => a - b)

  // ── Neighbor indices (only for cross-layer edges) ────────────────────
  // `upFrom.get(id)` → ids one or more layers above.
  // `downFrom.get(id)` → ids one or more layers below.
  // We allow non-adjacent edges (e.g. server→device when no agent is
  // present) because the barycenter average doesn't care about layer
  // distance, only position.
  const upFrom = new Map<string, string[]>()
  const downFrom = new Map<string, string[]>()
  inputEdges.forEach((e) => {
    const ls = layerOf.get(e.source)
    const lt = layerOf.get(e.target)
    if (ls === undefined || lt === undefined) return
    if (ls === lt) return // same-layer: not useful for ordering
    const upper = ls < lt ? e.source : e.target
    const lower = ls < lt ? e.target : e.source
    if (!downFrom.has(upper)) downFrom.set(upper, [])
    downFrom.get(upper)!.push(lower)
    if (!upFrom.has(lower)) upFrom.set(lower, [])
    upFrom.get(lower)!.push(upper)
  })

  // ── Current position index within each layer (0..count-1) ─────────────
  const pos = new Map<string, number>()
  sortedLayerKeys.forEach((L) => {
    const arr = buckets.get(L)!
    arr.forEach((id, i) => pos.set(id, i))
  })

  const barycenter = (id: string, useUp: boolean): number => {
    const nbrs = useUp ? upFrom.get(id) : downFrom.get(id)
    if (!nbrs || nbrs.length === 0) return pos.get(id) ?? 0
    let sum = 0
    for (const n of nbrs) sum += pos.get(n) ?? 0
    return sum / nbrs.length
  }

  const resortLayer = (L: number, useUp: boolean): void => {
    const arr = buckets.get(L)!
    if (arr.length <= 1) return
    const bc = new Map<string, number>()
    arr.forEach((id) => bc.set(id, barycenter(id, useUp)))
    arr.sort((a, b) => {
      const ba = bc.get(a)!
      const bb = bc.get(b)!
      if (ba !== bb) return ba - bb
      // Stable tiebreak by id so identical barycenters don't oscillate
      // between sweeps.
      return a < b ? -1 : a > b ? 1 : 0
    })
    arr.forEach((id, i) => pos.set(id, i))
  }

  // ── Barycenter sweeps ────────────────────────────────────────────────
  // Classic Sugiyama: top-down, then bottom-up. Repeat until stable or
  // the iteration cap is hit. Each sweep is O(V + E).
  for (let i = 0; i < iterations; i++) {
    for (let li = 1; li < sortedLayerKeys.length; li++) {
      resortLayer(sortedLayerKeys[li], /* useUp */ true)
    }
    for (let li = sortedLayerKeys.length - 2; li >= 0; li--) {
      resortLayer(sortedLayerKeys[li], /* useUp */ false)
    }
  }

  // ── Priority-based X assignment ──────────────────────────────────────
  // Each node's preferred X is the barycenter of its up-neighbors (so a
  // pod-gw sits directly under the average X of the fabric-gws feeding
  // it). Then we sweep left→right to enforce a minimum gap of `nodeGapX`
  // between adjacent nodes within a layer, and left→right again to keep
  // the layer centered on `originX`.
  const preferred = new Map<string, number>()
  const layerOutputs = new Map<number, string[]>()

  sortedLayerKeys.forEach((L, li) => {
    const arr = buckets.get(L)!
    // Layer 0 has no "above" neighbors — just spread uniformly.
    if (li === 0) {
      const count = arr.length
      const span = Math.max(
        (count - 1) * nodeGapX,
        opts.minLayerSpan ?? 0,
      )
      const step = count === 1 ? 0 : span / (count - 1)
      const startX = originX - span / 2
      arr.forEach((id, i) => preferred.set(id, startX + i * step))
    } else {
      // Two-pass preferred-X:
      //   1. Connected nodes (≥1 up-neighbor) get the average X of those
      //      neighbors — pulls each node under its parents.
      //   2. Orphans (no up-neighbor on any layer above) get the median X
      //      of the connected nodes in this same layer. Without this they
      //      default to 0 and pile up on the far left of the canvas.
      const connected: string[] = []
      const orphans: string[] = []
      arr.forEach((id) => {
        const ups = upFrom.get(id)
        if (ups && ups.length > 0) {
          let sum = 0
          let hits = 0
          for (const u of ups) {
            const px = preferred.get(u)
            if (px !== undefined) {
              sum += px
              hits++
            }
          }
          if (hits > 0) {
            preferred.set(id, sum / hits)
            connected.push(id)
            return
          }
        }
        orphans.push(id)
      })
      if (orphans.length > 0) {
        let fallbackX = originX
        if (connected.length > 0) {
          const xs = connected.map((id) => preferred.get(id)!).sort((a, b) => a - b)
          fallbackX = xs[Math.floor(xs.length / 2)]
        }
        orphans.forEach((id) => preferred.set(id, fallbackX))
      }
    }

    // Sort by preferred-x, then separate with a left-to-right sweep that
    // enforces minimum gap. This is the Brandes & Köpf "horizontal
    // compaction" kernel in simplified form — much cheaper than the full
    // version but produces a clean readable diagram for our scale.
    const sorted = arr.slice().sort((a, b) => {
      const pa = preferred.get(a)!
      const pb = preferred.get(b)!
      if (pa !== pb) return pa - pb
      return (pos.get(a) ?? 0) - (pos.get(b) ?? 0)
    })

    const xs = new Map<string, number>()
    let lastX = -Infinity
    sorted.forEach((id) => {
      const want = preferred.get(id)!
      const x = Math.max(want, lastX + nodeGapX)
      xs.set(id, x)
      lastX = x
    })

    // Re-center the layer horizontally on originX.
    let minX = Infinity
    let maxX = -Infinity
    xs.forEach((x) => {
      if (x < minX) minX = x
      if (x > maxX) maxX = x
    })
    const mid = (minX + maxX) / 2
    const shift = originX - mid
    sorted.forEach((id) => xs.set(id, xs.get(id)! + shift))

    // Commit: save per-layer order AND final preferred X (used as the
    // up-neighbor position reference for the layer below).
    layerOutputs.set(L, sorted)
    sorted.forEach((id) => preferred.set(id, xs.get(id)!))
  })

  // ── Final position map + bounds ──────────────────────────────────────
  const positions = new Map<string, { x: number; y: number }>()
  let minX = Infinity
  let maxX = -Infinity
  let minY = Infinity
  let maxY = -Infinity

  // Y comes from the actual LAYER NUMBER (offset from the top-most layer),
  // not from the compact index in sortedLayerKeys. This way, callers can
  // leave gaps in the layer space (e.g. give routers layer 2 and LBs layer
  // 3, skipping nothing, or layer 2 and layer 4 with an intentional gap)
  // and those gaps show up as visible vertical spacing in the diagram.
  const minLayer = sortedLayerKeys[0] ?? 0
  sortedLayerKeys.forEach((L) => {
    const arr = layerOutputs.get(L)!
    const y = originY + (L - minLayer) * layerGapY
    arr.forEach((id) => {
      const x = preferred.get(id)!
      positions.set(id, { x, y })
      if (x < minX) minX = x
      if (x > maxX) maxX = x
      if (y < minY) minY = y
      if (y > maxY) maxY = y
    })
  })

  if (!isFinite(minX)) {
    minX = originX
    maxX = originX
    minY = originY
    maxY = originY
  }

  return {
    positions,
    layers: layerOutputs,
    bounds: { minX, maxX, minY, maxY },
  }
}
