<script setup lang="ts">
// «Подача» and «Вид» (app/looks.py): the delivery's chips (wrapping, «Решит сценарист»
// first), the looks' tiles by group with tabs and a search, the writer's pick (a dashed tile) and «Сменить», «Свой вид»
// in words, «Сохранить как свой вид» and «Мои виды». The idea screen shows it in place; the clip's starts, the board and
// the graph open it in a window (LookPick.vue). v-model: { delivery, look, look_own }.
import { computed, nextTick, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import HelpQ from './HelpQ.vue'
import { AUTO, GLYPH, L, type Look, type LookChoice, type LookPick, deliveryName, loadLooks, lookName, lookOf, pickWhy, previewUrl, removeMine, saveMine, tileStyle } from '../lib/looks'
import { banner } from '../lib/store'

const props = withDefaults(defineProps<{ modelValue: LookChoice; pick?: LookPick | null; showDelivery?: boolean; showLook?: boolean; compact?: boolean; video?: string }>(),
  { pick: null, showDelivery: true, showLook: true, compact: false })
const emit = defineEmits<{ 'update:modelValue': [v: LookChoice] }>()
const { t, locale } = useI18n()

const v = computed(() => props.modelValue)
const set = (part: Partial<LookChoice>) => emit('update:modelValue', { ...props.modelValue, ...part })

const tab = ref('all')
const q = ref('')
const tilesEl = ref<HTMLElement | null>(null)
const tabs = computed(() => [{ id: 'all', name: t('looks.all') }, ...L.groups])
const match = (x: { name: string; hint?: string }) => !q.value.trim() || `${x.name} ${x.hint || ''}`.toLowerCase().includes(q.value.trim().toLowerCase())
// a look held by one model only (H3: comic, paper cut-out) - shown when that model shoots; no model known: shown
const fits = (x: Look) => !x.models || !props.video || x.models.includes(props.video)
const groups = computed(() => {
  const out: { id: string; name: string; items: Look[] }[] = []
  const auto = L.looks.find((x) => x.id === AUTO)
  if (auto && tab.value === 'all' && match(auto)) out.push({ id: AUTO, name: t('looks.auto'), items: [auto] })
  for (const g of L.groups) {
    if (tab.value !== 'all' && tab.value !== g.id) continue
    const items = g.id === 'mine' ? L.mine.map((m) => lookOf(m.id)).filter((x): x is Look => !!x) : L.looks.filter((x) => x.group === g.id && fits(x))
    const shown = items.filter(match)
    if (shown.length) out.push({ id: g.id, name: g.name, items: shown })
  }
  return out
})
const pickD = computed(() => (props.pick && v.value.delivery === AUTO ? props.pick.delivery : ''))
const pickL = computed(() => (props.pick && v.value.look === AUTO ? props.pick.look : ''))
const why = computed(() => pickWhy(props.pick))
const anyAuto = computed(() => v.value.delivery === AUTO || v.value.look === AUTO)

// «Сменить»: the writer's pick becomes the user's own choice, the tiles open on its group
function change() {
  if (!props.pick) return
  set({ delivery: props.pick.delivery, look: props.pick.look })
  tab.value = lookOf(props.pick.look)?.group || 'all'
  tilesEl.value?.scrollIntoView({ block: 'nearest', behavior: 'smooth' })
}

// «Сохранить как свой вид»: the look in effect + the own words, under a name
const naming = ref(false)
const mineName = ref('')
const busy = ref(false)
const nameEl = ref<HTMLInputElement | null>(null)
async function startNaming() { naming.value = true; await nextTick(); nameEl.value?.focus() }
const baseLook = () => {
  const lk = v.value.look
  if (lk.startsWith('my_')) return L.mine.find((m) => m.id === lk)?.base || 'live_cinema'
  if (lk !== AUTO) return lk
  return props.pick?.look || 'live_cinema'
}
async function saveAsMine() {
  if (!mineName.value.trim()) return
  busy.value = true
  try {
    const m = await saveMine(mineName.value.trim(), baseLook(), v.value.look_own)
    set({ look: m.id, look_own: '' })
    naming.value = false; mineName.value = ''
  } catch (e) { banner((e as Error).message) } finally { busy.value = false }
}
async function dropMine(id: string, name: string) {
  if (!confirm(t('looks.removeMine', { name }))) return
  try {
    await removeMine(id)
    if (v.value.look === id) set({ look: AUTO })
  } catch (e) { banner((e as Error).message) }
}
const faceNote = (x: Look) => (x.face_tested == null ? t('looks.notTested') : t('looks.tested', { v: x.face_tested.toFixed(2) }))
// a look drawn by every frame (looks.needs_frame): the tile says so, and the chosen one says what it costs
const tileTitle = (x: Look) => `${x.hint}${x.id !== AUTO ? ' · ' + faceNote(x) : ''}${x.needs_frame ? ' · ' + t('looks.frame') : ''}`
const frameLook = computed(() => { const x = lookOf(v.value.look === AUTO ? pickL.value : v.value.look); return x?.needs_frame ? x : null })

onMounted(() => { loadLooks().catch((e) => banner((e as Error).message)) })
watch(locale, () => { loadLooks().catch(() => {}) })
</script>

<template>
  <div class="lp" :class="{ compact }">
    <div v-if="showDelivery" class="lp-row">
      <span class="lbl">{{ t('looks.delivery') }} <small>{{ t('looks.deliverySub') }}</small> <HelpQ id="idea" term="Подача" /></span>
      <div class="chips lp-del" role="radiogroup" :aria-label="t('looks.delivery')">
        <button v-for="d in L.deliveries" :key="d.id" type="button" role="radio" :aria-checked="v.delivery === d.id" class="chip"
          :class="{ on: v.delivery === d.id, picked: pickD === d.id, 'lp-auto': d.id === AUTO }" :title="d.hint" @click="set({ delivery: d.id })">
          <span v-if="d.id === AUTO" aria-hidden="true">✦</span>{{ d.name }}</button>
      </div>
    </div>

    <div v-if="showLook" class="lp-look">
      <div class="lp-main">
        <div class="lp-head">
          <span class="lbl">{{ t('looks.look') }} <small>{{ t('looks.lookSub') }}</small> <HelpQ id="idea" term="Вид" /></span>
          <div class="lp-tabs" role="tablist" :aria-label="t('looks.look')">
            <button v-for="g in tabs" :key="g.id" type="button" role="tab" :aria-selected="tab === g.id" :class="{ on: tab === g.id }" @click="tab = g.id">{{ g.name }}</button>
          </div>
          <input v-model="q" type="search" class="field-in lp-find" :placeholder="t('looks.find')" :aria-label="t('looks.findLabel')">
        </div>
        <div ref="tilesEl" class="lp-groups">
          <div v-for="g in groups" :key="g.id" class="lp-group">
            <div class="lp-gname">{{ g.name }}</div>
            <div class="lp-tiles">
              <button v-for="x in g.items" :key="x.id" type="button" class="lp-tile" :class="{ on: v.look === x.id, picked: pickL === x.id }"
                :aria-pressed="v.look === x.id" :title="tileTitle(x)" @click="set({ look: x.id })">
                <span class="lp-pic" :style="tileStyle(x)">
                  <img v-if="x.preview" :src="previewUrl(x.id)" alt="" loading="lazy">
                  <i v-else aria-hidden="true">{{ GLYPH[x.group] || '✦' }}</i>
                  <em v-if="x.needs_frame" class="lp-frame" :title="t('looks.frame')" role="img" :aria-label="t('looks.frame')">✎ {{ t('looks.frameBadge') }}</em>
                </span>
                <span class="lp-name">{{ x.name }}</span>
              </button>
            </div>
          </div>
          <p v-if="!groups.length" class="muted">{{ t('looks.nothing') }}</p>
        </div>
        <p v-if="frameLook" class="lp-frame-note muted">✎ {{ t('looks.frameNote', { name: frameLook.name }) }}</p>
      </div>

      <aside class="lp-side">
        <div v-if="pick" class="lp-pick">
          <span class="lp-pick-h">✦ {{ t('looks.picked') }}</span>
          <b>{{ deliveryName(pick.delivery) }} · {{ lookName(pick.look) }}</b>
          <p v-if="why">{{ why }}</p>
          <button type="button" class="link-btn" @click="change">{{ t('looks.change') }}</button>
          <small>{{ t('looks.pickedNote') }}</small>
        </div>
        <p v-else-if="anyAuto" class="lp-later muted">{{ t('looks.pickLater') }}</p>
        <label class="stack"><span class="lbl">{{ t('looks.own') }} <HelpQ id="idea" term="Свой вид" /></span>
          <textarea class="field-in" rows="3" :value="v.look_own" :placeholder="t('looks.ownPh')" maxlength="600"
            @input="set({ look_own: ($event.target as HTMLTextAreaElement).value })" /></label>
        <div v-if="naming" class="lp-naming">
          <input ref="nameEl" v-model="mineName" class="field-in" maxlength="60" :placeholder="t('looks.minePh')" :aria-label="t('looks.mineName')" @keydown.enter.prevent="saveAsMine">
          <div class="lp-naming-acts"><button type="button" class="btn" :disabled="busy || !mineName.trim()" @click="saveAsMine">{{ t('looks.save') }}</button>
            <button type="button" class="link-btn" @click="naming = false">{{ t('looks.cancel') }}</button></div>
        </div>
        <button v-else type="button" class="btn lp-save" @click="startNaming">+ {{ t('looks.saveMine') }}</button>
        <div class="lp-mine">
          <span class="lbl">★ {{ t('looks.mine') }}</span>
          <div v-if="L.mine.length" class="chips">
            <span v-for="m in L.mine" :key="m.id" class="chip lp-mchip" :class="{ on: v.look === m.id }">
              <button type="button" @click="set({ look: m.id })">★ {{ m.name }}</button>
              <button type="button" class="lp-x" :title="t('looks.removeTitle')" :aria-label="t('looks.removeTitle')" @click="dropMine(m.id, m.name)">×</button>
            </span>
          </div>
          <small class="muted">{{ L.mine.length ? t('looks.mineNote') : t('looks.mineEmpty') }}</small>
        </div>
      </aside>
    </div>
  </div>
</template>
