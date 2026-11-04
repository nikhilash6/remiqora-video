<script setup lang="ts">
// The steps of «Сделать ролик» in one row: the home's route «Как снимаем» before the start (RouteStrip.vue: a step
// switched on or off, a stop flag, the model under «Съёмка») and the bar while it runs (StageBar.vue: done, now,
// still to come) - the same row, so the plan becomes the progress. The tiles keep their widths whatever happens (equal
// columns, «Съёмка» one and a half): a step's time is its «~2 мин», so switching a step, a hover hint or a new
// estimate never moves the row. A step's `lora` slot (RouteLora: a «LoRA» chip) sits in the tile's bottom right corner -
// beside the tile's button in the markup, so its click never switches the step. In a narrow box (a phone) the row is a list,
// the time on the right. `compact`: thin bars only (a gallery card).
// Every step says what a click does: one that can be skipped has a switch in its tile and «нажмите — пропустить /
// вернуть» on hover and focus; one with a choice (the writer, the model) shows it as a select «LTX 2.3 ▾»; one that
// always runs is not a button at all.
import Icon from './Icon.vue'
import type { Step } from '../lib/home'

export interface RouteItem {
  id: Step
  name: string
  icon: string
  who?: string             // who does it: «сценарист», «FLUX.2 Klein», the model
  time?: string            // «~2 мин», «пропустим», «уже сняты с песни», «12/29»
  // on: runs; off: skipped (dashed, struck through); done: already done; now: running; idle: the empty box's preview
  // (names only); grey: not run this time («пишу сам» ends at the script)
  state: 'on' | 'off' | 'done' | 'now' | 'idle' | 'grey'
  lora?: boolean           // the `lora` slot shows in its corner
  flag?: string            // a stop of «Спрашивать меня»
  flagShort?: string
  press?: 'toggle' | 'menu'   // a button: switches the step on/off, or opens its choice (the writer, the model)
  open?: boolean           // its menu is open
  locked?: boolean         // can't be switched off (the lock and why in `who`)
  photo?: string           // the hero's photo, a small circle before `who`
  pick?: boolean           // `who` is a choice: drawn as a select «LTX 2.3 ▾»
  hint?: string            // what a click does, shown on hover and focus in place of the time
  label?: string           // the button's accessible name
}
import { computed } from 'vue'
const props = defineProps<{ items: RouteItem[]; compact?: boolean }>()
// the columns: fixed by the steps alone, never by their state or time
const cols = computed(() => props.items.map((s) => (s.id === 'shoot' && !props.compact ? 'minmax(0, 1.5fr)' : 'minmax(0, 1fr)')).join(' '))
const emit = defineEmits<{ press: [id: Step, el: HTMLElement] }>()
</script>

<template>
  <div class="rs-wrap">
    <ol class="rs" :class="{ compact }" :style="{ gridTemplateColumns: cols }">
      <li v-for="s in items" :key="s.id" class="rs-li" :class="{ 'has-lora': s.lora && !compact }">
        <component :is="s.press ? 'button' : 'div'" :type="s.press ? 'button' : undefined" class="rs-step" :class="[s.state, { locked: s.locked, open: s.open }]"
          :aria-pressed="s.press === 'toggle' ? s.state === 'on' : undefined" :aria-expanded="s.press === 'menu' ? !!s.open : undefined"
          :aria-disabled="s.locked || undefined" :aria-label="s.label" :aria-current="s.state === 'now' ? 'step' : undefined"
          @click="s.press && !s.locked ? emit('press', s.id, $event.currentTarget as HTMLElement) : undefined">
          <span v-if="s.flag && !compact" class="rs-flag"><Icon name="flag" :size="11" /><span class="rs-full">{{ s.flag }}</span><span class="rs-short">{{ s.flagShort || s.flag }}</span></span>
          <span class="rs-n"><Icon :name="s.state === 'done' ? 'check' : s.icon" :size="15" /><span class="rs-name">{{ s.name }}</span></span>
          <span v-if="s.who && !compact" class="rs-d" :class="{ sel: s.pick }"><Icon v-if="s.locked" name="lock" :size="12" /><i v-if="s.photo" class="rs-ph" :style="{ backgroundImage: `url(${s.photo})` }" /><span class="rs-who">{{ s.who }}</span><Icon v-if="s.pick" name="down" :size="12" class="rs-pick" /></span>
          <span v-if="(s.time || s.hint) && !compact" class="rs-t"><span class="rs-time" :class="{ hides: s.hint }">{{ s.time }}</span><span v-if="s.hint" class="rs-hint">{{ s.hint }}</span></span>
          <i v-if="s.press === 'toggle' && !compact" class="rs-sw" :class="{ on: s.state === 'on', locked: s.locked }" aria-hidden="true"><Icon v-if="s.locked" name="lock" :size="9" /></i>
        </component>
        <span v-if="s.lora && !compact" class="rs-lora"><slot name="lora" :s="s" /></span>
      </li>
    </ol>
  </div>
</template>

<style scoped>
.rs-wrap { container-type: inline-size; min-width: 0; }
.rs { display: grid; gap: 6px; margin: 0; padding: 0; list-style: none; }
.rs-li { position: relative; min-width: 0; display: flex; }
/* the «LoRA» chip in the tile's bottom right corner; the time leaves it room */
.rs-lora { position: absolute; right: 9px; bottom: 9px; z-index: 1; }
.has-lora .rs-t { padding-right: 64px; }
.rs-step { position: relative; flex: 1; display: flex; flex-direction: column; gap: 3px; min-width: 0; min-height: 88px; padding: 9px 11px 12px; border-radius: 10px;
  border: 1px solid var(--route-rule); background: var(--route-on); color: var(--text); text-align: left; font: inherit; }
button.rs-step { cursor: pointer; }
button.rs-step:hover { border-color: var(--accent); }
button.rs-step.locked { cursor: default; }
button.rs-step.locked:hover { border-color: var(--route-rule); }
.rs-step.open { border-color: var(--accent); box-shadow: 0 0 0 1px var(--accent) inset; }
.rs-n { display: flex; align-items: center; gap: 7px; min-width: 0; font-size: 13.5px; font-weight: 600; white-space: nowrap; }
.rs-name { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
/* the switch of a step that can be skipped: top right in the tile (at the row's end on a phone) */
.rs-step:has(.rs-sw) .rs-n { padding-right: 30px; }
.rs-sw { position: absolute; top: 11px; right: 10px; display: grid; place-items: center; width: 26px; height: 15px; border-radius: 99px; background: var(--rule-3); }
.rs-sw::after { content: ""; position: absolute; top: 2px; left: 2px; width: 11px; height: 11px; border-radius: 50%; background: var(--soft); transition: left .15s; }
.rs-sw.on { background: var(--grad); }
.rs-sw.on::after { left: 13px; background: #fff; }
.rs-sw.locked { background: var(--accent-deep); }
.rs-sw.locked::after { display: none; }
.rs-sw :deep(svg) { color: var(--accent-hi); }
.rs-flag ~ .rs-sw { top: 13px; }
/* a choice: a select-looking pill */
.rs-d.sel { align-self: flex-start; max-width: 100%; padding: 1px 6px 1px 8px; border-radius: 6px; border: 1px solid var(--rule-input); background: var(--field); color: var(--text); }
button.rs-step:hover .rs-d.sel, .rs-step.open .rs-d.sel { border-color: var(--accent); }
.rs-who { min-width: 0; overflow: hidden; text-overflow: ellipsis; }
/* the time and the hint share one line: the hint shows over it, the tile keeps its size */
.rs-hint { visibility: hidden; color: var(--accent-hi); overflow: hidden; text-overflow: ellipsis; }
.rs-time, .rs-hint { grid-area: 1 / 1; min-width: 0; }
button.rs-step:hover .rs-hint, button.rs-step:focus-visible .rs-hint { visibility: visible; }
button.rs-step:hover .rs-time.hides, button.rs-step:focus-visible .rs-time.hides { visibility: hidden; }
.rs-n :deep(svg) { color: var(--accent-hi); }
.rs-d { display: flex; align-items: center; gap: 4px; min-width: 0; color: var(--soft); font-size: 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.rs-d :deep(svg) { flex: none; color: var(--faint); }
.rs-pick { color: var(--muted) !important; }
.rs-ph { flex: none; width: 18px; height: 18px; border-radius: 50%; background: var(--deep) center / cover no-repeat; }
.rs-t { display: grid; justify-items: start; max-width: 100%; overflow: hidden; margin-top: auto; color: var(--muted); font-size: 12px; font-variant-numeric: tabular-nums; white-space: nowrap; }
.rs-step.done { background: var(--panel); border-color: var(--rule-2); }
.rs-step.done .rs-n :deep(svg) { color: var(--ok); }
.rs-step.off { background: transparent; border-style: dashed; border-color: var(--rule-3); color: var(--muted); }
.rs-step.off .rs-n { color: var(--muted); text-decoration: line-through; text-decoration-color: var(--faint); }
.rs-step.off .rs-n :deep(svg) { color: var(--faint); }
.rs-step.off .rs-d, .rs-step.off .rs-t { color: var(--faint); }
.rs-step.grey { background: transparent; border-color: var(--rule-2); color: var(--muted); }
.rs-step.grey .rs-n { color: var(--muted); font-weight: 500; }
.rs-step.grey .rs-n :deep(svg), .rs-step.grey .rs-d { color: var(--faint); }
.rs-step.idle { min-height: 0; padding: 8px 11px; background: transparent; border-color: var(--rule-2); }
.rs-step.idle .rs-n { font-weight: 500; color: var(--soft); }
.rs-step.idle .rs-n :deep(svg) { color: var(--faint); }
.rs-step.now { border-color: var(--accent); background: var(--accent-bg); box-shadow: 0 0 0 1px var(--accent) inset; }
.rs-step.now .rs-t { color: var(--soft); }
.rs-flag { position: absolute; top: -9px; right: 10px; display: inline-flex; align-items: center; gap: 4px; padding: 2px 8px 2px 6px; border-radius: 99px;
  background: var(--warn-bg); border: 1px solid #5b4a10; color: var(--stop); font-size: 11px; font-weight: 600; line-height: 1.3; white-space: nowrap; }
.rs-short { display: none; }
/* a gallery card: thin bars */
.rs.compact { gap: 3px; }
.rs.compact .rs-step { min-height: 5px; height: 5px; padding: 0; border: 0; border-radius: 3px; background: var(--rule-2); }
.rs.compact .rs-step > * { display: none; }
.rs.compact .rs-step.done { background: color-mix(in srgb, var(--accent) 55%, transparent); }
.rs.compact .rs-step.now { background: var(--grad); box-shadow: none; }
/* a phone: a list, a step a 44 px row, the time on the right, the flag on the row's corner */
@media (prefers-reduced-motion: reduce) { .rs-sw::after { transition: none; } }
@container (max-width: 480px) {
  .rs:not(.compact) { display: flex; flex-direction: column; }
  .rs-hint { display: none; }
  /* a row: the chip before the time (and the switch) */
  .rs-lora { top: 50%; bottom: auto; transform: translateY(-50%); right: 12px; }
  .has-lora .rs-t { padding-right: 0; }
  .rs:not(.compact) .rs-t { flex: none; }
  .has-lora .rs-d:not(.sel) { display: none; }
  .rs:not(.compact) .has-lora .rs-step { padding-right: 58px; }
  .has-lora:has(.rs-sw) .rs-lora { right: 46px; }
  .rs:not(.compact) .has-lora:has(.rs-sw) .rs-step { padding-right: 92px; }
  .rs:not(.compact) .has-lora .rs-step .rs-sw { position: absolute; right: 12px; top: 50%; transform: translateY(-50%); }
  .rs-time.hides { visibility: visible !important; }
  .rs:not(.compact) .rs-step { flex-direction: row; align-items: center; gap: 10px; min-height: 44px; padding: 7px 10px 7px 12px; }
  .rs:not(.compact) .rs-n { flex: none; min-width: 88px; padding-right: 0; }
  .rs:not(.compact) .rs-sw { position: relative; top: auto; right: auto; flex: none; order: 9; }
  .rs:not(.compact) .rs-d { flex: 1; }
  .rs:not(.compact) .rs-t { margin: 0 0 0 auto; }
  .rs:not(.compact) .rs-step.idle { min-height: 40px; padding: 5px 10px; }
  .rs-flag { top: -8px; right: 8px; }
  .rs-full { display: none; }
  .rs-short { display: inline; }
}
</style>
