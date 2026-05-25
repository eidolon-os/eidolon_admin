# eidolon_admin

Unified admin gateway for the Eidolon ecosystem.

A thin FastAPI gateway plus a Vue 3 (Element Plus) SPA. The gateway forwards
HTTP requests to each sub-project's existing admin API, exposes a one-click
Deploy/Dev panel that drives every sub-project's `deploy/dev/run_all.sh`, and
declares all integration through a single YAML file.

## Integration spectrum

Each sub-project is integrated via one of four modes (declared in
`config/services.yaml`'s `integration:` field):

| Mode      | What it means                                                                                              | Examples       |
| --------- | ---------------------------------------------------------------------------------------------------------- | -------------- |
| `native`  | Admin endpoints implemented **inside this gateway** at `/api/<id>/*`. Talks to the sub-project via external protocols only (MCP / NATS / files). | `memory`       |
| `proxy`   | Transparent HTTP forward to the sub-project's existing admin API at `base_url + upstream_prefix`.          | `hub`, `agent` |
| `process` | No HTTP/NATS admin surface; supervisord owns lifecycle; UI shows status + logs + read-only config.         | `channel`      |
| `infra`   | Shared infrastructure listed only in supervisor configs.                                                   | `nats`         |

### Per-project notes

- **`memory`** — fully native. `legacy/admin/` in the memory repo can be
  deleted; this gateway provides all of Users / Memories / Search / Recall /
  KG / Graph / Hierarchy / MCP Tools natively via MCP HTTP + NATS JetStream +
  `users.yaml` + SIGHUP to memory-supervisor.
- **`hub`** — proxy. Hub's in-memory state (commands, probe stats, presence)
  and tight LiveKit integration make native re-implementation worse, not
  better. Our gateway relays `/api/services/hub/*` → `:8082/api/admin/*` and
  provides a polished UI on top. Hub's Next.js `client/web` and Vite
  `client/admin` are NOT under our supervisord — they remain hub-project
  concerns.
- **`agent`** — proxy. PersonasService is dense domain logic; we proxy to it
  rather than vendor it. Agent's own `admin_web/` can be retired once you're
  satisfied with our UI.
- **`channel`** — process-only. LiveKit voice worker with no HTTP/NATS admin
  surface. We expose process status (from supervisord) and a read-only view
  of `deploy/.livekit-channel.env` (secrets masked) — no channel project
  changes required.
- **`nats`** — shared infra under our supervisord (`nats-server` on
  `:4222` / `:8222` JetStream). One source of truth across the stack.
- **`livekit`** — shared infra under our supervisord (`livekit-server` on
  `:7880`). Config at `deploy/livekit/livekit.yaml`; hub and channel only
  need matching `LIVEKIT_API_*` in their `.env`.

### Port registry

Dev bind ports live in **`config/ports.yaml`**. `./deploy/dev/run_all.sh`
exports `EIDOLON_*` env vars for supervisord, syncs sub-project
`settings.yaml` / LiveKit config, and drives `services.yaml` URL expansion.
Change a port there, then `./deploy/dev/run_all.sh restart`.

### Adding a new sub-project

1. Write `deploy/supervisor/available/<id>.conf` (use `wrappers/with-env.sh`
   if it needs a `.env` loaded).
2. Append a `services[]` entry to `config/services.yaml` with the right
   `integration:` mode and `supervisor:` block.
3. (For `native` only) implement `server/eidolon_admin_server/app/<id>/`
   following the memory module shape (`schemas.py`, `router.py`, `routers/`,
   protocol clients in their own files).
4. (For UI) add page components under `web/src/modules/<id>/` and register
   them in `FeatureDispatcher.vue`. Unmapped features fall back to the
   generic `ApiConsole`.

## Architecture

```
┌─────────────────┐     /api/services/{id}/...     ┌────────────────────┐
│   Vue 3 SPA     │ ─────────── proxy ───────────► │  FastAPI gateway   │
│  (Element Plus) │                                │   (port 9000)      │
│   port 9001     │ ◄──── JSON / SSE / binary ──── │                    │
└─────────────────┘                                └────────┬───────────┘
                                                            │
                                              ┌─────────────┼──────────────┐
                                              ▼             ▼              ▼
                                       eidolon_agent  eidolon_hub  eidolon_memory
                                       (whatever port is configured in services.yaml)
```

The gateway has no business logic. It does four things:

1. **Forward** requests under `/api/services/{service_id}/{sub_path}` to the
   right upstream (`base_url + upstream_prefix + sub_path`).
2. **Probe** each upstream's `health` endpoint concurrently for the Health
   panel.
3. **Drive** each sub-project's `deploy/dev/run_all.sh` via a tightly
   whitelisted subprocess runner — one-click start/stop/restart.
4. **Serve** the SPA configuration (`/api/services` returns the menu).

## Repo layout

```
config/services.yaml          # service registry — edit this to add projects
config/ports.yaml             # dev stack port registry (single source of truth)
docs/                         # cross-project conventions (config, reference loaders)
server/                       # FastAPI gateway
  eidolon_admin_server/
    app/
      main.py                 # FastAPI factory
      settings.py             # services.yaml loader (Pydantic)
      gateway/                # proxy + router + registry
      deploy/                 # safe runner + deploy router
      routers/                # /api/services, /api/health
  tests/
web/                          # Vue 3 SPA (Vite)
  src/
    api/                      # axios client, service & deploy clients
    layouts/                  # AdminLayout with dynamic menu
    modules/                  # one folder per service (+ deploy, common, health)
```

## Getting started

### One-shot (supervisord-managed stack)

This wrapper owns three things:

1. The admin **gateway api** (uvicorn :9000)
2. The admin **web** (vite :9001)
3. A **supervisord daemon** that launches every sub-project declared under
   `deploy/supervisor/enabled/*.conf`.

```bash
./deploy/dev/run_all.sh                  # foreground admin only (Ctrl+C exits)
./deploy/dev/run_all.sh start            # supervisord + admin
./deploy/dev/run_all.sh stop             # reverse
./deploy/dev/run_all.sh restart
./deploy/dev/run_all.sh status           # admin + supervisorctl status

# Granular control
./deploy/dev/run_all.sh start-admin | stop-admin | restart-admin | status-admin
./deploy/dev/run_all.sh start-sv   | stop-sv   | status-sv

# Direct supervisorctl passthrough
./deploy/dev/run_all.sh sv status
./deploy/dev/run_all.sh sv tail -f memory:memory-supervisor

# Pre-supervisord wrapper (drives each sub-project's run_all.sh directly)
./deploy/dev/run_all.sh --legacy status
```

Per-program control (start / stop / restart of `memory-supervisor`, etc.)
happens via the admin UI's **Supervisor** page or via `sv` passthrough — this
top-level script doesn't touch individual programs.

First run auto-creates `.venv`, installs `eidolon-admin` (which brings
supervisord), and runs `pnpm install` (or `npm install`) for the web. PID /
log files for the gateway live under `~/eidolon/run/eidolon-admin-gateway-*.pid`
and `~/eidolon/logs/eidolon-admin-gateway-*.log`. The supervisord daemon
itself stores its pid + socket under `var/` inside this repo (so the unix
socket path stays short enough for macOS's 104-byte limit).

### Adding a new sub-project to the supervisor

1. Write `deploy/supervisor/available/<id>.conf` (`[program:...]` blocks +
   optional `[group:<id>]`).
2. Enable it — either from the admin UI's **Supervisor** page (toggle switch
   on the config card) or manually:
   ```bash
   ln -snf ../available/<id>.conf deploy/supervisor/enabled/<id>.conf
   ./deploy/dev/run_all.sh sv reread
   ./deploy/dev/run_all.sh sv update
   ```
3. (Optional) add a `supervisor:` block to `config/services.yaml` so the UI
   knows which programs belong to which service card:
   ```yaml
   - id: my-service
     name: My Service
     ...
     supervisor:
       config_file: deploy/supervisor/available/my-service.conf
       group: my-service
       programs: [my-prog-1, my-prog-2]
   ```

### Manual (each part separately)

```bash
# Backend
.venv/bin/eidolon-admin              # uvicorn on 127.0.0.1:9000
.venv/bin/pytest server/tests/ -v

# Frontend
cd web && pnpm dev                   # Vite on 127.0.0.1:9001
```

Open <http://127.0.0.1:9001>. Vite proxies `/api` → `http://127.0.0.1:9000`.

### End-to-end: start every sub-project from the UI

1. Boot the gateway and the frontend (above).
2. Open the **Deploy / Dev** page (the default landing page).
3. Click **全部启动** — the gateway invokes each sub-project's
   `deploy/dev/run_all.sh start` in parallel.
4. The Health panel turns green once each upstream comes online.

Run output streams into a side drawer; logs declared in `services.yaml` are
viewable from the same page.

## Adding a new sub-project

Append a block to `config/services.yaml` and restart the gateway:

```yaml
services:
  - id: my-service
    name: My Service
    base_url: http://127.0.0.1:8123
    upstream_prefix: /api/admin
    auth: { type: none }
    health: /api/admin/health
    deploy:
      script: /path/to/my-service/deploy/dev/run_all.sh
      cwd:    /path/to/my-service
      commands: [start, stop, restart, status]
      log_files:
        - /path/to/my-service/logs/app.log
    features:
      - { key: dashboard, label: "Dashboard" }
      - { key: console,   label: "API Console" }
```

The menu picks it up automatically. Endpoints not yet covered by a hand-written
page fall back to the generic **API Console** (an HTTP request builder bound to
this service), so day-one coverage is complete by construction.

## Deploy / Dev runner — safety model

The runner is the only part of the gateway that executes arbitrary shell.
Hardening:

- **Command whitelist** — only commands listed in `deploy.commands` for the
  service are accepted; anything else returns 400 before any process spawn.
- **No shell** — uses `asyncio.create_subprocess_exec(script, command, ...)`
  with positional args. Shell metacharacters in the command parameter cannot
  reach a shell.
- **Path validation** — script existence + executability checked at request
  time; log files must match the configured `log_files` whitelist exactly
  (after `~`/`$VAR` expansion + resolve).
- **Timeouts** — 30s for status/reload, 120s for start/stop/restart; SIGTERM
  on timeout, SIGKILL 5s later if still alive.
- **Per-service lock** — concurrent invocations on the same service serialise
  via `asyncio.Lock` so two starts can't race.
- **Restart degradation** — services whose script lacks native `restart` (e.g.
  `hub`) get a synthesised `stop → sleep 1s → start` in the gateway, leaving
  the sub-project script untouched.

## Configuration via env

| Env var                     | Default                                  | Purpose                                              |
| --------------------------- | ---------------------------------------- | ---------------------------------------------------- |
| `EIDOLON_ADMIN_SERVICES_FILE` | `<repo>/config/services.yaml`          | Path to the service registry                         |
| `EIDOLON_MEMORY_ADMIN_TOKEN` | (unset)                                  | Bearer token forwarded to eidolon_memory if required |

## Tests

```bash
.venv/bin/pytest server/tests/ -v
```

Covers:

- Proxy: forwarding, query strings, header filtering, bearer injection,
  upstream errors → 502, SSE/JSON branching, services catalog, concurrent
  health probes.
- Deploy runner: whitelist enforcement, shell-injection rejection, restart
  fallback, native restart preference, timeout/SIGKILL path, lock
  serialisation, log whitelist enforcement, all HTTP routes.

## Roadmap (post-MVP, not implemented)

- Authentication (single admin account → JWT)
- Operation audit log (JSONL)
- Permission model for multi-user gateways
- Docker compose for one-shot deployment
