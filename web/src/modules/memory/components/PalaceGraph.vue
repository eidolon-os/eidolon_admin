<script setup lang="ts">
import { onBeforeUnmount, onMounted, ref, watch } from 'vue'
import { Network, type Edge, type Node, type Options } from 'vis-network'
import { DataSet } from 'vis-data'
import type { GraphSnapshot } from '@/api/memory'

const props = defineProps<{ snapshot: GraphSnapshot | null }>()

const containerRef = ref<HTMLDivElement | null>(null)
let network: Network | null = null

// Color by node category. `kind` distinguishes palace-graph rooms from
// kg-snapshot entities; `entity_type` further bucketed by the KG ":" prefix
// convention (self / person / pet / place / project / ...).
const NODE_COLORS: Record<string, string> = {
  room:                      '#60a5fa',   // palace rooms
  'entity_type:self':        '#6366f1',   // the user themself — accent
  'entity_type:person':      '#a78bfa',
  'entity_type:pet':         '#f472b6',
  'entity_type:place':       '#fbbf24',
  'entity_type:project':     '#34d399',
  'entity_type:organization':'#22d3ee',
  default:                   '#9ca3af',
}

function colorFor(node: { kind: string; entity_type: string }): string {
  if (node.kind === 'room') return NODE_COLORS.room
  if (node.entity_type) {
    const key = `entity_type:${node.entity_type}`
    if (NODE_COLORS[key]) return NODE_COLORS[key]
  }
  return NODE_COLORS.default
}

function buildOptions(): Options {
  return {
    nodes: {
      shape: 'dot',
      size: 14,
      borderWidth: 0,
      font: { color: '#f3f4f6', size: 12, face: 'ui-sans-serif' },
    },
    edges: {
      arrows: { to: { enabled: true, scaleFactor: 0.5 } },
      color: { color: '#374151', highlight: '#6366f1' },
      font: { color: '#9ca3af', size: 10, strokeWidth: 0, align: 'middle' },
      smooth: { enabled: true, type: 'dynamic', roundness: 0.4 },
      width: 1,
    },
    physics: {
      barnesHut: {
        gravitationalConstant: -3000,
        springLength: 140,
        springConstant: 0.04,
        damping: 0.35,
      },
      stabilization: { iterations: 300, fit: true },
    },
    interaction: { hover: true, tooltipDelay: 200 },
  }
}

function render(snapshot: GraphSnapshot) {
  if (!containerRef.value) return
  const nodes: Node[] = snapshot.nodes.map((n) => ({
    id: n.id,
    label: n.label || n.id,
    color: colorFor(n),
    title: `${n.kind}${n.entity_type ? ` · ${n.entity_type}` : ''}`,
  }))
  const edges: Edge[] = snapshot.edges.map((e, idx) => ({
    id: `e${idx}`,
    from: e.source,
    to: e.target,
    label: e.label,
    dashes: e.valid_to ? [4, 4] : false,
    color: e.current ? undefined : { color: '#6b7280' },
  }))
  if (network) {
    network.setData({ nodes: new DataSet(nodes), edges: new DataSet(edges) })
    return
  }
  network = new Network(
    containerRef.value,
    { nodes: new DataSet(nodes), edges: new DataSet(edges) },
    buildOptions(),
  )
}

onMounted(() => {
  if (props.snapshot) render(props.snapshot)
})

watch(
  () => props.snapshot,
  (s) => { if (s) render(s) },
)

onBeforeUnmount(() => {
  network?.destroy()
  network = null
})
</script>

<template>
  <div ref="containerRef" class="palace-graph">
    <div v-if="!snapshot || (snapshot.nodes.length === 0 && snapshot.edges.length === 0)" class="empty">
      暂无图数据
    </div>
  </div>
</template>

<style scoped>
.palace-graph {
  width: 100%;
  height: 600px;
  background: var(--eid-bg-inset);
  border: 1px solid var(--eid-border);
  border-radius: var(--eid-radius);
  position: relative;
}
.empty {
  position: absolute;
  inset: 0;
  display: flex;
  align-items: center;
  justify-content: center;
  color: var(--eid-text-muted);
  font-size: 14px;
}
</style>
