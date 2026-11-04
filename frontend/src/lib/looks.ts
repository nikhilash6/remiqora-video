// «Подача» and «Вид» (app/looks.py): the lists from /api/looks in the interface's language, loaded once per language,
// and «Мои виды». The choice travels as { delivery, look, look_own }; "auto" in either is «Решит сценарист».
import { reactive } from 'vue'
import { api, post } from './api'
import { currentLocale } from '../i18n'

export interface Delivery { id: string; name: string; hint: string; scene: [number, number] | null; inserts: number | null }
export interface Look { id: string; group: string; name: string; hint: string; live: boolean; tint: [string, string]; preview: boolean; face_tested: number | null; models?: string[] | null; needs_frame?: boolean }
export interface MyLook { id: string; name: string; base: string; own: string; group: 'mine'; preview: boolean }
export interface LookChoice { delivery: string; look: string; look_own: string }
export interface LookPick { delivery: string; look: string; why_ru?: string; why_en?: string; fallback?: boolean }

export const AUTO = 'auto'
export const L = reactive({
  deliveries: [] as Delivery[],
  groups: [] as { id: string; name: string }[],
  looks: [] as Look[],
  mine: [] as MyLook[],
  hidden: [] as { id: string; name: string }[],   // not offered, but a stored plan may have one
  lang: '',
})

let loading: Promise<void> | null = null
export function loadLooks(force = false): Promise<void> {
  const lang = currentLocale()
  if (!force && L.lang === lang && L.looks.length) return Promise.resolve()
  if (loading && !force) return loading
  loading = api('/api/looks').then((r) => {
    L.deliveries = r.deliveries; L.groups = r.groups; L.looks = r.looks; L.mine = r.mine; L.hidden = r.hidden || []; L.lang = lang
  }).finally(() => { loading = null })
  return loading
}

// a tile's placeholder when the studio has not drawn its preview: the look's two colours and its group's sign
export const GLYPH: Record<string, string> = { auto: '✦', live: '☀', anim: '●', drawn: '✎', retro: '◐', genre: '☾', art: '◆', mine: '★' }

export const deliveryOf = (id: string) => L.deliveries.find((d) => d.id === id)
export const lookOf = (id: string): Look | undefined => {
  const own = L.mine.find((m) => m.id === id)
  if (own) {   // a base no longer offered keeps the saved look under its own name, with a plain tile
    const b = L.looks.find((x) => x.id === own.base) || { id: own.base, group: 'mine', name: own.name, hint: '', live: true, tint: ['#3a3a4a', '#6a6a7a'] as [string, string], preview: false, face_tested: null }
    return { ...b, id: own.id, name: own.name, group: 'mine', preview: own.preview }
  }
  return L.looks.find((x) => x.id === id)
}
export const deliveryName = (id: string) => deliveryOf(id)?.name || id
export const lookName = (id: string) => lookOf(id)?.name || L.hidden.find((x) => x.id === id)?.name || id
export const tileStyle = (x: Look | undefined) => x ? { background: `linear-gradient(160deg, ${x.tint[0]}, ${x.tint[1]})` } : {}
export const previewUrl = (id: string) => `/api/looks/preview/${encodeURIComponent(id)}`

// the home form's choice (store.ts Form: delivery, lookId, lookOwn) as the panel's v-model, and back
export const formChoice = (f: { delivery?: string; lookId?: string; lookOwn?: string }): LookChoice =>
  ({ delivery: f.delivery || AUTO, look: f.lookId || AUTO, look_own: f.lookOwn || '' })
export const toForm = (v: LookChoice) => ({ delivery: v.delivery, lookId: v.look, lookOwn: v.look_own })

// the writer's why in the interface's language
export const pickWhy = (p: LookPick | null | undefined) => (p ? (currentLocale() === 'en' ? p.why_en : p.why_ru) || '' : '')

export async function saveMine(name: string, base: string, own: string): Promise<MyLook> {
  const m = await post('/api/looks/mine', { name, base, own })
  await loadLooks(true)
  return m
}
export async function removeMine(id: string) {
  await api(`/api/looks/mine/${encodeURIComponent(id)}`, { method: 'DELETE' })
  await loadLooks(true)
}
