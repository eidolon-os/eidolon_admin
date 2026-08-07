# Eidolon Admin control-plane boundary (Data V2 / Kernel V1)

Status: implemented by the Admin adaptation that accompanies this document.

Evidence baselines:

- `eidolon_data` commit `2a33894` (`refactor: complete system data v2 boundary`)
- `eidolon_kernel` commit `66e61c9` (`feat: align kernel with eidolon data v2 boundary`)
- the public Hub code present at adaptation time (`hub/interfaces/http/routers/device_management.py`)

## Dependency direction

```text
Web / CLI
    -> Admin HTTP interface
    -> Admin application orchestration
    -> Admin-owned Data / Hub / Kernel / System Directory ports
    -> strict HTTP transport adapters
    -> eidolond endpoint directory
    -> bounded-context versioned HTTP contracts
```

Admin is a control-plane product entry and orchestration process. It is not an
Owner, Companion, Device Mount, Device Admission, runtime-event, or audit
authority. It owns no copy of those tables and opens no sibling SQLite file.

Process ownership is separate from business dependency direction. In the
isolated macOS/dev profile, Admin's launch script starts one supervisord daemon
as a host executor and starts eidolond; only eidolond owns desired state and
issues start/stop/restart for Data, Hub and Kernel. Admin API never calls
supervisor to implement a business workflow. The Raspberry Pi profile uses the
same shape with systemd as the host executor.

## Verified producer contracts

| Producer | Public contract used by Admin | Authority |
| --- | --- | --- |
| eidolond | `GET /api/system/v1/services/{service_id}/endpoints/{endpoint_id}` | machine-scoped endpoint discovery and readiness |
| Data V2 | `GET /api/companion-authority/v1/companions/{companion_id}` | Companion identity, Owner membership, lifecycle |
| Hub | Device list + Device approval under `/api/device-management/v1` | Device admission and Owner scope |
| Kernel | Mount/list/Attachment under `/api/kernel/v1/device-mounts*` | Device Mount and optional Companion Attachment |
| Agent | `/api/admin/*` through the existing transparent gateway | Agent-owned runtime/admin read models |
| Memory | `/api/admin/*` through the existing transparent gateway | Memory-owned realm/runtime operations |

The Data V2 producer exposes no public Owner list/mutation, Companion mutation,
Persona Genome, Memory Realm catalog, face asset, Guard binding, Data audit
outbox, or generic event query route. Admin therefore must not reconstruct
those APIs by importing `DataStore` or opening `eidolon-system.sqlite3`.

## Migration matrix

| Previous Admin path | Verified problem | V2 disposition |
| --- | --- | --- |
| `DataStore.open()` + `init_schema()` during Admin startup | Admin becomes a second Data writer/schema owner | deleted; Data is reached only through a strict HTTP client |
| `/api/owners`, `/api/data/*`, onboarding and workspace provisioning | direct Data repositories/application services; producer has no equivalent public Admin contract | removed until Data publishes the narrow management contracts listed below |
| Admin `store.devices` CRUD and legacy Hub `/api/admin/devices` | Data V2 deleted Device; current Hub owns admission under `/api/device-management/v1` | replaced by Hub management client and control-plane routes |
| device `bound_companion_id` in Data | duplicates Kernel Mount/Attachment authority | replaced by Kernel Mount/Attachment API |
| Guard routes mixing Data Device, face metadata and runtime delivery | crosses Data, Hub and runtime authorities in one route | removed; requires separate Data Guard/face management and Guard runtime contracts |
| Mission Control reads `Data.events` and Data Device rows | Data V2 deleted both; global audit is not Data outbox | removed; no cross-database fallback |
| native Memory realm/runners code reads Data SQLite and memory artifacts | cross-authority database/file aggregation | replaced by Memory's public `/api/admin` service |
| `/api/resolve` reads Data Device/Persona/Realm repositories | external DTOs and deleted tables leak into an Admin resolver | removed; runtime resolution belongs to Kernel/Agent public read models |
| Hub discovery/commands/presence/metrics legacy UI | current Hub has no such `/api/admin` contract and admission is not presence | removed; high-frequency presence/telemetry needs its owning projection/API |
| direct Supervisor ownership of Data/Kernel | conflicts with eidolond desired-state authority | Admin discovers Data/Hub/Kernel through eidolond; it does not start formal services |

The explicit `os-control-plane` development profile is not a counterexample:
its child supervisor programs have `autostart=false`; eidolond is the only
component that changes their desired state.

## Device admission and mount workflow

The supported orchestration is an explicit forward-only state machine:

```text
received
  -> hub_approved
  -> kernel_mounted
  -> companion_attached (optional)
  -> completed
```

Every upstream mutation receives a deterministic child request ID derived from
the caller-supplied workflow request ID. Kernel mutations use explicit expected
revision/CAS. Repeating the same workflow after an Admin restart therefore
replays producer-owned idempotency records; Admin does not need a shared
transaction or a local copy of authority state.

Partial-success rules:

- Hub approval committed, Kernel unavailable/rejected: return
  `retry_required` at `hub_approved`. The approved-but-unmounted device is a
  safe producer-defined intermediate state; do not revoke it as compensation.
- Kernel Mount committed, Attachment unavailable/rejected: return
  `retry_required` at `kernel_mounted`. Retry with the same workflow ID; do not
  unmount a valid Device Mount as compensation.
- A response lost after an upstream commit is recovered by replaying the same
  deterministic child request ID.
- Recovery is a forward retry with the same workflow ID, exposed as
  `retry-forward-same-request-id`; if that retry also fails, the response keeps
  the last committed stage and new upstream failure. This workflow deliberately
  has no destructive automatic compensation, so “compensation failure” is not
  disguised as rollback success and no valid Hub approval or Mount is undone.
- Non-retryable CAS/request-ID conflicts return `blocked` with
  `operator-action-required` and an HTTP 409 (or the corresponding 4xx/5xx for
  the failure kind). They are never mislabeled as retryable recovery.

401/403, 404, timeout/connect failure, 5xx and schema drift remain distinct
upstream outcomes. In particular, an unavailable authority is never mapped to
inactive or not found.

## Frequency and audit split

- Data, Hub admission and Kernel Mount mutations are low-frequency control
  facts in their own authority stores.
- Agent turns, Memory work, Channel media, presence, telemetry, logs and metrics
  stay on their owning runtime/query surfaces. Admin does not synchronously
  funnel them into System Data.
- Data `audit_outbox` is private to Data's committed facts. Admin never writes
  it. Kernel local audit is not used as a global timeline, and Admin never
  opens Kernel SQLite.
- No independent global audit projection/publisher contract exists in the
  inspected producers. The old Mission Control timeline is removed instead of
  substituting Data or Kernel SQLite. A future projection must consume stable
  authority facts asynchronously, in batches, with event-ID deduplication and
  observable backpressure.

## Required producer contracts still missing

Restoring the removed product surfaces requires producer-owned, authenticated,
versioned contracts. The minimum useful additions are:

1. Data Admin authority APIs for Owner list/get/create/update/archive; Companion
   list/create/update/archive; Persona Genome commands; Memory Realm catalog;
   face metadata/object transfer; and Guard binding policy. Each mutation needs
   a content-bound request ID and CAS/revision where concurrent edits matter.
2. A narrow runtime-identity read contract if Agent/Channel need an aggregate
   of Companion, current Persona and Memory Realm. It must be producer-owned;
   Admin must not rebuild it from repositories.
3. A rebuildable global audit projection query API and/or standard publisher
   contract for Admin's own committed workflow facts. It must be independent of
   Data outbox and Kernel/Hub databases and expose queue lag/backpressure.
4. An authority-owned high-frequency presence/telemetry read API if a runtime
   cockpit is still required. Audit history is not a telemetry transport.
