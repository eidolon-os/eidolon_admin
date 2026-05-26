#!/usr/bin/env bash
# Eidolon dev stack — supervisord edition.
#
# This wrapper does only what supervisord can't do for itself:
#   1. first-run bootstrap (venv + uv install, pnpm install, log dirs)
#   2. start the supervisord daemon (which then starts admin-api + every
#      enabled sub-project under deploy/supervisor/enabled/*.conf)
#   3. launch the vite dev server for the admin web (port 9001)
#   4. clean stop of everything in reverse order
#
# admin-api itself is now a supervised program (see
# deploy/supervisor/available/admin.conf), so it inherits auto-restart,
# unified logs, and remote-controlled restart from the Configs page.
#
# Per-program control (start/stop/restart memory-supervisor etc.) happens
# via the admin UI or `supervisorctl`. This script is just the top-level
# bootstrap + lifecycle.
#
# Usage:
#   ./deploy/dev/run_all.sh                # foreground admin-api + web only (no supervisord)
#   ./deploy/dev/run_all.sh start          # cold start: ports must be free, then supervisord + vite
#   ./deploy/dev/run_all.sh start --force-cleanup
#                                        # SIGTERM Eidolon-looking port holders, then cold start
#   ./deploy/dev/run_all.sh stop           # stop vite + supervisord (all supervised programs)
#   ./deploy/dev/run_all.sh restart        # stop then start (use when stack is already running)
#   ./deploy/dev/run_all.sh status         # show vite + supervisorctl status (no port check)
#   ./deploy/dev/run_all.sh foreground     # admin-api + web in foreground (no sub-projects)
#
#   ./deploy/dev/run_all.sh sv [...]       # passthrough to supervisorctl
#                                        # e.g. sv status, sv restart channel:channel-worker
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

# Monorepo root (eidolon_admin/..) — expands $EIDOLON_ROOT in config/services.yaml
export EIDOLON_ROOT="${EIDOLON_ROOT:-$(cd "${ROOT}/.." && pwd)}"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; CYAN='\033[0;36m'; NC='\033[0m'
info()  { echo -e "${GREEN}[INFO]${NC} $*"; }
warn()  { echo -e "${YELLOW}[WARN]${NC} $*"; }
error() { echo -e "${RED}[ERROR]${NC} $*" >&2; }
header(){ echo -e "${CYAN}==== $* ====${NC}"; }

# --- Paths ------------------------------------------------------------------
VAR_DIR="${ROOT}/var"
LOG_DIR="${HOME}/eidolon/logs"
RUN_DIR="${HOME}/eidolon/run"

# Log layout: ~/eidolon/logs/<project>/<file>.log
#   admin/      supervisord + gateway api (api) + vite (web)
#   nats/       server
#   livekit/    server
#   memory/     supervisor + discovery
#   hub/        api
#   agent/      main
#   channel/    worker
#   client-web/ dev
# supervisord refuses to spawn a program if its log dir doesn't exist; pre-
# create everything our own configs reference so the user never sees a phantom
# "no such file" on first start.
LOG_PROJECTS=(admin nats livekit memory hub agent channel)
for _p in "${LOG_PROJECTS[@]}"; do
  mkdir -p "${LOG_DIR}/${_p}"
done
mkdir -p "$VAR_DIR" "$RUN_DIR" "${LOG_DIR}/admin/childlogs"

# Vite dev server pid/log — admin-api's pid is owned by supervisord now.
WEB_PID_FILE="${RUN_DIR}/eidolon-admin-gateway-web.pid"
WEB_LOG_FILE="${LOG_DIR}/admin/gateway-web.log"

API_HOST="${EIDOLON_ADMIN_API_HOST:-127.0.0.1}"
API_PORT="${EIDOLON_ADMIN_API_PORT:-9000}"
WEB_PORT="${EIDOLON_ADMIN_WEB_PORT:-9001}"

load_ports_env() {
  ensure_api_deps
  # shellcheck disable=SC2046
  eval "$("${VENV}/bin/python" -m eidolon_admin_server.app.ports export)"
  API_HOST="${EIDOLON_ADMIN_API_HOST:-127.0.0.1}"
  API_PORT="${EIDOLON_ADMIN_API_PORT:-9000}"
  WEB_PORT="${EIDOLON_ADMIN_WEB_PORT:-9001}"
}

sync_ports_from_registry() {
  ensure_api_deps
  info "syncing config/ports.yaml into sub-project settings (if changed)"
  "${VENV}/bin/python" -m eidolon_admin_server.app.ports sync || true
}

VENV="${ROOT}/.venv"
WEB_DIR="${ROOT}/web"
VITE_BIN_REL="node_modules/.bin/vite"

SV_CONF="${ROOT}/deploy/dev/supervisord.conf"
SV_PID="${VAR_DIR}/supervisord.pid"
SV_SOCK="${VAR_DIR}/supervisor.sock"

# --- Deps -------------------------------------------------------------------

ensure_api_deps() {
  if [[ ! -x "${VENV}/bin/uvicorn" || ! -x "${VENV}/bin/supervisord" ]]; then
    info "first run — creating venv + installing eidolon-admin (incl. supervisord)"
    python3 -m venv "$VENV"
    "${VENV}/bin/pip" install -q --upgrade pip
    "${VENV}/bin/pip" install -q -e "${ROOT}[dev]"
  fi
}

ensure_web_deps() {
  if [[ ! -x "${WEB_DIR}/${VITE_BIN_REL}" ]]; then
    if command -v pnpm >/dev/null 2>&1; then
      info "first run — pnpm install"
      (cd "$WEB_DIR" && pnpm install)
    elif command -v npm >/dev/null 2>&1; then
      info "first run — npm install"
      (cd "$WEB_DIR" && npm install)
    else
      error "neither pnpm nor npm on PATH"
      exit 1
    fi
  fi
}

# --- process tree helpers --------------------------------------------------
#
# Why we need these: supervisord starts each child program with its own
# session (setsid), which means every child becomes a process-group leader
# independent of supervisord's group. So ``kill -<supervisord_pgid>`` only
# reaches supervisord itself — its children survive as orphans (PPID=1).
#
# The fix is to walk the PPID tree explicitly: enumerate descendants via
# pgrep -P, then signal each. This is the only way to guarantee that when
# we SIGKILL supervisord (because graceful shutdown timed out), we don't
# leave subprocesses hanging on to ports.

# Print all descendants of $1 (recursive) as ``pid|comm`` lines, where
# ``comm`` is the command name at snapshot time. Empty if no children.
#
# We snapshot the command name now so ``kill_tree`` can re-check it
# before signaling: a PID we collected at t=0 may have died and been
# reused by the kernel for a wholly unrelated process by t=signal.
# Without this guard we'd SIGKILL random innocents whenever shutdown
# stretched long enough for PID reuse (more common on busy macOS).
collect_descendants() {
  local parent=$1
  local children child comm
  children=$(pgrep -P "$parent" 2>/dev/null || true)
  for child in $children; do
    comm=$(ps -o comm= -p "$child" 2>/dev/null | tr -d '[:space:]')
    [[ -z "$comm" ]] && continue  # PID died between pgrep and ps; skip
    echo "${child}|${comm}"
    collect_descendants "$child"
  done
}

# Signal the entire tree rooted at $1 with signal $2 (e.g. "-TERM" or "-KILL").
#
# Order: parent first (stops supervisord's autorestart from racing us by
# respawning a child mid-shutdown), then descendants. Each kill is
# best-effort — already-dead PIDs return non-zero, which is fine.
#
# Each descendant is verified against its snapshotted comm: if the PID
# has been reused (different comm) we skip it. This is best-effort
# protection — comm match doesn't prove identity (two processes with
# the same name still alias) but it eliminates the common case where
# the PID has been recycled into something unrelated (a shell, sshd).
kill_tree() {
  local root=$1 signal=$2 entry pid snap_comm cur_comm
  kill "$signal" "$root" 2>/dev/null || true
  while IFS= read -r entry; do
    [[ -z "$entry" ]] && continue
    pid="${entry%%|*}"
    snap_comm="${entry#*|}"
    cur_comm=$(ps -o comm= -p "$pid" 2>/dev/null | tr -d '[:space:]')
    if [[ -z "$cur_comm" ]]; then
      continue  # already gone, fine
    fi
    if [[ "$cur_comm" != "$snap_comm" ]]; then
      warn "kill_tree: skip PID $pid — comm changed ($snap_comm → $cur_comm), likely PID reuse"
      continue
    fi
    kill "$signal" "$pid" 2>/dev/null || true
  done < <(collect_descendants "$root")
}

# --- vite dev server --------------------------------------------------------

web_alive() { [[ -f "$WEB_PID_FILE" ]] && kill -0 "$(cat "$WEB_PID_FILE")" 2>/dev/null; }

do_web_start() {
  ensure_web_deps
  if web_alive; then
    info "web already running (PID $(cat "$WEB_PID_FILE"))"
    return 0
  fi
  info "starting web (log $WEB_LOG_FILE)"
  (
    cd "$WEB_DIR"
    nohup "./${VITE_BIN_REL}" --port "$WEB_PORT" --strictPort >>"$WEB_LOG_FILE" 2>&1 &
    echo $! >"$WEB_PID_FILE"
  )
  sleep 1
  if ! web_alive; then
    error "web died immediately; tail $WEB_LOG_FILE :"
    tail -30 "$WEB_LOG_FILE" >&2 || true
    rm -f "$WEB_PID_FILE"
    return 1
  fi
  info "web PID $(cat "$WEB_PID_FILE")  /  http://127.0.0.1:${WEB_PORT}/"
}

do_web_stop() {
  if ! web_alive; then
    info "web not running"
    rm -f "$WEB_PID_FILE" 2>/dev/null || true
    return 0
  fi
  local pid; pid="$(cat "$WEB_PID_FILE")"
  # Vite spawns esbuild + occasional node worker children for HMR. Use
  # kill_tree so they all go down together — otherwise we leave esbuild
  # daemons holding scratch ports / file watches as orphans.
  info "SIGTERM web $pid (+ descendants)"
  kill_tree "$pid" "-TERM"
  for _ in $(seq 1 10); do web_alive || break; sleep 0.2; done
  if web_alive; then
    warn "vite tree still alive after 2s; SIGKILL whole tree"
    kill_tree "$pid" "-KILL"
  fi
  rm -f "$WEB_PID_FILE"
  info "web stopped"
}

do_web_status() {
  header "admin web (vite)"
  if web_alive; then
    info "running PID $(cat "$WEB_PID_FILE")"
    echo "  URL: http://127.0.0.1:${WEB_PORT}/"
    echo "  Log: $WEB_LOG_FILE"
  else
    info "not running"
    rm -f "$WEB_PID_FILE" 2>/dev/null || true
  fi
}

# --- supervisord ------------------------------------------------------------

# True only when var/supervisord.pid points at a *live supervisord process*.
#
# The PID-existence check alone isn't enough: PIDs get recycled by the
# kernel. If our last supervisord crashed without removing its pid file,
# and the kernel later reassigned that PID to (say) the shell ``sleep``
# in run_all.sh, ``kill -0`` succeeds and we'd incorrectly skip startup.
# Verifying the process is really a supervisord invocation closes that
# hole.
#
# We grep ``ps -o args=`` rather than ``-o comm=`` because on macOS
# ``comm`` returns the executable basename (``python3``) and supervisord
# is launched as a script. The full args contain
# ``.../bin/supervisord -c ...`` which we can match unambiguously.
sv_alive() {
  [[ -f "$SV_PID" ]] || return 1
  local pid; pid=$(cat "$SV_PID" 2>/dev/null)
  [[ -n "$pid" ]] || return 1
  kill -0 "$pid" 2>/dev/null || return 1
  ps -o args= -p "$pid" 2>/dev/null | grep -q "bin/supervisord"
}

sv_ctl_ready() {
  [[ -S "$SV_SOCK" ]] && "${VENV}/bin/supervisorctl" -c "$SV_CONF" version >/dev/null 2>&1
}

# Reload deploy/supervisor/enabled/*.conf into a running supervisord.
# Stop channel before LiveKit during stack shutdown so the worker does not log
# ConnectionRefused while livekit-server is tearing down (supervisorctl
# shutdown stops programs in parallel by default).
do_sv_stop_channel_first() {
  if ! sv_ctl_ready; then
    return 0
  fi
  local raw state
  raw="$("${VENV}/bin/supervisorctl" -c "$SV_CONF" status channel:channel-worker 2>/dev/null || true)"
  state="$(echo "$raw" | awk '{print $2}')"
  if [[ -z "$state" || "$state" == "STOPPED" ]]; then
    return 0
  fi
  info "stopping channel-worker before stack shutdown (clean LiveKit disconnect)"
  "${VENV}/bin/supervisorctl" -c "$SV_CONF" stop channel:channel-worker 2>/dev/null \
    || warn "supervisorctl stop channel:channel-worker failed (continuing)"
  local i
  for i in $(seq 1 80); do
    raw="$("${VENV}/bin/supervisorctl" -c "$SV_CONF" status channel:channel-worker 2>/dev/null || true)"
    state="$(echo "$raw" | awk '{print $2}')"
    if [[ -z "$state" || "$state" == "STOPPED" ]]; then
      return 0
    fi
    sleep 0.5
  done
  warn "channel-worker still ${state:-running} after 40s; proceeding with shutdown"
}

do_sv_reread_update() {
  ensure_api_deps
  if ! sv_alive; then
    return 0
  fi
  local i
  for i in $(seq 1 40); do
    sv_ctl_ready && break
    sleep 0.25
  done
  if ! sv_ctl_ready; then
    warn "supervisorctl not ready on $SV_SOCK — skip reread/update"
    return 1
  fi
  info "supervisord reread + update (reload enabled/*.conf)"
  "${VENV}/bin/supervisorctl" -c "$SV_CONF" reread \
    || warn "supervisorctl reread failed"
  "${VENV}/bin/supervisorctl" -c "$SV_CONF" update \
    || warn "supervisorctl update failed"
}

do_sv_start() {
  ensure_api_deps
  if sv_alive; then
    info "supervisord already running (PID $(cat "$SV_PID"), socket $SV_SOCK)"
    info "  config reload only — use '$0 status' to inspect; '$0 restart' for full stop+start"
    do_sv_reread_update
    return 0
  fi
  # If a stale socket lingers from a crashed daemon, supervisord will refuse
  # to start.
  if [[ -S "$SV_SOCK" && ! -f "$SV_PID" ]]; then
    info "cleaning stale socket $SV_SOCK"
    rm -f "$SV_SOCK"
  fi
  info "starting supervisord (conf $SV_CONF)"
  "${VENV}/bin/supervisord" -c "$SV_CONF"
  # supervisord daemonises immediately and writes the pid file.
  sleep 0.5
  if ! sv_alive; then
    error "supervisord did not start; tail of supervisord.log:"
    tail -30 "$LOG_DIR/admin/supervisord.log" >&2 || true
    return 1
  fi
  info "supervisord PID $(cat "$SV_PID"), socket $SV_SOCK"
  info "  (admin-api auto-starts under supervisord)"
  do_sv_reread_update
}

do_sv_stop() {
  if ! sv_alive; then
    info "supervisord not running"
    rm -f "$SV_PID" 2>/dev/null || true
    return 0
  fi
  local sv_pid; sv_pid=$(cat "$SV_PID")

  # Step 1: stop channel while LiveKit is still up (avoids reconnect errors in logs).
  do_sv_stop_channel_first

  # Step 2: ask supervisord to gracefully shut down the rest of the program tree.
  #
  # It will send SIGTERM to each program and wait up to that program's
  # ``stopwaitsecs`` before SIGKILL-ing. Remaining programs shut down in parallel;
  # wall time is bounded by the max ``stopwaitsecs`` plus supervisord overhead.
  info "supervisord shutdown (stops admin-api and all enabled supervised programs)"
  "${VENV}/bin/supervisorctl" -c "$SV_CONF" shutdown 2>/dev/null \
    || warn "supervisorctl shutdown rpc failed (continuing with patient wait)"

  # Step 3: patient wait. 60s = max(stopwaitsecs)=40 + 20s buffer for
  # supervisord's own teardown. Previously this was 20s, which routinely
  # truncated channel-worker's graceful exit and forced step 3.
  local wait_seconds=60
  local checks=$((wait_seconds * 2))
  for _ in $(seq 1 $checks); do sv_alive || break; sleep 0.5; done

  if ! sv_alive; then
    rm -f "$SV_PID" 2>/dev/null || true
    return 0
  fi

  # Step 4: escalation. Graceful shutdown didn't complete in time. This
  # is where the old code SIGKILL'd just supervisord — and every child
  # immediately became a PPID=1 orphan because supervisord uses setsid
  # to isolate each child into its own session, so signaling supervisord
  # alone doesn't reach its descendants.
  #
  # ``kill_tree`` walks the PPID hierarchy explicitly and signals every
  # descendant individually, eliminating the orphan window. We do this
  # in two stages: TERM first (some children may still react), then KILL
  # if anyone survives.
  warn "supervisord didn't exit within ${wait_seconds}s — escalating to TERM-tree"
  kill_tree "$sv_pid" "-TERM"
  for _ in $(seq 1 30); do sv_alive || break; sleep 0.5; done

  if sv_alive; then
    warn "still alive after TERM-tree (15s); SIGKILL whole tree"
    kill_tree "$sv_pid" "-KILL"
    sleep 1
  fi

  rm -f "$SV_PID" 2>/dev/null || true
}

do_sv_status() {
  header "supervisord"
  if sv_alive; then
    info "running PID $(cat "$SV_PID"), socket $SV_SOCK"
    echo
    "${VENV}/bin/supervisorctl" -c "$SV_CONF" status || true
  else
    info "not running"
  fi
}

do_sv_passthrough() {
  ensure_api_deps
  exec "${VENV}/bin/supervisorctl" -c "$SV_CONF" "$@"
}

# --- combined ---------------------------------------------------------------

# Pre-flight: refuse to start if any declared port is held by a process
# not under our control. Catches the "previous run's orphan" case before
# it cascades into supervisord's "Exited too quickly" failures.
#
# Pass --force-cleanup to auto-SIGTERM any orphans found (the audit CLI
# handles SIGTERM → re-scan → escalate to SIGKILL → re-scan). If even
# SIGKILL doesn't free the port, we still refuse to start because
# something is deeply wrong and silent failure would be worse.
do_preflight() {
  ensure_api_deps
  local cli="${VENV}/bin/python -m eidolon_admin_server.app.system_health.cli check"
  if [[ "${PREFLIGHT_CLEANUP:-0}" == "1" ]]; then
    cli="$cli --cleanup"
    info "pre-flight: will SIGTERM Eidolon-looking listeners on declared ports, then continue"
  else
    info "pre-flight: checking declared ports are free (required for cold start)"
  fi
  if ! $cli; then
    exit 1
  fi
}

do_start() {
  load_ports_env
  sync_ports_from_registry
  header "pre-flight port audit"
  do_preflight
  echo
  header "supervisord (incl. admin-api)"
  do_sv_start
  echo
  header "admin web"
  do_web_start
}

do_stop() {
  header "admin web"
  do_web_stop
  echo
  header "supervisord"
  do_sv_stop
}

do_restart() {
  do_stop
  sleep 1
  do_start
}

do_status() {
  do_web_status
  echo
  do_sv_status
}

do_foreground() {
  load_ports_env
  sync_ports_from_registry
  ensure_web_deps
  cleanup() {
    [[ -n "${API_PID:-}" ]] && kill "$API_PID" 2>/dev/null || true
    [[ -n "${WEB_PID:-}" ]] && kill "$WEB_PID" 2>/dev/null || true
  }
  trap cleanup EXIT INT TERM

  "${VENV}/bin/uvicorn" eidolon_admin_server.app.main:app \
    --host "$API_HOST" --port "$API_PORT" &
  API_PID=$!
  (cd "$WEB_DIR" && "./${VITE_BIN_REL}" --port "$WEB_PORT" --strictPort) &
  WEB_PID=$!

  echo
  info "eidolon-admin (foreground) — supervisord NOT touched (NATS / sub-projects not started)"
  echo "  API: http://${API_HOST}:${API_PORT}/docs"
  echo "  Web: http://127.0.0.1:${WEB_PORT}/"
  echo "  Use '$0 start' for the full stack (NATS, memory, hub, agent, channel, … + vite)."
  echo "  Foreground mode still serves the UI; /api/devices needs NATS from 'start'."
  echo "  Ctrl+C to stop."
  echo
  wait "$API_PID" "$WEB_PID" || true
}

# --- dispatch ---------------------------------------------------------------

# ``--force-cleanup`` is a flag both ``start`` and ``restart`` accept;
# parse and remove from $@ before the case match.
for arg in "$@"; do
  if [[ "$arg" == "--force-cleanup" ]]; then
    export PREFLIGHT_CLEANUP=1
  fi
done
set -- "${@/--force-cleanup/}"

case "${1:-}" in
  start)      do_start ;;
  stop)       do_stop ;;
  restart)    do_restart ;;
  status)     do_status ;;
  foreground) do_foreground ;;
  "")         do_foreground ;;

  # Targeted admin-web control (api now goes through supervisorctl).
  start-web|web-start)   do_web_start ;;
  stop-web|web-stop)     do_web_stop ;;
  restart-web|web-restart) do_web_stop; sleep 1; do_web_start ;;
  status-web|web-status) do_web_status ;;

  # Compat: old "start-admin" used to mean "start api+web". api is supervised
  # now, so map these to the web-only flow + a friendly hint.
  start-admin)
    warn "admin-api is now supervised; '$0 sv start admin:admin-api' restarts the api."
    do_web_start
    ;;
  stop-admin)
    warn "admin-api is now supervised; '$0 sv stop admin:admin-api' stops the api."
    do_web_stop
    ;;
  restart-admin)
    warn "admin-api is now supervised; restarting admin web (vite) only."
    warn "  to restart the api: '$0 sv restart admin:admin-api'"
    do_web_stop; sleep 1; do_web_start
    ;;
  status-admin)
    do_web_status
    echo
    "${VENV}/bin/supervisorctl" -c "$SV_CONF" status admin:admin-api 2>/dev/null || warn "supervisord not running"
    ;;

  start-sv|sv-start)   do_sv_start ;;
  stop-sv|sv-stop)     do_sv_stop ;;
  status-sv|sv-status) do_sv_status ;;
  sv)
    shift
    do_sv_passthrough "$@"
    ;;

  -h|--help|help)
    sed -n '2,30p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    error "unknown command: ${1:-}"
    error ""
    error "Lifecycle (full stack via supervisord):"
    error "  $0 start [--force-cleanup]   cold start — ports must be free first"
    error "  $0 stop                    stop vite + supervisord"
    error "  $0 restart [--force-cleanup] stop then start (use if stack already running)"
    error "  $0 status                    show vite + supervisorctl (no port check)"
    error "  $0 foreground                admin-api + vite only (no NATS / sub-projects)"
    error ""
    error "Partial / passthrough:"
    error "  $0 {start,stop,restart,status}-web"
    error "  $0 {start,stop,status}-sv"
    error "  $0 sv <args>                 supervisorctl passthrough"
    exit 1
    ;;
esac
