<script setup lang="ts">
// A list of folders the studio reads (the weights' roots, the writer's GGUF, the LoRA): one row a folder - its name bold,
// the path before it quiet, what is in it, «Открыть» and «убрать»; at the end «Добавить папку…» (a picker) and a typed
// path. It replaces the old «one per line» text box: the list is edited where it is shown.
import { nextTick, ref } from 'vue'
import { useI18n } from 'vue-i18n'
import Icon from './Icon.vue'

export interface FolderRow { dir: string; meta?: string; missing?: boolean }
const props = defineProps<{ rows: FolderRow[]; label: string }>()
const emit = defineEmits<{ save: [dirs: string[]]; add: []; open: [dir: string] }>()
const { t } = useI18n()

const typing = ref(false)
const typed = ref('')
const input = ref<HTMLInputElement | null>(null)
const dirs = () => props.rows.map((r) => r.dir)
// the path split at its last separator: «D:\models\comfyui\loras\» quiet, «h3» bold
const split = (p: string) => { const i = Math.max(p.lastIndexOf('\\'), p.lastIndexOf('/'), -1); return [p.slice(0, i + 1), p.slice(i + 1) || p] }
function remove(dir: string) { emit('save', dirs().filter((d) => d !== dir)) }
async function startTyping() { typing.value = true; await nextTick(); input.value?.focus() }
function addTyped() {
  const p = typed.value.trim().replace(/^"|"$/g, '')
  if (p && !dirs().some((d) => d.toLowerCase() === p.toLowerCase())) emit('save', [...dirs(), p])
  typed.value = ''
  typing.value = false
}
</script>

<template>
  <div class="fl" role="group" :aria-label="label">
    <ul v-if="rows.length" class="fl-list">
      <li v-for="r in rows" :key="r.dir" :class="{ missing: r.missing }">
        <Icon name="folder" :size="16" class="fl-ic" />
        <span class="fl-path" :title="r.dir"><span class="fl-dir">{{ split(r.dir)[0] }}</span><b>{{ split(r.dir)[1] }}</b></span>
        <span v-if="r.meta" class="fl-meta">{{ r.meta }}</span>
        <span class="fl-acts">
          <button v-if="!r.missing" type="button" class="ghost" :aria-label="`${t('common.open')}: ${r.dir}`" @click="emit('open', r.dir)">{{ t('common.open') }}</button>
          <button type="button" class="ghost fl-x" :title="t('models.fl.remove')" :aria-label="t('models.fl.removeAria', { v: r.dir })" @click="remove(r.dir)"><Icon name="close" :size="14" /></button>
        </span>
      </li>
    </ul>
    <p v-else class="fl-empty">{{ t('models.fl.empty') }}</p>
    <div class="fl-add">
      <button type="button" class="btn" @click="emit('add')"><Icon name="plus" :size="14" />{{ t('models.fl.add') }}</button>
      <form v-if="typing" class="fl-type" @submit.prevent="addTyped">
        <input ref="input" v-model="typed" class="field-in" spellcheck="false" :placeholder="t('models.fl.typePh')" :aria-label="t('models.fl.type')" @keydown.esc="typing = false">
        <button type="submit" class="btn" :disabled="!typed.trim()">{{ t('models.fl.addTyped') }}</button>
      </form>
      <button v-else type="button" class="ghost" @click="startTyping">{{ t('models.fl.type') }}</button>
    </div>
  </div>
</template>

<style scoped>
.fl { display: grid; gap: 10px; }
.fl-list { display: grid; margin: 0; padding: 0; list-style: none; border: 1px solid var(--rule); border-radius: 10px; overflow: hidden; }
.fl-list li { display: flex; gap: 10px; align-items: center; min-width: 0; padding: 9px 8px 9px 12px; background: var(--field); }
.fl-list li + li { border-top: 1px solid var(--rule); }
.fl-ic { flex: none; color: #c084fc; }
.fl-path { flex: 1; min-width: 0; overflow: hidden; text-overflow: ellipsis; white-space: nowrap; font-size: 13.5px; }
.fl-dir { color: var(--muted); }
.fl-path b { color: var(--text); font-weight: 600; }
.fl-meta { flex: none; color: var(--muted); font-size: 12.5px; font-variant-numeric: tabular-nums; }
.missing .fl-meta { color: var(--warn); }
.missing .fl-ic { color: var(--faint); }
.fl-acts { flex: none; display: flex; gap: 2px; }
.fl-x { padding: 4px 6px; color: var(--muted); }
.fl-x:hover { color: var(--bad); }
.fl-empty { margin: 0; padding: 12px; border: 1px dashed var(--rule-2); border-radius: 10px; color: var(--muted); font-size: 13px; }
.fl-add { display: flex; flex-wrap: wrap; gap: 8px; align-items: center; }
.fl-type { display: flex; gap: 8px; flex: 1; min-width: 260px; }
.fl-type input { flex: 1; min-width: 0; }
@media (max-width: 640px) { .fl-meta { display: none; } }
</style>
