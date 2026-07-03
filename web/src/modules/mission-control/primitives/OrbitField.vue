<script setup lang="ts">
// Ambient depth layer (A3.1/A3.2): a single <canvas> rAF loop drawing a drifting
// starfield + soft nebulae behind the whole cockpit. Decoupled from the SVG
// interactive layer so it never blocks hit-testing. GPU-light, DPR-aware, pauses
// when the tab is hidden, and renders one static frame under reduced-motion.
import { onBeforeUnmount, onMounted, ref } from 'vue'
import { prefersReducedMotion } from '../motion'

const canvas = ref<HTMLCanvasElement | null>(null)
let ctx: CanvasRenderingContext2D | null = null
let raf = 0
let start = 0
let w = 0
let h = 0
let dpr = 1
let ro: ResizeObserver | null = null

type Star = { x: number; y: number; r: number; z: number; tw: number; ph: number }
type Nebula = { x: number; y: number; r: number; hue: string; drift: number; ph: number }
let stars: Star[] = []
let nebulae: Nebula[] = []

function setup() {
  const el = canvas.value
  const parent = el?.parentElement
  if (!el || !parent) return
  dpr = Math.min(window.devicePixelRatio || 1, 2)
  w = parent.clientWidth
  h = parent.clientHeight
  el.width = Math.max(1, Math.round(w * dpr))
  el.height = Math.max(1, Math.round(h * dpr))
  el.style.width = `${w}px`
  el.style.height = `${h}px`
  ctx = el.getContext('2d')
  if (ctx) ctx.setTransform(dpr, 0, 0, dpr, 0, 0)
  seed()
}

function seed() {
  const count = Math.round((w * h) / 12000)
  const n = Math.min(160, Math.max(40, count))
  stars = Array.from({ length: n }, () => {
    const z = Math.random()
    return { x: Math.random() * w, y: Math.random() * h, r: 0.4 + z * 1.4, z, tw: 0.4 + Math.random() * 1.2, ph: Math.random() * Math.PI * 2 }
  })
  const big = Math.max(w, h)
  nebulae = [
    { x: w * 0.2, y: h * 0.16, r: big * 0.45, hue: '0,234,255', drift: 0.6, ph: 0 },
    { x: w * 0.82, y: h * 0.1, r: big * 0.4, hue: '255,46,136', drift: 0.5, ph: 1.5 },
    { x: w * 0.5, y: h * 0.78, r: big * 0.5, hue: '164,75,255', drift: 0.4, ph: 3.0 },
  ]
}

function draw(t: number) {
  if (!ctx) return
  const time = (t - start) / 1000
  ctx.clearRect(0, 0, w, h)
  ctx.globalCompositeOperation = 'lighter'
  for (const nb of nebulae) {
    const nx = nb.x + Math.sin(time * 0.05 * nb.drift + nb.ph) * 30
    const ny = nb.y + Math.cos(time * 0.04 * nb.drift + nb.ph) * 24
    const g = ctx.createRadialGradient(nx, ny, 0, nx, ny, nb.r)
    g.addColorStop(0, `rgba(${nb.hue},0.075)`)
    g.addColorStop(0.5, `rgba(${nb.hue},0.028)`)
    g.addColorStop(1, `rgba(${nb.hue},0)`)
    ctx.fillStyle = g
    ctx.fillRect(0, 0, w, h)
  }
  for (const s of stars) {
    const a = (0.25 + 0.55 * (0.5 + 0.5 * Math.sin(time * s.tw + s.ph))) * (0.4 + s.z * 0.6)
    ctx.beginPath()
    ctx.arc(s.x, s.y, s.r, 0, Math.PI * 2)
    ctx.fillStyle = `rgba(207,232,255,${a.toFixed(3)})`
    ctx.fill()
  }
  ctx.globalCompositeOperation = 'source-over'
}

function loop(t: number) {
  if (!start) start = t
  draw(t)
  raf = requestAnimationFrame(loop)
}

function onVisibility() {
  if (document.hidden) {
    if (raf) cancelAnimationFrame(raf)
    raf = 0
  } else if (!raf && !prefersReducedMotion()) {
    raf = requestAnimationFrame(loop)
  }
}

onMounted(() => {
  setup()
  ro = new ResizeObserver(() => setup())
  if (canvas.value?.parentElement) ro.observe(canvas.value.parentElement)
  if (prefersReducedMotion()) {
    requestAnimationFrame((t) => { start = t; draw(t) })
  } else {
    raf = requestAnimationFrame(loop)
  }
  document.addEventListener('visibilitychange', onVisibility)
})

onBeforeUnmount(() => {
  if (raf) cancelAnimationFrame(raf)
  ro?.disconnect()
  document.removeEventListener('visibilitychange', onVisibility)
})
</script>

<template>
  <canvas ref="canvas" class="orbit-field" aria-hidden="true" />
</template>

<style scoped>
.orbit-field { position: absolute; inset: 0; width: 100%; height: 100%; display: block; pointer-events: none; }
</style>
