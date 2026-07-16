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

## Projection model

`RuntimeTurn` and `RuntimeJob` remain the authoritative domain-shaped read
models. `RuntimeActivity` is the UI-neutral correlation layer above them:

- `voice_turn`: one Channel/Agent turn, correlated by trace/turn identifiers.
- `guard_event`: a Guard observation attributed to a companion.
- `device_command` / `device_event`: a Hub fact associated with a body.
- `background_job`: Agent or external-provider work.

Each activity contains ordered `RuntimeRouteHop` facts and its own
`current_hop_id`. There is intentionally no snapshot-wide `active_turn` or
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
