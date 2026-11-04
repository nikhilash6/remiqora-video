<script setup lang="ts">
// One button «Подача: … · Вид: … ▾» (the clip's starts: `selects`; the board's header and the graph's «Стиль
// проекта»: `chips` with ✎) opening the panel (LookPanel.vue) in a window, the choice is given back on «Готово»
// (the board saves it then: a change of look rebuilds the project's style). `pick`: the writer's choice, shown as
// «✦ Решит сценарист» → «Клип · VHS 90-х».
import { computed, onMounted, ref, watch } from 'vue'
import { useI18n } from 'vue-i18n'
import HelpModal from './help/HelpModal.vue'
import Icon from './Icon.vue'
import LookPanel from './LookPanel.vue'
import { AUTO, type LookChoice, type LookPick, deliveryName, loadLooks, lookName, lookOf, tileStyle } from '../lib/looks'

const props = withDefaults(defineProps<{ modelValue: LookChoice; pick?: LookPick | null; variant?: 'selects' | 'chips'; disabled?: boolean; video?: string }>(),
  { pick: null, variant: 'selects', disabled: false })
const emit = defineEmits<{ 'update:modelValue': [v: LookChoice] }>()
const { t, locale } = useI18n()

const open = ref(false)
const draft = ref<LookChoice>({ ...props.modelValue })
function show() {
  if (props.disabled) return
  draft.value = { ...props.modelValue }
  open.value = true
}
function done() {
  open.value = false
  const a = props.modelValue, b = draft.value
  if (a.delivery !== b.delivery || a.look !== b.look || (a.look_own || '') !== (b.look_own || '')) emit('update:modelValue', { ...b })
}

// «✦ Решит сценарист» until the writer picks; then what it picked
const dName = computed(() => (props.modelValue.delivery === AUTO && props.pick ? `✦ ${deliveryName(props.pick.delivery)}` : props.modelValue.delivery === AUTO ? `✦ ${deliveryName(AUTO)}` : deliveryName(props.modelValue.delivery)))
const lName = computed(() => (props.modelValue.look === AUTO && props.pick ? `✦ ${lookName(props.pick.look)}` : props.modelValue.look === AUTO ? `✦ ${lookName(AUTO)}` : lookName(props.modelValue.look)))

// the chosen look's colours (its tile's), a small square before its name; none while the writer decides
const swatch = computed(() => (props.modelValue.look !== AUTO ? tileStyle(lookOf(props.modelValue.look)) : null))
onMounted(() => { loadLooks().catch(() => {}) })
watch(locale, () => { loadLooks().catch(() => {}) })
</script>

<template>
  <div class="lpk" :class="`is-${variant}`">
    <button type="button" class="lpk-btn" :class="{ chip: variant === 'chips' }" :disabled="disabled" :title="t('looks.editTitle')" @click="show">
      <template v-if="variant === 'selects'"><Icon name="megaphone" :size="16" class="lpk-ic" /><span class="lpk-part">{{ t('looks.deliveryChip', { name: '' }) }}<b>{{ dName }}</b></span><span class="lpk-sep" aria-hidden="true">·</span>
        <Icon name="palette" :size="16" class="lpk-ic" /><span class="lpk-part">{{ t('looks.lookChip', { name: '' }) }}<i v-if="swatch && Object.keys(swatch).length" class="lpk-sw" :style="swatch" aria-hidden="true" /><b>{{ lName }}</b></span><Icon name="down" :size="14" class="lpk-down" /></template>
      <template v-else>{{ t('looks.deliveryChip', { name: dName }) }}<span class="lpk-sep" aria-hidden="true">·</span>{{ t('looks.lookChip', { name: lName }) }}<span aria-hidden="true"> ✎</span></template></button>
    <HelpModal :open="open" :title="t('looks.editTitle')" icon="spark" wide @close="open = false">
      <LookPanel v-model="draft" :pick="pick" :video="video" compact />
      <template #foot>
        <span class="lpk-now muted">{{ t('looks.deliveryChip', { name: deliveryName(draft.delivery) }) }} · {{ t('looks.lookChip', { name: lookName(draft.look) }) }}</span><span class="grow" />
        <button type="button" class="link-btn" @click="open = false">{{ t('looks.cancel') }}</button>
        <button type="button" class="btn grad lpk-done" @click="done">{{ t('looks.done') }}</button>
      </template>
    </HelpModal>
  </div>
</template>
