<script setup lang="ts">
// The home's «Ваши ролики»: a gallery narrowed first by Все / Клипы / Истории / Черновики /
// Готовые, then - under «Ещё фильтры» - by kind (storyboard, clip, graph), the models that shot them, the frame, the
// state; each choice with how many videos it would leave; found by name; sorted by when they changed, were made, by
// length or by name. The choices are kept for the next visit. A card shows its frame as it is (a vertical video stays
// vertical, on its own blurred frame), a ribbon in the colours of the models that shot it, each as long as its share
// of the scenes, its «Подача · Вид», and while «Сделать ролик» works on it the stage bar with «Остановить» (a stopped or
// failed one: «Продолжить»).
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import Icon from './Icon.vue'
import StageBar from './StageBar.vue'
import { api } from '../lib/api'
import { S, banner, live, loadProjects, modelColor, modelName } from '../lib/store'
import { liveAuto } from '../lib/project'
import { deliveryName, loadLooks, lookName } from '../lib/looks'
import { dur, when } from '../lib/format'

const { t } = useI18n()

type P = Record<string, any>
type Kind = 'all' | 'board' | 'clip' | 'graph'
type State = '' | 'ready' | 'unshot' | 'check' | 'draft' | 'busy'
type Main = 'all' | 'clips' | 'stories' | 'drafts' | 'ready'
const KEY = 'h3studio.shelf.v1'
const saved = (() => { try { return JSON.parse(localStorage.getItem(KEY) || '{}') } catch { return {} } })()
const main = ref<Main>(saved.main || 'all')
const kind = ref<Kind>(saved.kind || 'all')
const models = ref<string[]>(Array.isArray(saved.models) ? saved.models : [])
const aspect = ref<string>(saved.aspect || '')
const state = ref<State>(saved.state || '')
const sort = ref<string>(saved.sort || 'updated')
const q = ref('')
const open = ref(false)   // «Ещё фильтры»
watch([main, kind, models, aspect, state, sort], () => {
  try { localStorage.setItem(KEY, JSON.stringify({ main: main.value, kind: kind.value, models: models.value, aspect: aspect.value, state: state.value, sort: sort.value })) } catch { /* private window */ }
}, { deep: true })

// the work going on now: a project's running or queued jobs, the most telling first (the writer, then the shooting).
// Read through a key that changes only when that work does, so a jobs poll that changes nothing here redraws nothing.
const WORK: Record<string, string> = { write: 'writing', rewrite: 'writing', render: 'shooting', take: 'shooting',
  graph: 'building', 'graph-llm': 'writing', autopilot: 'shooting' }
const workKey = computed(() => S.jobs.filter((j) => live(j) && j.params?.project).map((j) => `${j.params!.project}\t${j.kind}`).sort().join('\n'))
const working = computed(() => {
  const m = new Map<string, string>(), rank = Object.keys(WORK)
  for (const line of workKey.value ? workKey.value.split('\n') : []) {
    const [pid, k] = line.split('\t'), was = m.get(pid)
    if (was === undefined || (rank.indexOf(k) + 1 || 99) < (rank.indexOf(was) + 1 || 99)) m.set(pid, k)
  }
  return m
})

// what a video is, worked out once per change of the list (or the model cards): the filters, the card and the counts
// all read this. Its state is this plus the work going on.
const KIND_NAME = ['board', 'manual', 'clip', 'graph']
const kindName = (k: string) => t(`shelf.kind.${KIND_NAME.includes(k) ? k : 'board'}`)
interface Fact { kind: Kind; models: string[]; aspect: string; state: State; ribbon: { id: string; pct: number; name: string; n: number }[]; line: string; title: string }
const facts = computed(() => new Map(S.projects.map((p): [string, Fact] => {
  const ms: P[] = p.models || []
  const total = ms.reduce((a, m) => a + (m.n || 0), 0)
  const ribbon = ms.map((m) => ({ id: m.id, pct: total ? (100 * m.n) / total : 100 / ms.length, name: modelName(m.id), n: m.n }))
  return [p.id, {
    kind: p.kind === 'graph' || p.kind === 'clip' ? p.kind : 'board', models: ms.map((m) => m.id), aspect: p.aspect || '16:9',
    state: p.video ? 'ready' : p.kind !== 'graph' && !p.written ? 'draft' : p.checks ? 'check' : 'unshot',
    ribbon, line: ribbon.map((r) => r.name).join(' + '),
    title: ribbon.map((r) => `${r.name}: ${t('shelf.scenesN', r.n)}`).join(', '),
  }]
})))
const fact = (p: P) => facts.value.get(p.id)!
const stateOf = (p: P): State => (working.value.has(p.id) ? 'busy' : fact(p).state)
const STATES: State[] = ['ready', 'unshot', 'check', 'draft', 'busy']
// a graph with nothing in it yet: «пустой граф», not «0:00 · 0 сцен»
const isEmpty = (p: P) => p.kind === 'graph' && !p.scenes && !p.seconds && !p.video
// the card's badge: its state in a word (a graph not yet put together says so)
function badge(p: P) {
  switch (stateOf(p)) {
    case 'busy': { const w = WORK[working.value.get(p.id)!]; return { cls: 'accent', text: w ? t(`shelf.work.${w}`) : t('shelf.work.busy') } }
    case 'ready': return { cls: '', text: t('shelf.badge.ready') }
    case 'draft': return { cls: 'warn', text: t('shelf.badge.draft') }
    case 'check': return { cls: 'warn', text: t('shelf.badge.check', { n: p.checks }) }
    default: return { cls: '', text: isEmpty(p) ? t('shelf.badge.empty') : p.kind === 'graph' ? t('shelf.badge.unbuilt') : t('shelf.badge.unshot') }
  }
}

// the filters in one pass: a video shown passes them all; one that fails a single filter still counts for that
// filter's other choices - so each choice says how many it would leave
type Facet = 'main' | 'kind' | 'models' | 'aspect' | 'state'
// the gallery's own filter: clips, stories (a storyboard from an idea or own prompts), drafts (no video yet), ready
const mainOf = (p: P, f: Fact): Main[] => ['all', ...(f.kind === 'clip' ? ['clips'] : f.kind === 'board' ? ['stories'] : []), p.video ? 'ready' : 'drafts'] as Main[]
const tally = computed(() => {
  const w = q.value.trim().toLowerCase()
  const n: Record<Facet, Record<string, number>> = { main: {}, kind: {}, models: {}, aspect: {}, state: {} }
  const shown: P[] = []
  for (const p of S.projects) {
    if (w && !(p.name || '').toLowerCase().includes(w)) continue
    const f = fact(p), st = stateOf(p)
    const mk = mainOf(p, f)
    const miss = ([
      ['main', mk.includes(main.value)],
      ['kind', kind.value === 'all' || f.kind === kind.value], ['models', !models.value.length || f.models.some((m) => models.value.includes(m))],
      ['aspect', !aspect.value || f.aspect === aspect.value], ['state', !state.value || st === state.value],
    ] as [Facet, boolean][]).filter(([, ok]) => !ok).map(([x]) => x)
    if (miss.length > 1) continue
    if (!miss.length) shown.push(p)
    const add = (x: Facet, keys: string[]) => { if (!miss.length || miss[0] === x) for (const k of keys) n[x][k] = (n[x][k] || 0) + 1 }
    add('main', mk); add('kind', ['all', f.kind]); add('models', f.models); add('aspect', [f.aspect]); add('state', [st])
  }
  return { n, shown }
})
const MAINS: Main[] = ['all', 'clips', 'stories', 'drafts', 'ready']
const mains = computed(() => MAINS.map((k) => ({ k, l: t(`home.filter.${k}`), n: tally.value.n.main[k] || 0 })))
const kinds = computed(() => (['all', 'board', 'clip', 'graph'] as Kind[])
  .map((k) => ({ k, l: t(`shelf.kinds.${k}`), n: tally.value.n.kind[k] || 0 })))
// the models: the ones the server knows that shoot (an imported Remiqora model counts as its own version), then any other
const modelList = computed(() => {
  const ids = (S.cfg?.video_models || []).filter((m) => !m.imported).map((m) => m.id)
  for (const f of facts.value.values()) for (const m of f.models) if (!ids.includes(m)) ids.push(m)
  return ids.map((id) => ({ id, name: modelName(id), n: tally.value.n.models[id] || 0 })).filter((m) => m.n || models.value.includes(m.id))
})
const toggleModel = (id: string) => { models.value = models.value.includes(id) ? models.value.filter((x) => x !== id) : [...models.value, id] }
const aspects = computed(() => ['16:9', '9:16', '1:1'].map((a) => ({ a, n: tally.value.n.aspect[a] || 0 })))
const states = computed(() => STATES.map((k) => ({ k, l: t(`shelf.state.${k}`), n: tally.value.n.state[k] || 0 })))
const SORTS = computed<[string, string][]>(() => ['updated', 'created', 'long', 'name'].map((k) => [k, t(`shelf.sort.${k}`)]))
// the filters under «Ещё фильтры» that are on (the gallery's own row shows itself)
const active = computed(() => (kind.value !== 'all' ? 1 : 0) + models.value.length + (aspect.value ? 1 : 0) + (state.value ? 1 : 0))
function reset() { main.value = 'all'; kind.value = 'all'; models.value = []; aspect.value = ''; state.value = ''; q.value = '' }

const BY: Record<string, (a: P, b: P) => number> = {
  updated: (a, b) => (b.updated || 0) - (a.updated || 0), created: (a, b) => (b.created || 0) - (a.created || 0),
  long: (a, b) => (b.seconds || 0) - (a.seconds || 0), name: (a, b) => (a.name || '').localeCompare(b.name || '', 'ru'),
}
const shown = computed(() => [...tally.value.shown].sort(BY[sort.value] || BY.updated))
const vertical = (p: P) => p.aspect === '9:16' || p.aspect === '1:1'
// «Подача · Вид» in effect (the server says them: GET /api/projects `looks`)
const looksLine = (p: P) => (p.looks ? `${deliveryName(p.looks.delivery)} · ${lookName(p.looks.look)}` : '')
// «Сделать ролик» on it: running, or stopped / failed short of the end
const autoOn = (p: P) => !!liveAuto(p.id) || !!(p.auto && !p.auto.finished && (p.auto.error || p.auto.stopped || p.auto.stage))
onMounted(() => { loadLooks().catch(() => {}) })

// what deleting takes away, said for what the video is
function goes(p: P) {
  const name = `«${p.name}»`
  if (p.kind === 'graph') return t('shelf.del.graph', { name })
  if (p.source === 'remiqora') return t('shelf.del.remiqora', { name })
  if (p.kind === 'clip') return t('shelf.del.clip', { name })
  return t('shelf.del.video', { name })
}
async function del(p: P) {
  if (!confirm(goes(p))) return
  try { await api(`/api/projects/${p.id}`, { method: 'DELETE' }) } catch (e) { banner((e as Error).message) }
  await loadProjects()
}
</script>

<template>
  <section class="stack shelf">
    <div class="shelf-head">
      <h2>{{ t('home.yours') }} <span>{{ S.projects.length }}</span></h2>
      <div class="chips shelf-main" role="radiogroup" :aria-label="t('shelf.whichShow')">
        <button v-for="x in mains" :key="x.k" type="button" role="radio" class="chip" :aria-checked="main === x.k" :class="{ on: main === x.k }"
          :disabled="!x.n && main !== x.k" @click="main = x.k">{{ x.l }}<small v-if="x.k !== 'all'">{{ x.n }}</small></button></div>
      <span class="grow" />
      <label class="shelf-find"><Icon name="search" :size="16" /><input v-model="q" type="search" :placeholder="t('shelf.find')" :aria-label="t('shelf.findLabel')"></label>
      <button type="button" class="btn shelf-more-btn" :aria-expanded="open" @click="open = !open"><Icon name="sliders" :size="16" />{{ t('home.moreFilters') }}<b v-if="active" class="bh-n">{{ active }}</b></button>
    </div>

    <div v-if="open" class="shelf-bar shelf-more open">
      <div class="seg shelf-kind" role="radiogroup" :aria-label="t('shelf.whichShow')">
        <button v-for="x in kinds" :key="x.k" type="button" role="radio" :aria-checked="kind === x.k" :class="{ on: kind === x.k }" :disabled="!x.n && kind !== x.k" @click="kind = x.k">
          {{ x.l }} <small>{{ x.n }}</small></button></div>
      <div class="shelf-models" role="group" :aria-label="t('shelf.models')">
        <button v-for="m in modelList" :key="m.id" type="button" class="mchip" :class="{ on: models.includes(m.id) }" :aria-pressed="models.includes(m.id)"
          :disabled="!m.n && !models.includes(m.id)" :style="{ '--m': modelColor(m.id) }" @click="toggleModel(m.id)">
          <i aria-hidden="true" />{{ m.name }} <small>{{ m.n }}</small></button></div>
      <div class="shelf-selects">
        <label class="sel"><span class="lbl">{{ t('shelf.frame') }}</span><select v-model="aspect"><option value="">{{ t('shelf.anyFrame') }}</option>
          <option v-for="x in aspects" :key="x.a" :value="x.a" :disabled="!x.n && aspect !== x.a">{{ x.a }} · {{ x.n }}</option></select></label>
        <label class="sel"><span class="lbl">{{ t('shelf.stateLbl') }}</span><select v-model="state"><option value="">{{ t('shelf.anyState') }}</option>
          <option v-for="x in states" :key="x.k" :value="x.k" :disabled="!x.n && state !== x.k">{{ x.l }} · {{ x.n }}</option></select></label>
        <label class="sel"><span class="lbl">{{ t('shelf.order') }}</span><select v-model="sort"><option v-for="[k, l] in SORTS" :key="k" :value="k">{{ l }}</option></select></label>
      </div>
    </div>
    <div v-if="active || main !== 'all' || q.trim()" class="shelf-found"><span>{{ shown.length ? t('shelf.foundOf', { found: t('shelf.videosN', shown.length), all: S.projects.length }) : t('shelf.nothing') }}</span>
      <button type="button" class="link-btn" @click="reset">{{ t('shelf.reset') }}</button></div>

    <div class="reels">
      <div v-for="p in shown" :key="p.id" class="reel" :class="{ working: autoOn(p) }">
        <router-link class="reel-link" :to="p.type === 'graph' ? `/g/${p.id}` : `/p/${p.id}`">
          <div class="pic" :class="{ tall: vertical(p) }">
            <template v-if="p.poster || p.photo">
              <img v-if="vertical(p)" class="pic-bg" :src="p.poster || p.photo" alt="">
              <img class="pic-img" :src="p.poster || p.photo" alt="" :style="p.poster ? 'opacity:1' : ''"></template>
            <span v-else class="pic-ic" aria-hidden="true"><Icon :name="p.kind === 'graph' ? 'layers' : 'film'" :size="34" /></span>
            <span class="badge reel-state" :class="badge(p).cls"><Icon :name="p.kind === 'graph' ? 'layers' : p.kind === 'clip' ? 'music' : 'film'" :size="12" />{{ badge(p).text }}</span>
            <span v-if="!isEmpty(p)" class="badge len">{{ dur(p.seconds || 0) }}<template v-if="p.kind !== 'graph' && p.video"> · {{ p.video }}</template><template v-else-if="p.aspect"> · {{ p.aspect }}</template></span>
            <span class="ribbon" :title="fact(p).title">
              <i v-for="r in fact(p).ribbon" :key="r.id" :style="{ width: `${r.pct}%`, background: modelColor(r.id) }" /></span>
          </div>
          <span class="name">{{ p.name }}</span>
          <span class="meta"><span>{{ kindName(p.kind) }}</span><span class="m-name">{{ fact(p).line }}</span><span v-if="p.source === 'remiqora'" :title="t('shelf.fromRemiqoraTitle')">{{ t('shelf.fromRemiqora') }}</span>
            <span v-if="!isEmpty(p)">{{ t('shelf.scenesN', p.scenes) }}</span><span>{{ when(sort === 'created' ? p.created : p.updated) }}</span></span>
        </router-link>
        <span v-if="looksLine(p)" class="reel-looks" :class="{ chosen: p.looks?.chosen }">{{ looksLine(p) }}</span>
        <StageBar v-if="autoOn(p)" class="reel-bar" compact :pid="p.id" :state="p.auto" />
        <button type="button" class="del" :aria-label="t('shelf.delLabel', { name: p.name })" :title="t('common.delete')" @click.prevent.stop="del(p)"><Icon name="trash" /></button>
      </div>
    </div>
    <p v-if="!shown.length" class="shelf-none">{{ t('shelf.none') }} <button type="button" class="link-btn" @click="reset">{{ t('shelf.showAll') }}</button></p>
  </section>
</template>

<style scoped>
.shelf-main .chip { min-height: 32px; padding: 0 14px; }
.shelf-main .chip small { margin-left: 4px; opacity: .7; font-size: 12px; }
.shelf-main .chip:disabled { opacity: .45; cursor: default; }
.shelf-more-btn { display: inline-flex; align-items: center; gap: 6px; }
.shelf-more { padding: 12px; border: 1px solid var(--rule); border-radius: 12px; }
.reel-link { display: flex; flex-direction: column; gap: 10px; text-decoration: none; color: inherit; }
.reel:hover .reel-link .pic { outline: 2px solid var(--rule-3); }
.reel.working .pic { outline: 2px solid color-mix(in srgb, var(--accent) 60%, transparent); }
.reel-looks { align-self: flex-start; padding: 2px 10px; border-radius: 99px; border: 1px solid color-mix(in srgb, var(--accent) 45%, transparent); color: var(--accent-hi); font-size: 12px; }
.reel-looks:not(.chosen) { border-style: dashed; }
@media (max-width: 760px) { .shelf-more { flex-direction: column; align-items: stretch; } }
</style>
