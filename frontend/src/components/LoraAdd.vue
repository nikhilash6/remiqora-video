<script setup lang="ts">
// «+ добавить»: a list under the button - the LoRA of each group's library not picked yet, with a search when there
// are many; a click (or ↑ ↓ and Enter) picks one. It is a popover over everything (moved to the page's body, placed
// under its button, following it on scroll), so no card around it clips it or shows through; Esc or a click beside it
// closes it. `disabled`: the button never opens it.
import { computed, nextTick, onMounted, onUnmounted, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Icon from './Icon.vue'

export type Lib = { id: string; name: string; strength?: number }
export type Group = { key: string; label?: string; items: Lib[]; total: number }
const { t } = useI18n()
const props = defineProps<{ groups: Group[]; more?: boolean; disabled?: boolean; label?: string }>()
const emit = defineEmits<{ pick: [key: string, l: Lib] }>()
const open = ref(false)
const q = ref('')
const root = ref<HTMLElement | null>(null)
const btn = ref<HTMLButtonElement | null>(null)
const pop = ref<HTMLElement | null>(null)
const search = ref<HTMLInputElement | null>(null)
const many = computed(() => props.groups.reduce((n, g) => n + g.total, 0) > 8)
const shown = computed(() => props.groups.map((g) => ({ ...g, items: g.items.filter((l) => l.name.toLowerCase().includes(q.value.trim().toLowerCase())) })))
const flat = computed(() => shown.value.flatMap((g) => g.items.map((l) => ({ key: g.key, l }))))
const cur = ref(-1)   // the item ↑ ↓ point at

// under the button, inside the window: above it when there is no room below
const pos = ref({ left: 0, top: 0, maxH: 300 })
function place() {
  const r = btn.value?.getBoundingClientRect()
  if (!r) return
  const w = Math.min(320, window.innerWidth - 32), below = window.innerHeight - r.bottom - 12, above = r.top - 12
  const up = below < 220 && above > below
  const maxH = Math.max(140, Math.min(320, up ? above : below))
  pos.value = { left: Math.max(16, Math.min(r.left, window.innerWidth - w - 16)), top: up ? r.top - 6 - maxH : r.bottom + 6, maxH }
}
async function toggle() {
  if (props.disabled) return
  open.value = !open.value
  q.value = ''
  cur.value = -1
  if (!open.value) return
  place()
  await nextTick()
  if (search.value) search.value.focus()
  else pop.value?.querySelector<HTMLElement>('.la-item')?.focus()
}
function close(back = false) { open.value = false; if (back) btn.value?.focus() }
function pick(key: string, l: Lib) { emit('pick', key, l); close(true) }
function key(e: KeyboardEvent) {
  if (!open.value) return
  if (e.key === 'Escape') { close(true); e.stopPropagation(); return }
  if (e.key !== 'ArrowDown' && e.key !== 'ArrowUp') return
  e.preventDefault()
  const n = flat.value.length
  if (!n) return
  cur.value = e.key === 'ArrowDown' ? (cur.value + 1) % n : (cur.value - 1 + n) % n
  pop.value?.querySelectorAll<HTMLElement>('.la-item')[cur.value]?.focus()
}
function searchKey(e: KeyboardEvent) {
  if (e.key === 'Enter' && flat.value.length) { e.preventDefault(); const x = flat.value[Math.max(0, cur.value)]; pick(x.key, x.l) }
}
const away = (e: MouseEvent) => {
  const n = e.target as Node
  if (open.value && !root.value?.contains(n) && !pop.value?.contains(n)) close()
}
const follow = () => { if (open.value) place() }
onMounted(() => {
  document.addEventListener('mousedown', away)
  window.addEventListener('scroll', follow, true)
  window.addEventListener('resize', follow)
})
onUnmounted(() => {
  document.removeEventListener('mousedown', away)
  window.removeEventListener('scroll', follow, true)
  window.removeEventListener('resize', follow)
})
</script>

<template>
  <span ref="root" class="la">
    <button ref="btn" type="button" class="la-btn" :class="{ on: open }" :disabled="disabled" :aria-expanded="open" aria-haspopup="listbox" :aria-label="label" @click="toggle">
      <Icon name="plus" :size="12" />{{ more ? t('loraset.more') : t('loraset.addShort') }}</button>
    <Teleport to="body">
      <div v-if="open" ref="pop" class="la-pop" :style="{ left: `${pos.left}px`, top: `${pos.top}px`, maxHeight: `${pos.maxH}px` }" @keydown="key">
        <input v-if="many" ref="search" v-model="q" class="field-in" :placeholder="t('loraset.search')" :aria-label="t('loraset.search')" @input="cur = -1" @keydown="searchKey">
        <div v-for="g in shown" :key="g.key" class="la-g" role="listbox" :aria-label="g.label || label">
          <span v-if="g.label" class="la-gh">{{ g.label }}</span>
          <button v-for="l in g.items" :key="l.id" type="button" role="option" class="la-item" :title="l.name" @click="pick(g.key, l)">{{ l.name }}</button>
          <span v-if="!g.items.length" class="la-none">{{ g.total ? t('loraset.nothing') : t('loraset.empty') }}</span>
        </div>
      </div>
    </Teleport>
  </span>
</template>
