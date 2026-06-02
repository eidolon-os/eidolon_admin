/**
 * Unit test for ``src/modules/common/CatalogPage.vue`` — the shared
 * layout shell used by the five catalog pages
 * (tenants/templates/users/agents/devices).
 *
 * Pure SSR render via ``vue/server-renderer`` — no @vue/test-utils so
 * we don't pull a new dev dep in just to verify a shell component.
 * The contract we're locking down here is small but important: every
 * catalog page renders the same chrome (``.page > .page-head``), the
 * title/hint props land where they should, and the three slots
 * (``head-actions``, ``hint-html``, default) compose without
 * surprises. Locked because all 5 catalog pages now depend on it; if
 * the shell changes shape they all silently break.
 */
import { createSSRApp, h } from 'vue'
import { renderToString } from 'vue/server-renderer'
import { describe, expect, it } from 'vitest'
import CatalogPage from '../src/modules/common/CatalogPage.vue'

async function render(props: Record<string, unknown>, slots: Record<string, () => unknown> = {}) {
  const app = createSSRApp({
    render: () => h(CatalogPage, props, slots),
  })
  return await renderToString(app)
}

describe('CatalogPage', () => {
  it('renders title in <h2> and applies .page / .page-head chrome', async () => {
    const html = await render({ title: '租户管理' })
    // <h2> picks up scoped-style data-v-* attrs at SSR — match by content.
    expect(html).toMatch(/<h2[^>]*>租户管理<\/h2>/)
    expect(html).toMatch(/class="page"/)
    expect(html).toMatch(/class="page-head"/)
  })

  it('renders hint prop as plain text in <p class="hint">', async () => {
    const html = await render({ title: 'X', hint: 'simple hint' })
    expect(html).toMatch(/<p[^>]*class="hint"[^>]*>simple hint<\/p>/)
  })

  it('hint-html slot wins over hint prop and allows nested markup', async () => {
    const html = await render(
      { title: 'X', hint: 'should-not-appear' },
      { 'hint-html': () => [h('code', 'default')] },
    )
    expect(html).not.toContain('should-not-appear')
    expect(html).toMatch(/<code[^>]*>default<\/code>/)
  })

  it('renders nothing for hint when neither prop nor slot provided', async () => {
    const html = await render({ title: 'X' })
    expect(html).not.toMatch(/class="hint"/)
  })

  it('mounts head-actions slot into the right-hand .head-actions container', async () => {
    const html = await render(
      { title: 'X' },
      { 'head-actions': () => [h('button', { class: 'refresh-btn' }, 'Refresh')] },
    )
    // The head-actions slot is wrapped in a <div class="head-actions">
    expect(html).toMatch(/<div[^>]*class="head-actions"[^>]*>.*refresh-btn.*Refresh.*<\/div>/s)
  })

  it('renders default slot as the page body', async () => {
    const html = await render(
      { title: 'X' },
      { default: () => [h('section', { class: 'body-marker' }, 'body content')] },
    )
    expect(html).toMatch(/<section[^>]*class="body-marker"[^>]*>body content<\/section>/)
  })
})
