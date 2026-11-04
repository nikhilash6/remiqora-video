<script setup lang="ts">
// A stop of «Сделать ролик» with «Спрашивать меня» (app/autopilot.py): «Выберите
// героя» - the tries with their face match to the photo, the one it would take marked, a countdown, «Попросить других
// кандидатов»; «Пробная сцена» - the scene shot, «Дальше» or «Сменить вид» (the look's panel; the wait is held while
// it is open, «Готово» saves the look and the trial is shot again). The answer goes to POST …/autopilot/answer; the
// job waits on the server, so closing the page loses nothing.
import { computed, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Icon from './Icon.vue'
import HelpModal from './help/HelpModal.vue'
import LookPanel from './LookPanel.vue'
import { api, post } from '../lib/api'
import { banner, pollJobs } from '../lib/store'
import type { LookChoice } from '../lib/looks'

const props = defineProps<{ pid: string; ask: Record<string, any> }>()
const emit = defineEmits<{ stop: []; fix: [] }>()
const { t } = useI18n()

// the countdown: the server's deadline (it is the same machine, or near enough for a minute)
const now = ref(Date.now() / 1000)
let tick: ReturnType<typeof setInterval> | undefined
onMounted(() => { tick = setInterval(() => { now.value = Date.now() / 1000 }, 500) })
onUnmounted(() => clearInterval(tick))
const left = computed(() => Math.max(0, Math.round((props.ask.until || 0) - now.value)))
const wait = computed(() => props.ask.wait || 60)
const ring = computed(() => `${Math.max(0, Math.min(1, left.value / wait.value)) * 100}`)

const sending = ref(false)
async function answer(body: Record<string, unknown>) {
  sending.value = true
  try { await post(`/api/projects/${props.pid}/autopilot/answer`, body); await pollJobs() } catch (e) { banner((e as Error).message) } finally { sending.value = false }
}

// the hero's tries
const tries = computed(() => (props.ask.tries || []) as { file: string; faces?: number; sim?: number | null }[])
const fileUrl = (f: string) => `/api/projects/${props.pid}/files/${encodeURIComponent(f)}`
function note(x: { faces?: number; sim?: number | null }) {
  if (x.sim != null) return t('home.match', { p: Math.round(x.sim * 100) })
  return x.faces === 0 ? t('home.noFace') : x.faces && x.faces > 1 ? t('home.twoFaces') : t('home.oneFace')
}

// the trial: «Сменить вид» holds the wait (the GPU queue waits with it) and opens the panel with the project's look;
// closing the panel, leaving the page or closing it lets the job go on («Дальше») - a hold is never left behind
const lookOpen = ref(false)
const draft = ref<LookChoice>({ delivery: 'auto', look: 'auto', look_own: '' })
let was: LookChoice | null = null
async function openLook() {
  await answer({ hold: true })
  try {
    const p = await api(`/api/projects/${props.pid}`)
    draft.value = { delivery: p.looks?.delivery || 'auto', look: p.looks?.look || 'auto', look_own: p.looks?.look_own || '' }
  } catch { /* the panel opens on «Решит сценарист» */ }
  was = { ...draft.value }
  lookOpen.value = true
}
const same = (a: LookChoice, b: LookChoice | null) => !!b && a.delivery === b.delivery && a.look === b.look && (a.look_own || '') === (b.look_own || '')
async function applyLook() {
  lookOpen.value = false
  await answer(same(draft.value, was) ? { go: true } : { ...draft.value })   // the same choice: nothing to rebuild
}
function closeLook() {
  if (!lookOpen.value) return
  lookOpen.value = false
  answer({ go: true })
}
// the page going away with the panel open: the answer by a beacon (it survives the unload)
function release() {
  if (!lookOpen.value) return
  lookOpen.value = false
  try { navigator.sendBeacon(`/api/projects/${props.pid}/autopilot/answer`, new Blob([JSON.stringify({ go: true })], { type: 'application/json' })) } catch { /* the hold ends by itself */ }
}
onMounted(() => addEventListener('pagehide', release))
onUnmounted(() => { removeEventListener('pagehide', release); release() })
</script>

<template>
  <section class="ask" :class="`is-${ask.kind}`" role="region" :aria-label="ask.kind === 'hero' ? t('home.heroTitle') : t('home.trialTitle')">
    <div class="ask-head">
      <div><h2>{{ ask.kind === 'hero' ? t('home.heroTitle') : t('home.trialTitle') }}</h2>
        <p v-if="ask.kind === 'hero'">{{ ask.photo ? t('home.heroPhoto', { n: tries.length }) : t('home.heroWords', { n: tries.length }) }}</p>
        <p v-else>{{ t('home.trialAbout', { n: (ask.scene ?? 0) + 1 }) }}</p></div>
      <div class="ask-clock" :title="ask.held ? t('home.held') : ''">
        <svg viewBox="0 0 36 36" aria-hidden="true"><circle cx="18" cy="18" r="15.9" class="trk" /><circle cx="18" cy="18" r="15.9" class="arc" :stroke-dasharray="`${ring} 100`" /></svg>
        <b>{{ left }}</b>
        <small>{{ ask.held ? t('home.held') : t('home.countdown', { s: Math.round(wait), what: ask.kind === 'hero' ? (ask.best ? t('home.takeMarked') : t('home.keepPhoto')) : t('home.goOn') }) }}</small>
      </div>
    </div>

    <div class="ask-body">
      <div v-if="ask.kind === 'hero'" class="ask-tries">
        <button v-for="x in tries" :key="x.file" type="button" class="ask-try" :class="{ best: x.file === ask.best }" :disabled="sending" @click="answer({ pick: x.file })">
          <img :src="fileUrl(x.file)" alt="">
          <span v-if="x.file === ask.best" class="ask-mark">{{ t('home.takeMark') }}</span>
          <span class="ask-note" :class="{ good: x.sim != null && x.sim >= 0.5 }">{{ note(x) }}</span>
        </button>
      </div>
      <div v-else class="ask-trial">
        <video :src="fileUrl(ask.file)" controls autoplay muted loop playsinline />
      </div>

      <aside class="ask-next">
        <b>{{ t('home.nextTitle') }}</b>
        <p v-if="ask.kind === 'hero'">{{ ask.best ? t('home.heroNext', { s: Math.round(wait) }) : t('home.heroNextNone', { s: Math.round(wait) }) }}</p>
        <div v-else class="ask-acts">
          <button type="button" class="btn grad" :disabled="sending" @click="answer({ go: true })"><Icon name="play" :size="13" />{{ t('home.go') }}</button>
          <button type="button" class="btn" :disabled="sending" @click="openLook"><Icon name="brush" :size="13" />{{ t('home.changeLook') }}</button>
        </div>
        <div class="ask-acts">
          <button type="button" class="btn danger-line" @click="emit('stop')"><Icon name="stop" :size="13" />{{ t('home.stop') }}</button>
          <button type="button" class="btn" @click="emit('fix')"><Icon name="pen" :size="13" />{{ t('home.openFix') }}</button>
        </div>
        <button v-if="ask.kind === 'hero'" type="button" class="link-btn" :disabled="sending" @click="answer({ more: true })"><Icon name="retake" :size="13" />{{ t('home.moreTries') }}</button>
        <small class="muted">{{ t('home.selfHint') }}</small>
      </aside>
    </div>

    <HelpModal :open="lookOpen" :title="t('home.lookTitle')" icon="spark" wide @close="closeLook">
      <LookPanel v-model="draft" :video="ask.video" compact />
      <div class="lpk-foot">
        <button type="button" class="link-btn" @click="closeLook">{{ t('home.cancel') }}</button>
        <button type="button" class="btn grad" @click="applyLook">{{ t('home.apply') }}</button>
      </div>
    </HelpModal>
  </section>
</template>

<style scoped>
.ask { display: flex; flex-direction: column; gap: 14px; padding: 18px 20px; border: 1.5px solid var(--accent); border-radius: 16px; background: var(--panel); }
.ask-head { display: flex; gap: 16px; align-items: flex-start; justify-content: space-between; flex-wrap: wrap; }
.ask-head h2 { margin: 0 0 4px; font-size: 22px; }
.ask-head p { margin: 0; color: var(--soft); font-size: 14px; max-width: 70ch; }
.ask-clock { display: grid; justify-items: end; gap: 4px; position: relative; min-width: 120px; }
.ask-clock svg { width: 54px; height: 54px; transform: rotate(-90deg); }
.ask-clock .trk { fill: none; stroke: var(--rule-2); stroke-width: 3; }
.ask-clock .arc { fill: none; stroke: var(--accent); stroke-width: 3; stroke-linecap: round; transition: stroke-dasharray .5s linear; }
.ask-clock b { position: absolute; top: 0; right: 0; width: 54px; height: 54px; display: grid; place-items: center; font-size: 17px; font-variant-numeric: tabular-nums; }
.ask-clock small { color: var(--faint); font-size: 12px; text-align: right; max-width: 220px; }
.ask-body { display: flex; flex-wrap: wrap; gap: 16px; align-items: flex-start; }
.ask-body > :first-child { flex: 999 1 340px; min-width: 0; }
.ask-next { flex: 1 1 260px; }
.ask-tries { display: grid; grid-template-columns: repeat(auto-fill, minmax(160px, 1fr)); gap: 12px; }
.ask-try { position: relative; display: flex; flex-direction: column; padding: 0; overflow: hidden; border: 1px solid var(--rule-2); border-radius: 12px; background: var(--panel-2, var(--panel)); cursor: pointer; text-align: left; color: inherit; }
.ask-try:hover:not(:disabled) { border-color: var(--accent-hi, var(--accent)); }
.ask-try.best { border: 2px solid var(--accent); }
.ask-try img { width: 100%; aspect-ratio: 4 / 3; object-fit: cover; display: block; }
.ask-mark { position: absolute; top: 8px; right: 8px; padding: 2px 8px; border-radius: 99px; background: var(--accent); color: #fff; font-size: 11px; }
.ask-note { padding: 8px 10px; font-size: 13px; font-weight: 600; }
.ask-note.good { color: var(--ok); }
.ask-trial video { width: 100%; max-height: 420px; border-radius: 12px; background: #000; }
.ask-next { display: flex; flex-direction: column; gap: 10px; padding: 14px; border: 1px solid var(--rule-2); border-radius: 12px; }
.ask-next p { margin: 0; font-size: 13px; color: var(--soft); }
.ask-next small { font-size: 12px; }
.ask-acts { display: flex; gap: 8px; flex-wrap: wrap; }
.ask-acts .btn { flex: 1; justify-content: center; }
.danger-line { color: var(--bad, #f87171); border-color: color-mix(in srgb, var(--bad, #f87171) 50%, transparent); }
.lpk-foot { display: flex; justify-content: flex-end; gap: 12px; align-items: center; margin-top: 12px; }
</style>
