<script setup lang="ts">
// A link on the graph's canvas, drawn as the graph says (GE.lines, «Линии» in the tool bar): «плавные» - Vue Flow's
// bezier, «ступеньки» - right angles with rounded corners, the vertical run in the gap right before the target's column
// (graphArrange's GAP_X), «прямые» - a straight line. The colour, the selection and a running job's glow stay the
// canvas's CSS (the wrapper's classes: ge-<port type>, selected, animated).
import { computed } from 'vue'
import { BaseEdge, getBezierPath, getSmoothStepPath, getStraightPath, type EdgeProps } from '@vue-flow/core'
import { GE } from '../../lib/graphDoc'
import { GAP_X } from '../../lib/graphArrange'

const props = defineProps<EdgeProps>()
const RADIUS = 14, OFFSET = 16
const path = computed(() => {
  const p = props
  // a port a pixel or so off the other's height (rounding) is level: a straight run, not a tiny step or wave
  const sy = Math.abs(p.sourceY - p.targetY) < 2 ? p.targetY : p.sourceY
  const at = { sourceX: p.sourceX, sourceY: sy, targetX: p.targetX, targetY: p.targetY, sourcePosition: p.sourcePosition, targetPosition: p.targetPosition }
  const lines = GE.lines || 'smooth'
  if (lines === 'straight') return getStraightPath(at)[0]
  if (lines === 'step') {
    // forward over a gap: turn half a gap before the target (between its column and the one before); else Vue Flow's own
    const fwd = p.targetX - p.sourceX > GAP_X
    return getSmoothStepPath({ ...at, borderRadius: RADIUS, offset: OFFSET, ...(fwd ? { centerX: p.targetX - GAP_X / 2 } : {}) })[0]
  }
  return getBezierPath(at)[0]
})
</script>

<template>
  <BaseEdge :id="id" :path="path" :marker-start="markerStart" :marker-end="markerEnd" :style="style" :interaction-width="interactionWidth" />
</template>
