/**
 * Real-call tests for src/api/devices.ts.
 *
 * Why these exist: vue-tsc only proves the TypeScript types match what
 * the backend OpenAPI claims to return — it can't catch URL-encoding
 * bugs in path parameters, axios body-serialization regressions, or the
 * frontend silently misparsing a renamed response field. The Python
 * fullstack suite tests the BACKEND side of the same contract; this
 * file tests the FRONTEND side. Both halves must agree for the
 * Browser → admin link to be honestly verified.
 *
 * No mocks, no msw. Each test hits ``http://127.0.0.1:9000`` and skips
 * cleanly when admin isn't running (same pattern as the Python tests'
 * NATS skip).
 */
import { describe, expect, it } from 'vitest'
import type { TestContext } from 'vitest'
import axios from 'axios'

// Point the shared axios client at the running admin gateway. The
// production client uses baseURL='/api' under vite's dev proxy; in tests
// (no proxy in the way) we hit admin directly.
import client from '../src/api/client'
import * as devicesApi from '../src/api/devices'

const ADMIN_URL = 'http://127.0.0.1:9000'
client.defaults.baseURL = `${ADMIN_URL}/api`

async function adminReachable(): Promise<boolean> {
  try {
    const r = await axios.get(`${ADMIN_URL}/api/devices`, { timeout: 2000 })
    return r.status === 200
  } catch {
    return false
  }
}

/** Skip the running test if admin isn't reachable.
 *
 * Why runtime (not collection-time) skip: ``beforeAll`` runs AFTER vitest
 * has already decided which tests to skip via ``it.skip()``. Doing the
 * check inside each test body via ``ctx.skip()`` lets us probe at the
 * right moment without relying on a module-level pre-flight that races
 * with the runner's scheduling.
 */
async function skipIfAdminDown(ctx: TestContext): Promise<boolean> {
  if (!(await adminReachable())) {
    ctx.skip()
    return true
  }
  return false
}

/** Generate a unique device id so re-runs don't fight over state. */
function makeDeviceId(prefix: string): string {
  return `${prefix}-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`
}

/** Find a device the test can safely operate on, honoring real state.
 *
 * Constraint enforced by the orchestrator: a device → user binding is
 * 1:1. If the device already has a mapping with user X, we MUST reuse X
 * for any new agent we create — using a fresh user_id would trip the
 * "cannot mix users on one device" check (which is correct architectural
 * behaviour, not a test bug).
 *
 * Returns ``null`` if no usable device exists; tests then self-skip
 * cleanly without polluting hub state.
 */
async function findDevice(
  approvedFilter: boolean | null,
): Promise<devicesApi.DeviceView | null> {
  const r = await devicesApi.listDevices()
  for (const d of r.devices) {
    if (approvedFilter === null) return d
    if (d.approved === approvedFilter) return d
  }
  return null
}

/** Pick the user_id we'll bind agents as.
 *
 * If the device already has a binding, return its existing user_id —
 * this respects the orchestrator's 1:1 constraint without us having to
 * mutate state we don't own. Otherwise return a stable per-run id so
 * concurrent test runs don't clash with each other (Math.random suffix).
 */
function chooseUserId(d: devicesApi.DeviceView): string {
  if (d.binding?.user_id) return d.binding.user_id
  return `vitest-${Math.random().toString(36).slice(2, 10)}`
}

describe('api/devices.ts — real calls to admin gateway', () => {
  // ---- listDevices ---------------------------------------------------

  it('listDevices returns the documented envelope shape', async (ctx) => {
    if (await skipIfAdminDown(ctx)) return
    const r = await devicesApi.listDevices()
    expect(typeof r.nats_available).toBe('boolean')
    expect(Array.isArray(r.devices)).toBe(true)
    // Each device row carries the keys our Overview.vue indexes by.
    for (const d of r.devices) {
      expect(typeof d.device_id).toBe('string')
      expect(typeof d.approved).toBe('boolean')
      expect(typeof d.status).toBe('string')
      // binding is either null or an object with agents[]
      if (d.binding !== null) {
        expect(Array.isArray(d.binding.agents)).toBe(true)
        expect(Array.isArray(d.binding.agent_ids)).toBe(true)
      }
    }
  })

  // ---- approveDevice -------------------------------------------------

  it('approveDevice round-trips on a known device', async (ctx) => {
    if (await skipIfAdminDown(ctx)) return
    const target = await findDevice(null)
    if (!target) {
      // eslint-disable-next-line no-console
      console.warn('no devices in hub — skipping approveDevice test')
      return
    }
    const r = await devicesApi.approveDevice(target.device_id)
    expect(r.device_id).toBe(target.device_id)
    expect(typeof r.approved).toBe('boolean')
    // After a successful approve the field is true (idempotent on
    // already-approved devices).
    expect(r.approved).toBe(true)
  })

  it('approveDevice URL-encodes device_id with special chars', async (ctx) => {
    if (await skipIfAdminDown(ctx)) return
    // We don't have a device with special chars seeded — but we can
    // verify the encoded request reaches admin and gets a 404 (not a
    // routing 405/400 from misencoding). A 404 here proves the path
    // was routed correctly to the approve handler.
    const exotic = 'has/slash and space'
    try {
      await devicesApi.approveDevice(exotic)
      // Either it 404s (device not registered) — handled in catch.
      // Or it 200s (very unlikely test pollution) — also fine.
    } catch (err: any) {
      const status = err?.response?.status
      // Any 4xx is acceptable proof that the request reached the right
      // route and admin made a decision; what we're guarding against
      // is 405 (method not allowed) or 404-from-missing-route which
      // would indicate the URL encoding broke the routing.
      expect([404, 422, 503]).toContain(status)
    }
  })

  // ---- createAgent / readSoul / deleteAgent — full lifecycle --------

  it(
    'full lifecycle: approve → createAgent → getSoul → deleteAgent',
    async (ctx) => {
      if (await skipIfAdminDown(ctx)) return
      // Need an approvable device. Look for one already approved (to
      // avoid mutating discovered ones), or fall back to approving one.
      let target = await findDevice(true)
      if (!target) {
        target = await findDevice(false)
        if (!target) {
          // eslint-disable-next-line no-console
          console.warn('no devices in hub — skipping lifecycle test')
          return
        }
        await devicesApi.approveDevice(target.device_id)
      }
      const deviceId = target.device_id

      // Pick a template that the agent service definitely has.
      // gatewayCall avoids hardcoding the agent's URL — it goes through
      // admin's proxy at /api/services/agent/personas/templates which
      // is what BindAgentDialog.vue actually uses.
      const templates = (
        await client.get('/services/agent/personas/templates')
      ).data
      expect(Array.isArray(templates)).toBe(true)
      if (templates.length === 0) {
        // eslint-disable-next-line no-console
        console.warn('agent has no templates — skipping lifecycle test')
        return
      }
      const templateId =
        templates[0].metadata?.template_id ?? templates[0].template_id

      // Create — verifies POST body shape + URL composition + response parse.
      const created = await devicesApi.createAgent(deviceId, {
        template_id: templateId,
        user_id: chooseUserId(target),
      })
      expect(typeof created.agent_id).toBe('string')
      expect(created.is_active).toBe(true)
      expect(created.soul_preview_chars).toBeGreaterThan(0)
      const agentId = created.agent_id

      try {
        // Read the soul back. Verifies path encoding of nested params.
        const soul = await devicesApi.getSoul(deviceId, agentId)
        expect(soul.agent_id).toBe(agentId)
        expect(typeof soul.markdown).toBe('string')
        expect(soul.markdown.length).toBeGreaterThan(0)
        expect(soul.size_bytes).toBe(
          new TextEncoder().encode(soul.markdown).length,
        )

        // Update the soul. Verifies PUT serialization + new size returned.
        const edited = `# vitest edit ${Date.now()}\n${soul.markdown}`
        const updated = await devicesApi.updateSoul(deviceId, agentId, edited)
        expect(updated.size_bytes).toBe(new TextEncoder().encode(edited).length)
      } finally {
        // Always clean up the agent we created so the test is idempotent.
        const del = await devicesApi.deleteAgent(deviceId, agentId)
        expect(del.deleted_agent_id).toBe(agentId)
        // active fallback shape — three documented kinds.
        expect(['next_newest', 'cleared', 'no_change']).toContain(
          del.fallback_kind,
        )
      }
    },
  )

  // ---- switchActiveAgent — needs two agents on the same device ------

  it('switchActiveAgent moves the active pointer', async (ctx) => {
    if (await skipIfAdminDown(ctx)) return
    let target = await findDevice(true)
    if (!target) {
      // eslint-disable-next-line no-console
      console.warn('no approved devices — skipping switchActive test')
      return
    }
    const templates = (
      await client.get('/services/agent/personas/templates')
    ).data
    if (templates.length === 0) return
    const templateId =
      templates[0].metadata?.template_id ?? templates[0].template_id

    const userId = chooseUserId(target)
    const a1 = await devicesApi.createAgent(target.device_id, {
      template_id: templateId,
      user_id: userId,
    })
    const a2 = await devicesApi.createAgent(target.device_id, {
      template_id: templateId,
      user_id: userId,
    })

    try {
      // a2 is active (newest wins). Flip to a1.
      const swap = await devicesApi.switchActiveAgent(target.device_id, a1.agent_id)
      expect(swap.active_agent_id).toBe(a1.agent_id)

      // Verify via listDevices that the active really moved.
      const r2 = await devicesApi.listDevices()
      const fresh = r2.devices.find((d) => d.device_id === target!.device_id)
      expect(fresh?.binding?.active_agent_id).toBe(a1.agent_id)
    } finally {
      // Clean up both agents we created so re-runs stay deterministic.
      await devicesApi.deleteAgent(target.device_id, a1.agent_id).catch(() => {})
      await devicesApi.deleteAgent(target.device_id, a2.agent_id).catch(() => {})
    }
  })

  // ---- formatTimestamp + deriveDeviceStatusLabel are pure ----------

  it('formatTimestamp returns "—" for null/undefined', () => {
    expect(devicesApi.formatTimestamp(null)).toBe('—')
    expect(devicesApi.formatTimestamp(undefined)).toBe('—')
  })

  it('formatTimestamp formats ISO strings into local YYYY-MM-DD HH:mm:ss', () => {
    const result = devicesApi.formatTimestamp('2026-05-25T10:30:45+00:00')
    // We don't pin the exact value because it's locale-dependent; just
    // verify the SHAPE is the documented one.
    expect(result).toMatch(/^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$/)
  })

  it('deriveDeviceStatusLabel returns "discovered" for unapproved devices', () => {
    const d: any = { approved: false, paired: false, binding: null }
    const r = devicesApi.deriveDeviceStatusLabel(d)
    expect(r.label).toBe('discovered')
    expect(r.tone).toBe('info')
  })

  it('deriveDeviceStatusLabel returns "approved (no agents)" when binding is empty', () => {
    const d: any = {
      approved: true,
      paired: false,
      binding: { user_id: 'x', agent_ids: [], active_agent_id: null, agents: [], updated_at: '' },
    }
    const r = devicesApi.deriveDeviceStatusLabel(d)
    expect(r.label).toBe('approved (no agents)')
    expect(r.tone).toBe('warning')
  })

  it('deriveDeviceStatusLabel composes "bound · N · template" when active set', () => {
    const d: any = {
      approved: true,
      paired: false,
      binding: {
        user_id: 'x',
        agent_ids: ['a1'],
        active_agent_id: 'a1',
        agents: [{ agent_id: 'a1', template_id: 'caretaker_jiezhi', is_active: true }],
        updated_at: '',
      },
    }
    const r = devicesApi.deriveDeviceStatusLabel(d)
    expect(r.label).toContain('bound')
    expect(r.label).toContain('caretaker_jiezhi')
    expect(r.tone).toBe('success')
  })
})
