# Runtime wiring phase 1 report

Date: 2026-08-07 (Asia/Shanghai)

## Scope and repository evidence

- Admin base adaptation: `8d6a2c88c5a21571d6750b314ec72962ed6fd238`.
- Data producer: `2a338940681fb281e1d962f891f2d675a0bb97b5`.
- Kernel inspected head before this phase: `40d9b67602f428f848ce3c5b7f0471274086e93f`.
- Hub inspected head: `a91ea8356f79da75b359faf9d90cace6d4a07ffb`.

Kernel's product systemd manifest already published Data/Hub/Kernel. Its macOS/dev manifest published only Hub. Admin had no Data/Kernel/eidolond supervisor entries, no isolated credential materialization and no deployment preflight that could prove it was not using the formal Data database.

## Implemented boundary

`./deploy/dev/run_all.sh os-control-plane prepare` now:

1. resolves sibling repositories correctly from normal checkouts and linked worktrees;
2. creates `var/os-control-plane/{env,config,data}` with directory mode `0700`;
3. creates or reuses sandbox credentials in mode `0600` without printing their values;
4. points Data, Hub, Kernel, eidolond and Admin only at the isolated runtime;
5. applies the tracked Data V2 Alembic baseline to the isolated database;
6. requires the exact Data/Hub/Kernel endpoint identity, address, protocol, contract and supervisord target;
7. validates the exact ten-table Data V2 schema, Alembic version, integrity and foreign keys;
8. validates all real entrypoints and the composed supervisord profile without starting it.

The profile starts supervisord only as host executor. Data/Hub/Kernel programs have `autostart=false`; eidolond imports the manifest into its isolated desired-state store and starts them.

## Reproducible checks and actual results

```bash
.venv/bin/pytest -q \
  server/tests/test_dev_control_plane_deployment.py \
  server/tests/test_ports.py
```

Result: `20 passed, 0 failed, 0 skipped` in `0.50s`.

```bash
cd /Users/manson/ai/eidolon/eidolon_kernel
.venv/bin/pytest -q tests/system/test_systemd_deployment.py
.venv/bin/ruff check tests/system/test_systemd_deployment.py
```

Focused result: `8 passed, 0 failed, 0 skipped` in `0.13s`; pytest emitted one cache-write warning because the initial sandboxed run could not write Kernel's `.pytest_cache`. Ruff passed after running with sibling-worktree write permission.

Final Kernel full regression:

```bash
.venv/bin/ruff check eidolon_kernel eidolon_system eidolon_deploy tests
.venv/bin/pytest -q
```

Result: Ruff passed; `219 passed, 0 failed, 0 skipped` in `13.60s`.

```bash
./deploy/dev/run_all.sh os-control-plane prepare
```

Result: preparation, Data migration/schema checks and Supervisor syntax validation passed. Formal `/Users/manson/eidolon/data/eidolon-system.sqlite3` mtime remained `1786001432` (`2026-08-06 15:30:32 +0800`), and no formal WAL/SHM/temp file appeared.

The first implementation incorrectly treated Supervisor `-t` as a config-test flag; in Supervisor 4 it means `strip_ansi` and therefore started the isolated daemon. The profile was stopped and verified clean. The final implementation calls `supervisor.options.ServerOptions.realize()` to parse config without opening a socket, PID file or log; a regression test proves no daemon artifacts are created.

Final Admin full regression:

```bash
.venv/bin/pytest server/tests -q \
  --cov=eidolon_admin_server --cov-branch --cov-report=term
```

Result: `177 passed, 0 failed, 0 skipped`, one Starlette/httpx deprecation warning, overall branch-aware combined coverage `72%`, in `41.97s`. Layer markers: unit `15`, component `30`, contract `9`, integration `4`, real-process E2E `1`.

Frontend regression remained `6` files / `25` tests passed; `vue-tsc --noEmit` and Vite production build passed. Vite retained the existing third-party PURE annotation and >500 kB chunk warnings.

## Real-process validation

The isolated profile launched the actual Admin, eidolond, Data V2, Hub and Kernel entrypoints. Agent was not launched. Evidence:

- eidolond UDS directory reported Data, Hub and Kernel `runtime_state=ready` and returned the exact consumed endpoint contracts;
- Admin `GET /api/control-plane/v1/capabilities` returned 200;
- removed `GET /api/data/owners` returned 404;
- missing Companion returned 404 through real Admin -> eidolond UDS -> Data, rather than an unavailable/inactive fabrication;
- a short-lived locally signed Hub test JWT exercised `GET /api/control-plane/v1/owners/owner-isolated/inventory`; response was 200, `degraded=false`, with empty Hub devices and Kernel mounts;
- observed source latency was Hub `9.29ms`, Kernel `3.93ms` in this single request. These are local diagnostics, not SLA data;
- restarting only `admin:admin-api` through the profile recovered the capabilities route;
- Data process open files proved it used `var/os-control-plane/data/eidolon-system.sqlite3`;
- profile shutdown exited all six supervisor/authority PIDs and removed isolated WAL/SHM files.

The eidolond profile intentionally serves its API over `var/os-control-plane/eidolond.sock`; the configured TCP base URL is only an HTTP authority name for the UDS transport. Operational readiness therefore uses supervisor state, while Admin business resolution uses the typed UDS directory client.

## Remaining work

- This phase is a development/runtime connection, not permission to start the formal Admin/Agent environment.
- Data needs producer-owned per-consumer credentials before a product deployment can independently rotate Admin and Kernel access.
- Raspberry Pi release activation currently switches only Kernel/Data/SDK. Hub/Admin units, built web ingress, product operator authentication, target-native preparation/transfer and first-install handling remain separate phases documented in `docs/deployment/raspberry-pi-unified-deployment.md`.
- Admin's parallel `refactor/memory-contracts-v2` branch contains Bootstrap/Local API product work and overlaps this boundary branch in 35 changed files. It was not modified or merged in this phase; semantic branch reconciliation is required before extending the product release descriptor.
