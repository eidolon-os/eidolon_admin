# Mission Control runtime projection

Mission Control is an observatory, not a runtime coordinator. The production
systems continue to own their domains:

- Channel owns voice turn/EOT/interruption state.
- Hub owns device presence and commands.
- Agent and memory own turns, jobs, tools, and memory work.
- `eidolon_data.events` is the shared, redacted evidence ledger.

Mission Control only reads those facts and projects them into
`RuntimeActivity[]`. It never publishes audio/transcripts, completes turns,
changes a command, or schedules a job. Projection or SSE failure therefore
degrades the page only; it cannot change the normal runtime flow.

Device runtime availability and capability contracts are a separate current
fact surface. Mission Control reads exactly one key per selected owner from the
Hub-owned JetStream KV bucket `EIDOLON_RUNTIME_DEVICES`:

`owner.<sha256(owner_id)>.current`

The Admin does not create or write this bucket, scan other owner keys, Watch
updates, or cache snapshot values. Every snapshot page request performs a fresh
KV `get` and validates the strict SDK schema v2 plus the embedded `owner_id`.
Only a ready snapshot with a live Hub lease may project online devices and
capability names. Missing, malformed, foreign-owner, not-ready, and expired
snapshots fail closed; persisted `devices.capabilities_json` is inventory
metadata and is never used as an online capability fallback.

The Mission Control UI follows the same boundary: it may select, filter, and
open observed facts, but it does not create/bind/start a body. Those operations
belong to the normal Devices and Companion administration surfaces.

## Projection model

`RuntimeTurn` and `RuntimeJob` remain the authoritative domain-shaped read
models. `RuntimeActivity` is the UI-neutral correlation layer above them:

- `voice_turn`: one Channel/Agent turn, correlated by trace/turn identifiers.
- `guard_event`: a Guard observation attributed to a companion.
- `device_command` / `device_event`: a Hub fact associated with a body.
- `background_job`: Agent or external-provider work.

Each activity contains ordered `RuntimeRouteHop` facts and its own
`current_hop_id`. There is intentionally no snapshot-wide active-voice field or
global playhead: several companions and several activity kinds may be active at
the same time.

## Attribution

Older audit and Hub events may contain `device_id` without `companion_id`.
Mission Control resolves that missing scope from the authoritative
device-to-companion binding at ingestion. Snapshot events use the already
loaded device set; live SSE events use a best-effort repository lookup. The
originating event remains unchanged.

The frontend consumes only the projected activities for concurrency, route
highlighting, activity labels, and service hot spots. Voice-specific turn data
is retained solely for the detailed voice trace drawer.

## Connections and isolation

- Snapshot reads are owner-scoped and fan out only to existing read surfaces.
- The live stream merges the shared event-ledger tail with Hub's global SSE.
  Device-only Hub frames are attributed through the current persisted binding,
  then filtered back to the requested owner. Unattributed global Hub frames are
  not assigned to the selected owner.
- Opening or losing Mission Control does not join a LiveKit room and does not
  start, interrupt, or reconcile a Channel session. Channel facts arrive through
  the shared ledger; Channel remains the sole owner of voice lifecycle.

## Concurrency guarantees

- Every activity has its own route and `current_hop_id`; the substrate may show
  several hot services and companion labels simultaneously.
- A companion may own multiple devices. Device ports remain distinct and a
  Guard/device fact inherits the companion bound to its originating device.
- Several companions may be active at once. Focusing one companion only scopes
  drawers and event lists; it does not replace the owner-level activity set.
- Voice spans are filtered to the selected/focused voice turn. They are a detail
  view and never act as the playhead for Guard, device, or background activities.
