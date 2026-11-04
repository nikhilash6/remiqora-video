<script setup lang="ts">
// The LoRA of one step of the home's route «Как снимаем»: a quiet chip «LoRA» in the step's tile (beside it in the
// markup, not inside the tile's button - so a click here never switches the step), «LoRA · 2» when some are chosen. It
// opens a popover under itself: the chosen ones with their strength (LoraChip) and ×, then the library to add from -
// «Съёмка»: the video's, only its model's own (lorasFor: H3's for H3, LTX 2.3's for LTX 2.3…); «Герой» / «Места»: Krea
// 2's; «Кадры»: FLUX.2 Klein's. The popover lives in <body> (nothing clips it), follows the chip on scroll, ↑ ↓ Enter
// pick, Esc or a click beside it closes it.
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Icon from './Icon.vue'
import LoraChip from './LoraChip.vue'
import type { Lib } from './LoraAdd.vue'
import type { Pick } from './LoraSet.vue'
import { S, lorasFor } from '../lib/store'

const props = defineProps<{ kind: 'video' | 'krea' | 'flux2'; model?: string; list: Pick[]; label: string }>()
const emit = defineEmits<{ 'update:list': [v: Pick[]] }>()
const { t } = useI18n()
const lib = computed<Lib[]>(() => (props.kind === 'video' ? lorasFor(props.model || 'h3') : props.kind === 'krea' ? S.kreaLoras : lorasFor('flux2'))
  .filter((l) => l.verdict !== 'blocked'))
const q = ref('')
const free = computed(() => lib.value.filter((l) => !props.list.some((u) => u.id === l.id) && l.name.toLowerCase().includes(q.value.trim().toLowerCase())))
const add = (l: Lib) => emit('update:list', [...props.list, { id: l.id, name: l.name, strength: l.strength ?? 0.8 }])
const strength = (id: string, v: number) => emit('update:list', props.list.map((u) => (u.id === id ? { ...u, strength: v } : u)))
const remove = (id: string) => emit('update:list', props.list.filter((u) => u.id !== id))

const open = ref(false)
const btn = ref<HTMLButtonElement | null>(null)
const pop = ref<HTMLElement | null>(null)
const search = ref<HTMLInputElement | null>(null)
const pos = ref({ left: 0, top: 0, maxH: 360 })
function place() {
  const r = btn.value?.getBoundingClientRect()
  if (!r) return
  const w = Math.min(340, window.innerWidth - 32), below = window.innerHeight - r.bottom - 12, above = r.top - 12
  const up = below < 240 && above > below
  const maxH = Math.max(160, Math.min(380, up ? above : below))
  pos.value = { left: Math.max(16, Math.min(r.right - w, window.innerWidth - w - 16)), top: up ? r.top - 6 - maxH : r.bottom + 6, maxH }
}
async function toggle() {
  open.value = !open.value
  q.value = ''
  if (!open.value) return
  place()
  await nextTick()
  ;(search.value || pop.value?.querySelector<HTMLElement>('button'))?.focus()
}
function close(back = false) { open.value = false; if (back) btn.value?.focus() }
function key(e: KeyboardEvent) {
  if (e.key === 'Escape') { close(true); e.stopPropagation(); return }
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
  e.preventDefault()
  const items = [...(pop.value?.querySelectorAll<HTMLElement>('.rl-item') || [])]
  if (!items.length) return
  const i = items.indexOf(document.activeElement as HTMLElement)
  items[e.key === 'ArrowDown' ? (i + 1) % items.length : (i - 1 + items.length) % items.length].focus()
}
const away = (e: MouseEvent) => {
  const n = e.target as Node
  if (open.value && !btn.value?.contains(n) && !pop.value?.contains(n)) close()
}
const follow = () => { if (open.value) place() }
onMounted(() => { document.addEventListener('mousedown', away); window.addEventListener('scroll', follow, true); window.addEventListener('resize', follow) })
onUnmounted(() => { document.removeEventListener('mousedown', away); window.removeEventListener('scroll', follow, true); window.removeEventListener('resize', follow) })
</script>

<template>
  <button ref="btn" type="button" class="rl-chip" :class="{ on: list.length, open }" :aria-expanded="open" aria-haspopup="dialog"
    :aria-label="`${label}: ${list.length ? list.map((u) => u.name).join(', ') : t('home.loraNone')}`" :title="label" @click.stop="toggle">
    <Icon name="plug" :size="12" /><span class="rl-txt">LoRA</span><template v-if="list.length"><span class="rl-txt"> ·</span> {{ list.length }}</template></button>
  <Teleport to="body">
    <div v-if="open" ref="pop" class="rl-pop" role="dialog" :aria-label="label" :style="{ left: `${pos.left}px`, top: `${pos.top}px`, maxHeight: `${pos.maxH}px` }" @keydown="key">
      <div class="rl-ttl">{{ label }}</div>
      <div v-if="list.length" class="rl-chosen">
        <LoraChip v-for="u in list" :key="u.id" :name="u.name" :strength="u.strength" @update:strength="strength(u.id, $event)" @remove="remove(u.id)" />
      </div>
      <p v-else class="rl-none">{{ t('home.loraNoneYet') }}</p>
      <input v-if="lib.length > 8" ref="search" v-model="q" class="field-in" :placeholder="t('loraset.search')" :aria-label="t('loraset.search')"
        @keydown.enter.prevent="free.length && add(free[0])">
      <div class="rl-list" role="listbox" :aria-label="label">
        <button v-for="l in free" :key="l.id" type="button" role="option" class="rl-item" :title="l.name" @click="add(l)"><Icon name="plus" :size="12" />{{ l.name }}</button>
        <span v-if="!free.length" class="rl-none">{{ lib.length ? t('loraset.nothing') : t('loraset.empty') }}</span>
      </div>
    </div>
  </Teleport>
</template>

<style scoped>
.rl-chip { display: inline-flex; align-items: center; gap: 4px; min-height: 24px; padding: 0 8px; border-radius: 99px; border: 1px solid var(--rule-2);
  background: var(--panel); color: var(--muted); font: 500 11.5px/1 var(--ui); white-space: nowrap; cursor: pointer; }
.rl-chip:hover, .rl-chip.open { border-color: var(--accent); color: var(--text); }
.rl-chip.on { border-color: var(--accent); background: var(--accent-bg); color: var(--accent-hi); }
/* a finger needs more than the chip's 24 px: the zone grows, the chip does not */
.rl-chip { position: relative; }
.rl-chip::after { content: ""; position: absolute; inset: -8px -4px; }
/* a phone's row is narrow: the plug and the count */
@container (max-width: 480px) { .rl-txt { display: none; } .rl-chip { padding: 0 7px; } }
</style>

<style>
/* the popover is in <body>: not scoped */
.rl-pop { position: fixed; z-index: 1000; width: min(340px, calc(100vw - 32px)); overflow-y: auto; overscroll-behavior: contain; display: flex; flex-direction: column; gap: 8px;
  padding: 10px; border-radius: 12px; border: 1px solid var(--rule-2); background: var(--panel); box-shadow: 0 14px 40px rgba(0, 0, 0, .45); font-size: 13px; }
.rl-pop .rl-ttl { font-weight: 600; font-size: 13.5px; margin: 0 2px; }
.rl-pop .rl-chosen { display: flex; flex-wrap: wrap; gap: 6px; padding-bottom: 8px; border-bottom: 1px solid var(--rule); }
.rl-pop .rl-none { margin: 0; padding: 2px 4px; color: var(--muted); font-size: 12.5px; }
.rl-pop .field-in { padding: 7px 10px; font-size: 13px; }
.rl-pop .rl-list { display: flex; flex-direction: column; gap: 1px; }
.rl-pop .rl-item { display: flex; align-items: center; gap: 6px; min-height: 32px; padding: 0 8px; border: 0; border-radius: 7px; background: none; color: var(--text); font-size: 13px; text-align: left;
  overflow: hidden; text-overflow: ellipsis; white-space: nowrap; cursor: pointer; }
.rl-pop .rl-item svg { flex: none; color: var(--muted); }
.rl-pop .rl-item:hover, .rl-pop .rl-item:focus-visible { background: var(--hover); outline: none; box-shadow: inset 0 0 0 2px var(--accent); }
</style>
