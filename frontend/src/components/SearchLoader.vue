<template>
  <div
    role="status"
    aria-live="polite"
    class="rounded-lg border border-gray-200 bg-white p-5 shadow-sm dark:border-gray-700 dark:bg-gray-800"
  >
    <svg
      :viewBox="`0 0 ${WIDTH} ${HEIGHT}`"
      class="block w-full h-[200px] overflow-visible"
      aria-hidden="true"
    >
      <g class="text-gray-300 dark:text-gray-600">
        <circle
          v-for="(p, i) in points"
          :key="`bg-${i}`"
          :cx="p.x"
          :cy="p.y"
          r="2.5"
          fill="currentColor"
        />
      </g>

      <g v-if="!reducedMotion">
        <line
          v-for="edge in activeEdges"
          :key="`edge-${edge.key}`"
          :x1="CENTER_X"
          :y1="CENTER_Y"
          :x2="points[edge.targetIdx].x"
          :y2="points[edge.targetIdx].y"
          class="edge-line"
          stroke="currentColor"
          stroke-width="1.25"
          stroke-linecap="round"
        />
        <circle
          v-for="edge in activeEdges"
          :key="`hit-${edge.key}`"
          :cx="points[edge.targetIdx].x"
          :cy="points[edge.targetIdx].y"
          r="3"
          class="neighbor-pulse"
          fill="currentColor"
        />
      </g>

      <circle
        :cx="CENTER_X"
        :cy="CENTER_Y"
        r="6"
        class="query-node"
        fill="currentColor"
      />
    </svg>

    <div class="mt-4 text-center text-sm text-gray-600 min-h-[1.25rem] dark:text-gray-400">
      <Transition name="caption" mode="out-in">
        <span :key="captionIndex">{{ stages[captionIndex] }}</span>
      </Transition>
    </div>
  </div>
</template>

<script setup>
import { onMounted, onBeforeUnmount, ref } from 'vue'

const WIDTH = 512
const HEIGHT = 200
const CENTER_X = WIDTH / 2
const CENTER_Y = HEIGHT / 2
const BG_NODE_COUNT = 32
const ACTIVE_EDGE_COUNT = 6
const EDGE_CYCLE_MS = 1300
const CAPTION_CYCLE_MS = 1500
const CENTER_EXCLUSION_R = 36

const stages = [
  'Understanding question…',
  'Querying Loki…',
  'Condensing log lines…',
  'Asking LLM…',
]

const points = ref([])
const activeEdges = ref([])
const captionIndex = ref(0)
const reducedMotion = ref(false)

let edgeTimer = null
let captionTimer = null
let edgeKeyCounter = 0

function seedPoints() {
  const out = []
  let attempts = 0
  while (out.length < BG_NODE_COUNT && attempts < BG_NODE_COUNT * 20) {
    attempts += 1
    const x = 12 + Math.random() * (WIDTH - 24)
    const y = 12 + Math.random() * (HEIGHT - 24)
    const dx = x - CENTER_X
    const dy = y - CENTER_Y
    if (Math.hypot(dx, dy) < CENTER_EXCLUSION_R) continue
    out.push({ x, y })
  }
  points.value = out
}

function pickActiveEdges() {
  const indices = new Set()
  while (indices.size < ACTIVE_EDGE_COUNT && indices.size < points.value.length) {
    indices.add(Math.floor(Math.random() * points.value.length))
  }
  activeEdges.value = [...indices].map((targetIdx) => ({
    targetIdx,
    key: ++edgeKeyCounter,
  }))
}

onMounted(() => {
  reducedMotion.value =
    typeof window !== 'undefined' &&
    window.matchMedia &&
    window.matchMedia('(prefers-reduced-motion: reduce)').matches

  seedPoints()

  if (!reducedMotion.value) {
    pickActiveEdges()
    edgeTimer = setInterval(pickActiveEdges, EDGE_CYCLE_MS)
  }

  captionTimer = setInterval(() => {
    captionIndex.value = (captionIndex.value + 1) % stages.length
  }, CAPTION_CYCLE_MS)
})

onBeforeUnmount(() => {
  if (edgeTimer) clearInterval(edgeTimer)
  if (captionTimer) clearInterval(captionTimer)
})
</script>

<style scoped>
.query-node {
  color: oklch(0.623 0.214 259.815); /* tailwind blue-500 */
  transform-origin: 256px 100px;
  transform-box: fill-box;
  animation: query-pulse 1.6s ease-in-out infinite;
}

@keyframes query-pulse {
  0%, 100% { transform: scale(1); opacity: 0.95; }
  50% { transform: scale(1.35); opacity: 1; }
}

.edge-line {
  color: oklch(0.707 0.165 254.624); /* tailwind blue-400 */
  stroke-dasharray: 320;
  stroke-dashoffset: 320;
  animation: edge-draw 700ms ease-out forwards;
}

@keyframes edge-draw {
  to { stroke-dashoffset: 0; }
}

.neighbor-pulse {
  color: oklch(0.707 0.165 254.624); /* tailwind blue-400 */
  transform-origin: center;
  transform-box: fill-box;
  opacity: 0;
  animation: neighbor-pop 900ms ease-out 600ms forwards;
}

@keyframes neighbor-pop {
  0% { transform: scale(0.6); opacity: 0; }
  35% { transform: scale(2.4); opacity: 1; }
  100% { transform: scale(1); opacity: 0.5; }
}

.caption-enter-active,
.caption-leave-active {
  transition: opacity 250ms ease;
}
.caption-enter-from,
.caption-leave-to {
  opacity: 0;
}

@media (prefers-reduced-motion: reduce) {
  .query-node {
    animation: query-pulse-soft 2.4s ease-in-out infinite;
  }
  @keyframes query-pulse-soft {
    0%, 100% { transform: scale(1); }
    50% { transform: scale(1.15); }
  }
}

/* Dark mode: brighten animated nodes against dark background */
:global(.dark) .query-node {
  color: oklch(0.707 0.165 254.624); /* blue-400 */
}
:global(.dark) .edge-line {
  color: oklch(0.707 0.165 254.624); /* blue-400 */
}
:global(.dark) .neighbor-pulse {
  color: oklch(0.707 0.165 254.624); /* blue-400 */
}
</style>
