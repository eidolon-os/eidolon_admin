# Admin product integration verification

Date: 2026-08-07 (Asia/Shanghai)

## Scope and revisions

This report covers `codex/admin-product-integration`, based on Admin
`refactor/memory-contracts-v2` commit
`d88fb6f9c8376d5e029037cedcd347e9c581b0c6` and semantically integrating:

- `06e7a2e`: Data V2 / Kernel control-plane boundary;
- `c6274de`: isolated Admin + eidolond + Data + Hub + Kernel runtime wiring;
- Kernel runtime manifest commit
  `301967cb19d3d3802a1de3d58ba059933072c445` on
  `codex/kernel-dev-control-plane-wiring`.

The merge resolution retained Bootstrap, the authenticated Local API, their
systemd/Avahi assets and the independent audit projection. It deleted the
remaining Admin-owned Data ORM/SQLite adapter, Data outbox dispatcher,
Data-reading Memory runner, Mission Control cross-authority aggregation and
their legacy API/Web tests. Operator Admin and Local API have a source guard
against SQLite/SQLAlchemy and foreign authority imports. Bootstrap and audit
may use their own local SQLite state, with a separate guard rejecting any Data,
Kernel or Hub database reference.

## Environment

- macOS 26.5.2 arm64;
- Python 3.13.13;
- Node 24.15.0, pnpm 11.5.2;
- nats-server 2.14.0 for the isolated audit diagnostic;
- all databases, ports, credentials and NATS storage used by validation were
  temporary or below this worktree's `var/os-control-plane`.

The formal Data database remained stopped and unchanged at mtime
`1786001432` (`2026-08-06 15:30:32 +0800`). No formal WAL/SHM file appeared.
Formal Admin and Agent were not started.

## Static, dependency and build verification

```bash
.venv/bin/ruff check server deploy
.venv/bin/python -m compileall -q server/eidolon_admin_server deploy
bash -n deploy/dev/run_all.sh
uv lock --check --offline --project <temporary-sibling-layout>/eidolon_admin
git diff --name-only --diff-filter=ACMR d88fb6f..HEAD -- '*.py' \
  | xargs .venv/bin/ruff format --check
```

Ruff, compileall, shell syntax, lock consistency and the changed-Python format
check passed. A whole-tree `ruff format --check server deploy` was also run and
reported 44 pre-existing files from the product base that would be reformatted;
they were not mechanically mixed into this integration.

```bash
cd web
pnpm test
./node_modules/.bin/vue-tsc --noEmit
pnpm build
```

Frontend result: 6 files / 25 tests passed; type check and production build
passed. Vite emitted the existing third-party PURE-annotation warnings and a
1.24 MB main-chunk warning.

## Backend tests

Final command:

```bash
.venv/bin/pytest server/tests -q \
  --cov=eidolon_admin_server --cov-branch --cov-report=term
```

Final result: **241 passed, 0 failed, 0 skipped**, 24 warnings, 44.51 seconds,
overall branch-aware coverage **70%**. Warnings were one Starlette/httpx
deprecation and 23 `dbus-next` Python deprecation warnings.

Marker runs before the two additional deployment-cleanliness unit tests were:

| Layer | Result |
| --- | ---: |
| unit | 15 passed |
| component | 30 passed |
| contract | 10 passed |
| integration | 4 passed |
| real-process E2E | 1 passed |

The final suite therefore contains 17 unit-marked tests; other counts are
unchanged. The remaining tests are unmarked Bootstrap, Local API, audit and
legacy-neutral Admin infrastructure tests.

Two non-final runs are recorded rather than hidden:

1. collection initially stopped with 8 errors because the pre-integration
   virtual environment lacked newly direct Bootstrap/audit dependencies and
   one stale test still imported the deliberately deleted Admin Data outbox
   dispatcher;
2. after dependency synchronization, 237 tests passed and one contract guard
   failed because it incorrectly banned Bootstrap's own SQLite and the audit
   projection's SQLAlchemy. The guard was split by bounded context, after which
   the final run above passed.

## Runtime preparation evidence

```bash
./deploy/dev/run_all.sh os-control-plane prepare
./deploy/dev/run_all.sh os-control-plane validate
```

Both passed against the integrated tree. Preparation now detects a live
eidolond UDS, removes only a provably stale socket, checkpoints the isolated
Data database and validates it through an immutable read-only connection.
After the final prepare there were no isolated PID, socket, WAL or SHM files.

The full real-process evidence from the immediately preceding runtime-wiring
phase remains reproducible and is not relabelled as a new run: actual Admin,
eidolond, Data V2, Hub and Kernel were started on isolated ports; the service
directory reported all authorities ready; removed Data routes returned 404;
Admin restart recovered; and shutdown removed all processes and database
sidecars. See `2026-08-07-runtime-wiring-phase-1.md`.

## Performance diagnostics (not SLA)

Control-plane business-path results from the real-process E2E on this same
machine and unchanged control-plane implementation were:

| Diagnostic | Result |
| --- | ---: |
| first Hub approval + Kernel mount + Data-backed attachment | 46.32 ms |
| 6 concurrent duplicate mutations | p50 36.46 ms / p95 39.99 ms |
| 20 concurrent Hub + Kernel inventory reads | wall 120.04 ms / p50 105.16 ms / p95 117.70 ms |
| one live inventory source observation | Hub 9.29 ms / Kernel 3.93 ms |

Admin operator has no business SQLite writer. The two legal local-state
services were diagnosed with:

```bash
.venv/bin/python -m deploy.dev.local_state_diagnostics \
  --events 2000 --fetch-batch 200
```

| Diagnostic | Result |
| --- | ---: |
| Bootstrap configured busy timeout | 5000 ms |
| observed contended write failure | 5183.74 ms |
| audit backlog | 2000 events |
| bounded batches | 10 batches, maximum 200 |
| publish time | 13.56 ms |
| projection drain | 291.96 ms / 6850.19 events/s |
| read 200 projected events | p50 2.24 ms / p95 2.52 ms |

The audit run used a temporary JetStream and projection database. It proves a
bounded consumer batch and commit-before-ACK implementation under one finite
backlog; it does not establish sustained producer capacity, disk endurance or
a Raspberry Pi performance target.

## Remaining product work and restart decision

1. Data still exposes one opaque token; product deployment needs independent,
   producer-owned Admin and Kernel service credentials.
2. Admin operator API/Web has no product ingress authentication or production
   serving decision. Local API authentication must not be reused implicitly.
3. Kernel release descriptor V1 still excludes Hub, Admin, Bootstrap and their
   systemd/readiness assets.
4. Data lifecycle/Persona/Memory/Face/Guard mutation contracts remain absent,
   so their deleted Admin screens must remain unavailable.
5. High-frequency telemetry projection remains absent. The audit projection is
   independent and rebuildable but is not a telemetry store.

The integrated code is safe to start with the isolated `os-control-plane`
profile. It is **not yet approved for formal Admin restart** because product
credentials, operator ingress and unified release ownership remain unresolved.
Agent remains outside this startup decision.
