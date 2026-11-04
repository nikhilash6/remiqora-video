<script setup lang="ts">
// The writer's thinking while it writes (app/llm/client.py Thoughts -> job.progress.thinking), one calm card:
// the header says what the writer does now; with `title` (the board) it also holds the job's progress - the seconds,
// the bar and «ещё ~N с» («почти готово» once the estimate is over) - so the board needs no second box above it.
// Under it the steps as chips (Подача и вид › Сценарий › Места…: done ones checked, the current one lit), from the
// server's `parts`; then the thought text as light markdown (lib/thinkMd.ts), each step's own, the end kept in view
// unless the reader scrolled up («↓ к последнему» brings it back). The thoughts are the model's English, said once,
// not translated. Live: collapsible, the choice remembered in this browser. Done: one line «Подумал 18 с · 3 шага ▸»
// that opens the text again for this job. A cached answer has no live thinking: «Ответ из кэша».
// On the home's StageBar, the board while the writer works and a graph's writer node (`node`: only its own thinking;
// `compact`: fits the node's width).
import { computed, h, nextTick, onBeforeUnmount, ref, watch, type FunctionalComponent } from 'vue'
import { useI18n } from 'vue-i18n'
import type { Job } from '../lib/types'
import { mins } from '../lib/format'
import { parse, type Block, type Inline } from '../lib/thinkMd'
import Icon from './Icon.vue'

interface Part { step: string; at: number; secs: number; calls: number; cached: number; live: boolean; node?: string | null }
interface Thinking { step: string; text: string; live: boolean; secs: number; calls: number; cached: number; node?: string | null; parts?: Part[] }

const props = defineProps<{ job?: Job | null; node?: string; compact?: boolean; title?: string }>()
const { t, te, locale } = useI18n()

const th = computed<Thinking | null>(() => {
  const x = props.job?.progress?.thinking as Thinking | undefined
  if (!x || (props.node && x.node !== props.node && !(x.parts || []).some((p) => p.node === props.node))) return null
  return x
})
const jobLive = computed(() => !!props.job && (props.job.status === 'running' || props.job.status === 'queued'))
const liveNow = computed(() => !!th.value?.live && jobLive.value && (!props.node || th.value.node === props.node))
const onlyCached = computed(() => !!th.value && !th.value.calls && th.value.cached > 0)
const shown = computed(() => !!th.value && (liveNow.value || !!th.value.text || onlyCached.value))
// the board's card: the job's own progress in the header, shown while the job runs, thinking or not
const withJob = computed(() => !!props.title && jobLive.value)
const stepName = (s: string) => (te(`thinking.step.${s}`) ? t(`thinking.step.${s}`) : '')

// the steps and each one's text: `at` is where a step's thinking starts in the text; an older server sent no parts
const parts = computed(() => {
  const x = th.value
  if (!x) return []
  const all: Part[] = x.parts?.length ? x.parts : [{ step: x.step, at: 0, secs: x.secs, calls: x.calls, cached: x.cached, live: x.live, node: x.node }]
  return all.map((p, i) => ({ ...p, live: p.live && liveNow.value, text: x.text.slice(Math.max(0, p.at), i + 1 < all.length ? Math.max(0, all[i + 1].at) : undefined).trim() }))
    .filter((p) => !props.node || !p.node || p.node === props.node)
})
const chips = computed(() => {
  const out: { step: string; name: string; now: boolean }[] = []
  for (const p of parts.value) {
    const name = stepName(p.step)
    if (!name) continue
    const last = out[out.length - 1]
    if (last && last.step === p.step) last.now = last.now || p.live
    else out.push({ step: p.step, name, now: p.live })
  }
  return out
})
const curName = computed(() => (liveNow.value ? stepName(parts.value[parts.value.length - 1]?.step || th.value!.step) : ''))
const sections = computed(() => {
  const ps = parts.value
  return ps.map((p, i) => ({ key: `${i}-${p.step}`, name: stepName(p.step), live: p.live, old: liveNow.value && i < ps.length - 1, blocks: parse(p.text) as Block[] }))
    .filter((s) => s.blocks.length || s.live)
})
const named = computed(() => sections.value.length > 1)
const hasText = computed(() => !!th.value?.text?.trim())

// the inline runs of a block as text nodes (never v-html: the model's text is not trusted markup)
const Inl: FunctionalComponent<{ inl: Inline[] }> = (p) => p.inl.map((x) => (x.k === 't' ? x.v : h(x.k === 'b' ? 'strong' : x.k === 'i' ? 'em' : 'code', x.v)))

// live: the collapsed choice is this viewer's, kept in the browser; done: closed until clicked, for this job only
const KEY = 'remiqora_video_thinking_collapsed'
const collapsed = ref((() => { try { return localStorage.getItem(KEY) === '1' } catch { return false } })())
function toggleLive() {
  collapsed.value = !collapsed.value
  try { localStorage.setItem(KEY, collapsed.value ? '1' : '0') } catch { /* not remembered */ }
}
const reopened = ref(false)
watch(() => props.job?.id, () => { reopened.value = false })
const open = computed(() => (liveNow.value ? !collapsed.value : reopened.value))

// the seconds: the server's figures (the thinking's twice a second, the job's with each poll) and the local clock between
const now = ref(Date.now())
const seen = ref({ secs: 0, at: Date.now() })
const seenJob = ref({ secs: 0, at: Date.now() })
watch(() => th.value?.secs, (s) => { seen.value = { secs: s || 0, at: Date.now() } }, { immediate: true })
watch(() => props.job?.elapsed, (s) => { seenJob.value = { secs: s || 0, at: Date.now() } }, { immediate: true })
let tick: number | undefined
watch(jobLive, (on) => {
  if (tick) { clearInterval(tick); tick = undefined }
  if (on) tick = window.setInterval(() => { now.value = Date.now() }, 1000)
}, { immediate: true })
onBeforeUnmount(() => { if (tick) clearInterval(tick) })
const since = (x: { at: number }) => Math.max(0, (now.value - x.at) / 1000)
const secs = computed(() => Math.max(0, Math.round(seen.value.secs + (liveNow.value && parts.value[parts.value.length - 1]?.text ? since(seen.value) : 0))))
const elapsed = computed(() => seenJob.value.secs + (props.job?.status === 'running' ? since(seenJob.value) : 0))
const est = computed(() => props.job?.estimate || 0)
const pct = computed(() => (est.value ? Math.min(99, Math.round((100 * elapsed.value) / est.value)) : null))
const leftText = computed(() => {
  const left = est.value - elapsed.value
  return left > 2.5 ? t('thinking.left', { t: mins(left) }) : t('thinking.almost')
})

// the end of the text in view while it grows, unless the reader scrolled up to read
const box = ref<HTMLElement | null>(null)
const follow = ref(true)
const scrolled = ref(false)
function onScroll() {
  const b = box.value
  if (!b) return
  follow.value = b.scrollHeight - b.scrollTop - b.clientHeight < 24
  scrolled.value = b.scrollTop > 4
}
const calm = () => typeof matchMedia === 'function' && matchMedia('(prefers-reduced-motion: reduce)').matches
function toEnd() {
  const b = box.value
  if (!b) return
  follow.value = true
  b.scrollTo({ top: b.scrollHeight, behavior: calm() ? 'auto' : 'smooth' })
}
watch(() => [th.value?.text, open.value], async ([, o], old) => {
  await nextTick()
  const b = box.value
  if (!b) return
  if (follow.value || (o && !old?.[1])) { b.scrollTop = b.scrollHeight; follow.value = true }
  onScroll()
}, { immediate: true })

const doneLabel = computed(() => {
  if (onlyCached.value) return t(th.value?.text ? 'thinking.cachedSaved' : 'thinking.cached')
  const n = chips.value.length
  return t('thinking.done', { s: secs.value }) + (n > 1 ? ` · ${t('thinking.steps', n)}` : '')
})
const headTitle = computed(() => (withJob.value ? props.title! : props.compact && curName.value ? curName.value : t('thinking.live')))
</script>

<template>
  <section v-if="withJob || shown" class="wt" :class="{ compact, live: liveNow, card: withJob }" :aria-label="t('thinking.region')">
    <div v-if="withJob || liveNow" class="wt-head">
      <span class="wt-pulse" :class="{ on: jobLive }" aria-hidden="true" />
      <span class="wt-title"><b>{{ headTitle }}</b><span v-if="!withJob && !compact && curName && chips.length < 2" class="wt-cur">{{ curName }}</span></span>
      <span class="wt-secs">{{ withJob ? mins(elapsed) : t('thinking.secs', { s: secs }) }}</span>
      <button v-if="liveNow" type="button" class="wt-tog" :aria-expanded="open" :aria-label="open ? t('thinking.collapse') : t('thinking.expand')" @click="toggleLive">
        <svg class="wt-chev" :class="{ shut: !open }" viewBox="0 0 24 24" width="16" height="16" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
      </button>
    </div>

    <ol v-if="!compact && chips.length && (withJob || chips.length > 1) && (withJob || liveNow)" class="wt-chips" :aria-label="t('thinking.stepsLabel')">
      <li v-for="c in chips" :key="c.step" :class="{ now: c.now }" :aria-label="t(c.now ? 'thinking.stepNow' : 'thinking.stepDone', { name: c.name })">
        <Icon v-if="!c.now" name="check" :size="11" /><i v-else class="wt-cdot" aria-hidden="true" />{{ c.name }}</li>
    </ol>

    <div v-if="withJob && pct != null" class="wt-prog">
      <span class="wt-bar" role="progressbar" :aria-label="t('thinking.progress')" :aria-valuenow="pct" aria-valuemin="0" aria-valuemax="100"><i :style="{ width: `${pct}%` }" /></span>
      <small :class="{ almost: est - elapsed <= 2.5 }">{{ leftText }}</small>
    </div>

    <button v-if="th && !liveNow" type="button" class="wt-sum" :aria-expanded="open" :disabled="!hasText" @click="reopened = !reopened">
      <span>{{ doneLabel }}</span>
      <svg v-if="hasText" class="wt-chev" :class="{ shut: !open }" viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path d="M6 9l6 6 6-6" /></svg>
    </button>

    <div v-if="open && (hasText || liveNow)" class="wt-body">
      <p v-if="hasText" class="wt-lang">{{ t('thinking.english') }}</p>
      <div ref="box" class="wt-text nowheel nodrag" :class="{ fade: scrolled }" lang="en" @scroll.passive="onScroll">
        <div v-for="s in sections" :key="s.key" class="wt-sec" :class="{ old: s.old }">
          <h4 v-if="named" class="wt-sec-h" :lang="locale">{{ s.name }}</h4>
          <template v-for="(b, i) in s.blocks" :key="i">
            <p v-if="b.k === 'li'" class="wt-li" :class="{ ord: b.ordered }" :style="{ '--d': b.depth }"><span class="wt-mk" aria-hidden="true">{{ b.mark }}</span><span><Inl :inl="b.inl" /><i v-if="s.live && i === s.blocks.length - 1" class="wt-caret" aria-hidden="true" /></span></p>
            <p v-else :class="b.k === 'h' ? 'wt-h' : 'wt-p'"><Inl :inl="b.inl" /><i v-if="s.live && i === s.blocks.length - 1" class="wt-caret" aria-hidden="true" /></p>
          </template>
          <p v-if="!s.blocks.length" class="wt-wait" :lang="locale">{{ t('thinking.waiting') }}<i class="wt-caret" aria-hidden="true" /></p>
        </div>
      </div>
      <button v-if="!follow" type="button" class="wt-end" @click="toEnd"><span aria-hidden="true">↓</span> {{ t('thinking.toEnd') }}</button>
    </div>
  </section>
</template>

<style scoped>
.wt { position: relative; display: flex; flex-direction: column; gap: 8px; min-width: 0; padding: 10px 14px; border: 1px solid var(--rule); border-radius: 12px;
  background: color-mix(in srgb, var(--panel) 78%, transparent); color: var(--muted); font: 400 13px/1.4 var(--ui); }
/* the board's card: a touch of the brand on its top edge, the rest quiet */
.wt.card { gap: 10px; padding: 14px 16px 12px; border-radius: 14px; overflow: hidden;
  background: linear-gradient(180deg, color-mix(in srgb, var(--accent) 7%, var(--panel)), var(--panel) 72px); }
.wt.card::before { content: ""; position: absolute; inset: 0 0 auto; height: 1px; background: var(--grad); opacity: .55; }

.wt-head { display: flex; align-items: center; gap: 10px; min-width: 0; min-height: 28px; }
.wt-title { display: flex; align-items: baseline; flex-wrap: wrap; gap: 0 8px; min-width: 0; flex: 1; }
.wt-title b { font-weight: 500; color: var(--text); overflow-wrap: anywhere; }
.wt.card .wt-title b { font-size: 14.5px; }
.wt-cur { color: var(--muted); }
.wt-cur::before { content: "·"; margin-right: 8px; color: var(--faint); }
.wt-secs { flex: none; color: var(--faint); font-variant-numeric: tabular-nums; }
.wt-tog { flex: none; display: inline-flex; align-items: center; justify-content: center; width: 28px; height: 28px; margin: -4px -6px -4px 0; padding: 0;
  border: 0; border-radius: 8px; background: none; color: var(--faint); }
.wt-tog:hover { background: var(--hover); color: var(--text); }
.wt-chev { fill: none; stroke: currentColor; stroke-width: 2; stroke-linecap: round; stroke-linejoin: round; transition: transform .2s ease; }
.wt-chev.shut { transform: rotate(-90deg); }

/* the pulse: a small gradient dot breathing while the job runs */
.wt-pulse { position: relative; flex: none; width: 8px; height: 8px; border-radius: 50%; background: var(--rule-3); }
.wt-pulse.on { background: var(--grad); }
.wt-pulse.on::after { content: ""; position: absolute; inset: -4px; border-radius: 50%; border: 1px solid var(--accent); opacity: 0; animation: wt-ring 2.4s ease-out infinite; }

/* the steps: done ones checked, the current one lit */
.wt-chips { display: flex; flex-wrap: wrap; align-items: center; gap: 6px 4px; margin: 0; padding: 0; list-style: none; }
.wt-chips li { display: inline-flex; align-items: center; gap: 5px; padding: 3px 9px; border-radius: 999px; border: 1px solid var(--rule); color: var(--muted); font-size: 12px; line-height: 1.3; }
.wt-chips li { position: relative; }
.wt-chips li + li { margin-left: 14px; }
.wt-chips li + li::before { content: "›"; position: absolute; left: -12px; color: var(--faint); }
.wt-chips li :deep(.ic) { color: var(--ok-hi); opacity: .8; }
.wt-chips li.now { border-color: color-mix(in srgb, var(--accent) 55%, transparent); background: var(--accent-bg); color: var(--text); }
.wt-cdot { width: 6px; height: 6px; border-radius: 50%; background: var(--accent-hi); animation: wt-breathe 1.8s ease-in-out infinite; }

/* the job's progress */
.wt-prog { display: flex; align-items: center; gap: 12px; }
.wt-bar { flex: 1; min-width: 60px; height: 4px; border-radius: 2px; background: var(--rule); overflow: hidden; }
.wt-bar i { display: block; height: 100%; border-radius: 2px; background: var(--grad); transition: width .6s ease; }
.wt-prog small { flex: none; color: var(--faint); font-size: 12px; font-variant-numeric: tabular-nums; }
.wt-prog small.almost { color: var(--accent-hi); }

/* done: one quiet line */
.wt-sum { display: inline-flex; align-items: center; gap: 6px; align-self: flex-start; min-height: 24px; padding: 0; border: 0; background: none; color: var(--faint); font-size: 13px; text-align: left; }
.wt-sum:hover:not(:disabled) { color: var(--text); }
.wt-sum:disabled { cursor: default; }
.wt.card .wt-sum { padding-top: 2px; }

/* the thoughts */
.wt-body { position: relative; display: flex; flex-direction: column; gap: 6px; min-width: 0; }
.wt.card .wt-body { margin-top: 2px; padding-top: 10px; border-top: 1px solid var(--rule); }
.wt-lang { margin: 0; color: var(--faint); font-size: 11.5px; letter-spacing: .2px; }
.wt-text { max-height: min(22rem, 46vh); overflow-y: auto; overscroll-behavior: contain; padding-right: 6px; overflow-wrap: anywhere;
  color: var(--soft); font: 400 13.5px/1.65 var(--ui); scrollbar-width: thin; scrollbar-color: var(--rule-2) transparent; }
.wt-text::-webkit-scrollbar { width: 6px; }
.wt-text::-webkit-scrollbar-track { background: transparent; }
.wt-text::-webkit-scrollbar-thumb { background: var(--rule-2); border-radius: 3px; }
.wt-text::-webkit-scrollbar-thumb:hover { background: var(--rule-3); }
/* the older lines fade out at the top once there is more above */
.wt-text.fade { mask-image: linear-gradient(to bottom, transparent 0, #000 2.2em); -webkit-mask-image: linear-gradient(to bottom, transparent 0, #000 2.2em); }
.wt-sec { max-width: 70ch; }
.wt-sec + .wt-sec { margin-top: 12px; }
.wt-sec.old { color: var(--muted); }
.wt-sec-h { margin: 0 0 4px; color: var(--faint); font: 500 11px/1.4 var(--ui); letter-spacing: .6px; text-transform: uppercase; }
.wt-p, .wt-h, .wt-li, .wt-wait { margin: 0 0 6px; }
.wt-h { color: var(--text); font-weight: 600; margin-top: 4px; }
.wt-li { display: grid; grid-template-columns: 1.4em 1fr; gap: 0 4px; margin: 0 0 3px calc(var(--d, 0) * 1.3em); }
.wt-li.ord { grid-template-columns: 1.8em 1fr; }
.wt-mk { color: var(--faint); font-variant-numeric: tabular-nums; text-align: right; padding-right: 2px; }
.wt-li:not(.ord) .wt-mk { text-align: center; }
.wt-text :deep(strong) { color: var(--text); font-weight: 600; }
.wt-text :deep(em) { font-style: italic; }
.wt-text :deep(code) { padding: 0 4px; border-radius: 4px; background: var(--field); font: 12.5px/1.5 ui-monospace, Consolas, monospace; }
.wt-wait { color: var(--faint); font-style: italic; }
/* the caret at the end while it writes */
.wt-caret { display: inline-block; width: 7px; height: 1em; margin-left: 3px; vertical-align: -.15em; border-radius: 2px; background: var(--accent-hi); opacity: .7; animation: wt-blink 1.1s steps(1) infinite; }
.wt-end { position: absolute; right: 12px; bottom: 8px; display: inline-flex; align-items: center; gap: 4px; padding: 4px 10px; border: 1px solid var(--rule-2); border-radius: 999px;
  background: color-mix(in srgb, var(--deep) 92%, transparent); color: var(--soft); font-size: 12px; box-shadow: 0 4px 14px rgba(0, 0, 0, .35); }
.wt-end:hover { color: var(--text); border-color: var(--rule-3); }

/* a graph node's: the node's width, smaller type */
.wt.compact { gap: 5px; padding: 7px 9px; border-radius: 9px; font-size: 12px; }
.wt.compact .wt-head { gap: 7px; min-height: 22px; }
.wt.compact .wt-title b { font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.wt.compact .wt-tog { width: 22px; height: 22px; margin: -2px -4px -2px 0; }
.wt.compact .wt-pulse { width: 6px; height: 6px; }
.wt.compact .wt-sum { font-size: 12px; min-height: 20px; }
.wt.compact .wt-lang { font-size: 10.5px; }
.wt.compact .wt-text { max-height: 9.5em; font-size: 11.5px; line-height: 1.55; padding-right: 3px; }
.wt.compact .wt-sec-h { font-size: 10px; }
.wt.compact .wt-text :deep(code) { font-size: 10.5px; }
.wt.compact .wt-end { right: 4px; bottom: 4px; padding: 2px 8px; font-size: 11px; }

@media (max-width: 520px) {
  .wt.card { padding: 12px 12px 10px; }
  .wt-text { font-size: 13px; max-height: 50vh; }
}
@keyframes wt-ring { 0% { opacity: .55; transform: scale(.6); } 100% { opacity: 0; transform: scale(1.5); } }
@keyframes wt-breathe { 0%, 100% { opacity: .4; } 50% { opacity: 1; } }
@keyframes wt-blink { 0%, 55% { opacity: .7; } 56%, 100% { opacity: 0; } }
@media (prefers-reduced-motion: reduce) {
  .wt-pulse.on::after, .wt-cdot, .wt-caret { animation: none; }
  .wt-chev, .wt-bar i { transition: none; }
}
</style>
