<script setup lang="ts">
/**
 * Phase 33.A3: shared shell for the 5 Phase-29 catalog pages.
 *
 * What it owns:
 *   - the ``.page`` flex-column wrapper + gap
 *   - the standard ``.page-head`` (title / hint / head-actions)
 *   - the title + hint typography (h2 + .hint)
 *
 * What it does NOT own:
 *   - the table / master-detail body (page renders its own via the
 *     default slot — too varied to abstract: tenants is a plain
 *     table, templates/agents are master-detail with selection state,
 *     users has dropdowns inline, devices has 4-state buttons)
 *   - dialogs (each page's create/edit modal is bespoke)
 *   - data loading / error toasts (the page's setup script handles
 *     these — pages already use the shared ``extractErrorMessage``)
 *
 * The point is to centralize the visual chrome so a future "every
 * catalog page gets a trace-id header" or "every catalog page gets
 * a fav-star toggle" change touches ONE file, not five. Body shape
 * stays at the page level.
 */

defineProps<{
  title: string
  /** Optional short paragraph under the title — supports plain HTML
   * via the ``hint-html`` slot if richer formatting is needed (e.g.
   * inline code spans). Pass via prop for plain text. */
  hint?: string
}>()
</script>

<template>
  <div class="page">
    <slot name="breadcrumb" />
    <header class="page-head">
      <div>
        <h2>{{ title }}</h2>
        <p v-if="$slots['hint-html']" class="hint">
          <slot name="hint-html" />
        </p>
        <p v-else-if="hint" class="hint">{{ hint }}</p>
      </div>
      <div class="head-actions">
        <slot name="head-actions" />
      </div>
    </header>

    <slot />
  </div>
</template>

<style scoped>
.page {
  display: flex;
  flex-direction: column;
  gap: 16px;
}
.page-head {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 16px;
}
.page-head h2 {
  margin: 0;
  font-size: 19px;
  font-weight: 760;
  color: var(--eid-text-primary);
  line-height: 1.25;
}
.hint {
  margin: 6px 0 0;
  font-size: 13px;
  color: var(--eid-text-secondary);
  max-width: 720px;
  line-height: 1.65;
}
.head-actions {
  display: flex;
  gap: 8px;
  flex-wrap: wrap;
  justify-content: flex-end;
}
/* Inline ``<code>`` inside hints is used by several pages — keep
   the style here so they don't each re-declare it. */
.hint :deep(code) {
  font-family: var(--eid-font-mono);
  padding: 1px 6px;
  background: var(--eid-bg-panel);
  border-radius: 3px;
}
</style>
