// «Упорядочить»: the graph laid out by its flow, left to right. A node's column is how far down the flow it is, and
// a node that only feeds sits right before what it feeds (a track by its music, a LoRA by its scenes); the order in a
// column keeps the links short (barycenters, a few sweeps both ways). A group is laid out inside and placed as one
// block, its frame fitted to it; notes go in a row above. Then the heights: a node moves up or down so that its output
// sits level with the input it feeds - one output into one input is a straight line (several: the middle one). Only
// positions (and group frames) change - the save is an undo step like any edit, so Ctrl+Z brings the old layout back.
import type { Node } from '@vue-flow/core'
import { GE, insideGroup, nodeOf, tOf } from './graphDoc'

export interface Size { w: number; h: number }
// a port's middle, down from its node's top (an output: 'source', an input: 'target')
export type PortOf = (id: string, handle: string, side: 'source' | 'target') => number
interface Item { id: string; w: number; h: number; y0: number; t: string }
// a link a -> b: a's output at `so` from a's top, b's input at `to` from b's top
interface Link { a: string; b: string; so: number; to: number }
// the video's tail: what the scenes go into. The rest - the chains that make the scenes - is laid out chain by chain
const TAIL = new Set(['join', 'montage', 'music', 'track', 'output'])
const ONE_STACK = 3200   // a head taller than this is wrapped into several stacks, about a screen's proportion
type Pos = Map<string, { x: number; y: number }>
export const GAP_X = 90
const GAP_Y = 40, NOTE_GAP = 60
const PAD = { x: 32, top: 56, bottom: 32 }   // a group's frame around its nodes (its title takes the top)

// items with links between them -> their top-left corners, the block from 0,0; and its size
function layered(items: Item[], links: Link[]): { pos: Pos; w: number; h: number } {
  const pos: Pos = new Map()
  if (!items.length) return { pos, w: 0, h: 0 }
  const by = new Map(items.map((i) => [i.id, i]))
  const succ = new Map(items.map((i) => [i.id, new Set<string>()])), pred = new Map(items.map((i) => [i.id, new Set<string>()]))
  const mine = links.filter((l) => l.a !== l.b && by.has(l.a) && by.has(l.b))
  for (const { a, b } of mine) { succ.get(a)!.add(b); pred.get(b)!.add(a) }

  // the flow's order (Kahn, the upper first); a cycle's leftovers go last as they lie
  const indeg = new Map(items.map((i) => [i.id, pred.get(i.id)!.size]))
  const ready = items.filter((i) => !indeg.get(i.id)).sort((a, b) => a.y0 - b.y0).map((i) => i.id)
  const order: string[] = []
  while (ready.length) {
    const v = ready.shift()!
    order.push(v)
    for (const w of succ.get(v)!) { indeg.set(w, indeg.get(w)! - 1); if (!indeg.get(w)) ready.push(w) }
  }
  for (const i of [...items].sort((a, b) => a.y0 - b.y0)) if (!order.includes(i.id)) order.push(i.id)

  // columns: the longest way from a source; then a feeder moves right up to just before what it feeds
  const col = new Map<string, number>()
  for (const v of order) col.set(v, Math.max(0, ...[...pred.get(v)!].map((u) => (col.get(u) ?? -1) + 1)))
  for (const v of [...order].reverse()) {
    const next = [...succ.get(v)!].map((w) => col.get(w)!)
    if (next.length) col.set(v, Math.max(col.get(v)!, Math.min(...next) - 1))
  }
  const n = Math.max(...col.values()) + 1
  const cols: string[][] = Array.from({ length: n }, () => [])
  for (const i of [...items].sort((a, b) => a.y0 - b.y0)) cols[col.get(i.id)!].push(i.id)

  // the order in a column: where its links lead (their middle), a few sweeps right and left
  const mid = new Map<string, number>()
  const stack = (c: string[]) => { let y = 0; for (const id of c) { mid.set(id, y + by.get(id)!.h / 2); y += by.get(id)!.h + GAP_Y } }
  cols.forEach(stack)
  const sweep = (c: string[], near: Map<string, Set<string>>) => {
    const key = (id: string) => { const ys = [...near.get(id)!].map((x) => mid.get(x)!).filter((y) => y !== undefined); return ys.length ? ys.reduce((a, b) => a + b) / ys.length : mid.get(id)! }
    const keys = new Map(c.map((id) => [id, key(id)]))
    c.sort((a, b) => keys.get(a)! - keys.get(b)!)
    stack(c)
  }
  for (let k = 0; k < 4; k++) {
    for (let i = 1; i < n; i++) sweep(cols[i], pred)
    for (let i = n - 2; i >= 0; i--) sweep(cols[i], succ)
  }

  // the heights: first each column stacked, centred on the tallest; then right to left, a node takes the height that
  // puts its output level with the input it feeds (one in the next column first; several - the middle one), and left
  // to right, a node that feeds nothing here takes its feeder's. A column keeps its order and its gaps (place)
  const heights = cols.map((c) => c.reduce((a, id) => a + by.get(id)!.h, 0) + GAP_Y * Math.max(0, c.length - 1))
  const H0 = Math.max(...heights)
  const top = new Map<string, number>()
  cols.forEach((c, i) => { let y = (H0 - heights[i]) / 2; for (const id of c) { top.set(id, y); y += by.get(id)!.h + GAP_Y } })
  const outs = new Map(items.map((i) => [i.id, [] as Link[]])), ins = new Map(items.map((i) => [i.id, [] as Link[]]))
  for (const l of mine) if (col.get(l.b)! > col.get(l.a)!) { outs.get(l.a)!.push(l); ins.get(l.b)!.push(l) }   // a cycle's way back: none
  // the link to line up with: the nearest column's first, the middle one by height
  const pick = (ls: Link[], far: (l: Link) => number, y: (l: Link) => number) => {
    const near = Math.min(...ls.map(far)), c = ls.filter((l) => far(l) === near).sort((p, q) => y(p) - y(q))
    return c[(c.length - 1) >> 1]
  }
  // a column's wanted tops (and how much each wants it) -> tops in the column's order, GAP_Y apart, as near the wanted
  // as can be: nodes that would overlap are pushed together into a block, and the block sits at their weighted median -
  // so one of them is exactly where it wants (a straight line), not all of them a little off as a mean would leave
  const place = (c: string[], want: (id: string) => [number, number]) => {
    interface B { ids: string[]; tops: [number, number][]; h: number; top: number }   // tops: the block's top each node wants, its weight
    const median = (ts: [number, number][]) => {
      const s = [...ts].sort((p, q) => p[0] - q[0]), half = s.reduce((a, t) => a + t[1], 0) / 2
      let acc = 0
      for (const [y, w] of s) if ((acc += w) >= half) return y
      return s[s.length - 1][0]
    }
    const bs: B[] = []
    for (const id of c) {
      const [y, w] = want(id)
      let b: B = { ids: [id], tops: [[y, w]], h: by.get(id)!.h, top: y }
      while (bs.length && bs[bs.length - 1].top + bs[bs.length - 1].h + GAP_Y > b.top) {
        const p = bs.pop()!, off = p.h + GAP_Y   // b's nodes go after p's: each one off lower in the merged block
        const tops: [number, number][] = [...p.tops, ...b.tops.map(([t, w]) => [t - off, w] as [number, number])]
        b = { ids: [...p.ids, ...b.ids], tops, h: off + b.h, top: median(tops) }
      }
      bs.push(b)
    }
    for (const b of bs) { let y = b.top; for (const id of b.ids) { top.set(id, y); y += by.get(id)!.h + GAP_Y } }
  }
  const FREE = 0.001, KEEP = 100   // no wish: stays where it is unless pushed; lined up already: hardly moves
  for (let i = n - 2; i >= 0; i--) place(cols[i], (id) => {
    const ls = outs.get(id)!
    if (!ls.length) return [top.get(id)!, FREE]
    const l = pick(ls, (x) => col.get(x.b)!, (x) => top.get(x.b)! + x.to)
    // a link of the chain itself (a scene into the next one) goes straight before a feeder's (a prompt into its scene)
    return [top.get(l.b)! + l.to - l.so, ins.get(id)!.length ? 2 : 1]
  })
  for (let i = 1; i < n; i++) place(cols[i], (id) => {
    const ls = ins.get(id)!, feeds = outs.get(id)!.length
    if (feeds || !ls.length) return [top.get(id)!, feeds ? KEEP : FREE]
    const l = pick(ls, (x) => -col.get(x.a)!, (x) => top.get(x.a)! + x.so)
    return [top.get(l.a)! + l.so - l.to, 1]
  })

  // the coordinates: columns as wide as their widest, the block from y 0
  const y0 = Math.min(...top.values())
  let x = 0, H = 0
  cols.forEach((c) => {
    for (const id of c) { const y = top.get(id)! - y0; pos.set(id, { x, y }); H = Math.max(H, y + by.get(id)!.h) }
    x += Math.max(...c.map((id) => by.get(id)!.w)) + GAP_X
  })
  return { pos, w: x - GAP_X, h: H }
}

// the flow of a block: the chains that make scenes (its head) each laid out on its own, stacked in the order they
// were and wrapped into columns when tall - a hundred scenes into one join made a tower before; the tail (join,
// montage, music, track, output) laid out to their right, in the middle
function flowLayout(items: Item[], links: Link[]): { pos: Pos; w: number; h: number } {
  const head = items.filter((i) => !TAIL.has(i.t)), tail = items.filter((i) => TAIL.has(i.t))
  // the head's chains: what is linked, the tail apart
  const root = new Map(head.map((i) => [i.id, i.id]))
  const find = (x: string): string => (root.get(x) === x ? x : (root.set(x, find(root.get(x)!)), root.get(x)!))
  for (const { a, b } of links) if (root.has(a) && root.has(b)) root.set(find(a), find(b))
  const chains = new Map<string, Item[]>()
  for (const i of head) { const r = find(i.id); chains.set(r, [...(chains.get(r) || []), i]) }
  if (!tail.length || chains.size < 2) return layered(items, links)

  const parts = [...chains.values()].map((c) => ({ y0: Math.min(...c.map((i) => i.y0)), ...layered(c, links) })).sort((a, b) => a.y0 - b.y0)
  const total = parts.reduce((a, p) => a + p.h, 0) + GAP_Y * (parts.length - 1)
  const area = parts.reduce((a, p) => a + (p.w + GAP_X) * (p.h + GAP_Y), 0)
  const maxH = total <= ONE_STACK ? total : Math.max(...parts.map((p) => p.h), Math.sqrt((area * 9) / 16))
  const pos: Pos = new Map()
  let x = 0, y = 0, colW = 0, H = 0
  for (const p of parts) {
    if (y > 0 && y + p.h > maxH) { x += colW + GAP_X * 1.5; y = 0; colW = 0 }
    for (const [id, q] of p.pos) pos.set(id, { x: x + q.x, y: y + q.y })
    y += p.h + GAP_Y; colW = Math.max(colW, p.w); H = Math.max(H, y - GAP_Y)
  }
  const headW = x + colW
  const t = layered(tail, links)
  // the tail at the height where the middle of the links into its first column runs straight (none: in the middle)
  const into = links.filter((l) => pos.has(l.a) && t.pos.has(l.b))
  const first = Math.min(...into.map((l) => t.pos.get(l.b)!.x))
  const near = into.filter((l) => t.pos.get(l.b)!.x === first).sort((p, q) => pos.get(p.a)!.y + p.so - (pos.get(q.a)!.y + q.so))
  const mid = near[(near.length - 1) >> 1]
  const tx = headW + GAP_X * 2, ty = mid ? pos.get(mid.a)!.y + mid.so - t.pos.get(mid.b)!.y - mid.to : Math.max(0, (H - t.h) / 2)
  for (const [id, q] of t.pos) pos.set(id, { x: tx + q.x, y: ty + q.y })
  // the block from y 0 again (the tail may stand higher than the head's top)
  const y0 = Math.min(0, ty)
  if (y0 < 0) for (const p of pos.values()) p.y -= y0
  return { pos, w: tx + t.w, h: Math.max(H, ty + t.h) - y0 }
}

// a block of nodes: its notes in a row on top, the rest laid out by their links under them
function block(items: Item[], notes: Item[], links: Link[]): { pos: Pos; w: number; h: number } {
  const flow = flowLayout(items, links)
  const pos: Pos = new Map()
  let x = 0, top = 0
  for (const t of notes) { pos.set(t.id, { x, y: 0 }); x += t.w + GAP_Y; top = Math.max(top, t.h) }
  const dy = notes.length ? top + NOTE_GAP : 0
  for (const [id, p] of flow.pos) pos.set(id, { x: p.x, y: p.y + dy })
  return { pos, w: Math.max(flow.w, x - GAP_Y), h: dy + flow.h }
}

// lay out these nodes (a selection; null - the whole graph). sizeOf: a node's drawn size, portOf: where its ports are.
// -> how many moved
export function arrange(ids: string[] | null, sizeOf: (id: string) => Size, portOf: PortOf): number {
  const scope = (ids ? ids.map((id) => nodeOf(id)).filter(Boolean) as Node[] : GE.nodes)
  if (scope.length < 2) return 0
  const inScope = new Set(scope.map((n) => n.id))
  const item = (n: Node): Item => { const s = sizeOf(n.id); return { id: n.id, w: s.w, h: s.h, y0: n.position.y, t: tOf(n) } }
  const groups = scope.filter((n) => tOf(n) === 'group')
  // a node in two frames belongs to the smaller one
  const owner = new Map<string, Node>()
  const area = (g: Node) => ((g.data as any).w || 720) * ((g.data as any).h || 420)
  for (const g of [...groups].sort((a, b) => area(b) - area(a))) for (const m of insideGroup(g)) if (inScope.has(m.id) && tOf(m) !== 'group') owner.set(m.id, g)
  const top = (id: string) => owner.get(id)?.id ?? id   // what a node is in the outer layout: itself or its group
  const links: Link[] = GE.edges.filter((e) => inScope.has(e.source) && inScope.has(e.target))
    .map((e) => ({ a: e.source, b: e.target, so: portOf(e.source, e.sourceHandle || '', 'source'), to: portOf(e.target, e.targetHandle || '', 'target') }))

  // each group with nodes: laid out inside, the frame around it
  const inner = new Map<string, { pos: Pos; w: number; h: number }>()
  for (const g of groups) {
    const ms = scope.filter((n) => owner.get(n.id) === g)
    if (!ms.length) continue
    inner.set(g.id, block(ms.filter((n) => tOf(n) !== 'note').map(item), ms.filter((n) => tOf(n) === 'note').map(item), links))
  }
  // the outer layout: the free nodes, and each group as one block
  const outer: Item[] = [], notes: Item[] = []
  for (const n of scope) {
    if (owner.has(n.id)) continue
    if (tOf(n) === 'group') {
      const b = inner.get(n.id), d = n.data as any
      outer.push({ id: n.id, w: b ? b.w + 2 * PAD.x : d.w || 720, h: b ? b.h + PAD.top + PAD.bottom : d.h || 420, y0: n.position.y, t: 'group' })
    } else (tOf(n) === 'note' ? notes : outer).push(item(n))
  }
  // a node in a group: its port as far down the group's block as the node is
  const down = (id: string) => { const g = owner.get(id); return g ? PAD.top + (inner.get(g.id)?.pos.get(id)?.y ?? 0) : 0 }
  const outerLinks = links.map((l) => ({ a: top(l.a), b: top(l.b), so: down(l.a) + l.so, to: down(l.b) + l.to }))
  const all = block(outer, notes, outerLinks)

  // where the block goes: where the nodes were (their top-left corner)
  const x0 = Math.min(...scope.map((n) => n.position.x)), y0 = Math.min(...scope.map((n) => n.position.y))
  let moved = 0
  const put = (n: Node, x: number, y: number) => {
    x = Math.round(x); y = Math.round(y)
    if (n.position.x !== x || n.position.y !== y) { n.position = { x, y }; moved++ }
  }
  for (const n of scope) {
    if (owner.has(n.id)) continue
    const p = all.pos.get(n.id)
    if (!p) continue
    put(n, x0 + p.x, y0 + p.y)
    const b = inner.get(n.id)
    if (b) {
      const d = n.data as any
      d.w = Math.round(b.w + 2 * PAD.x)
      d.h = Math.round(b.h + PAD.top + PAD.bottom)
      for (const m of scope) if (owner.get(m.id) === n) { const q = b.pos.get(m.id)!; put(m, x0 + p.x + PAD.x + q.x, y0 + p.y + PAD.top + q.y) }
    }
  }
  return moved
}
