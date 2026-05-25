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
#   ./deploy/dev/run_all.sh                # foreground admin-api + web (Ctrl+C)
#   ./deploy/dev/run_all.sh start          # supervisord (incl. admin-api) + vite
#   ./deploy/dev/run_all.sh stop           # reverse
#   ./deploy/dev/run_all.sh restart
#   ./deploy/dev/run_all.sh status         # admin web + supervisorctl status
#   ./deploy/dev/run_all.sh foreground     # admin-api + web in foreground
#
#   ./deploy/dev/run_all.sh sv [...]       # passthrough to supervisorctl
#                                          # e.g. `sv status`, `sv tail -f memory:memory-supervisor`
#
set -euo pipefail
ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"

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

API_HOST="${EIDOLON_ADMIN_HOST:-127.0.0.1}"
API_PORT="${EIDOLON_ADMIN_PORT:-9000}"
WEB_PORT="${EIDOLON_ADMIN_WEB_PORT:-9001}"

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

# Print all descendants of $1 (recursive). Empty if no children.
collect_descendants() {
  local parent=$1
  local children child
  children=$(pgrep -P "$parent" 2>/dev/null || true)
  for child in $children; do
    echo "$child"
    collect_descendants "$child"
  done
}

# Signal the entire tree rooted at $1 with signal $2 (e.g. "-TERM" or "-KILL").
#
# Order: parent first (stops supervisord's autorestart from racing us by
# respawning a child mid-shutdown), then descendants. Each kill is
# best-effort — already-dead PIDs return non-zero, which is fine.
kill_tree() {
  local root=$1 signal=$2 pid
  kill "$signal" "$root" 2>/dev/null || true
  for pid in $(collect_descendants "$root"); do
    kill "$signal" "$pid" 2>/dev/null || true
  done
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

sv_alive() { [[ -f "$SV_PID" ]] && kill -0 "$(cat "$SV_PID")" 2>/dev/null; }

do_sv_start() {
  ensure_api_deps
  if sv_alive; then
    info "supervisord already running (PID $(cat "$SV_PID"), socket $SV_SOCK)"
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
}

do_sv_stop() {
  if ! sv_alive; then
    info "supervisord not running"
    rm -f "$SV_PID" 2>/dev/null || true
    return 0
  fi
  local sv_pid; sv_pid=$(cat "$SV_PID")

  # Step 1: ask supervisord to gracefully shut down its program tree.
  #
  # It will send SIGTERM to each program and wait up to that program's
  # ``stopwaitsecs`` (max in our setup is 40s for channel-worker) before
  # SIGKILL-ing. Programs are shut down in parallel, so total wall time
  # is bounded by the max ``stopwaitsecs`` plus supervisord's own
  # bookkeeping overhead.
  info "supervisord shutdown (will stop admin-api + all enabled programs)"
  "${VENV}/bin/supervisorctl" -c "$SV_CONF" shutdown 2>/dev/null \
    || warn "supervisorctl shutdown rpc failed (continuing with patient wait)"

  # Step 2: patient wait. 60s = max(stopwaitsecs)=40 + 20s buffer for
  # supervisord's own teardown. Previously this was 20s, which routinely
  # truncated channel-worker's graceful exit and forced step 3.
  local wait_seconds=60
  local checks=$((wait_seconds * 2))
  for _ in $(seq 1 $checks); do sv_alive || break; sleep 0.5; done

  if ! sv_alive; then
    rm -f "$SV_PID" 2>/dev/null || true
    return 0
  fi

  # Step 3: escalation. Graceful shutdown didn't complete in time. This
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

do_start() {
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
  ensure_api_deps
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
  info "eidolon-admin (foreground) — supervisord NOT touched"
  echo "  API: http://${API_HOST}:${API_PORT}/docs"
  echo "  Web: http://127.0.0.1:${WEB_PORT}/"
  echo "  Use '$0 start' for the full stack (supervisord + admin-api + sub-projects + vite)."
  echo "  Ctrl+C to stop."
  echo
  wait "$API_PID" "$WEB_PID" || true
}

# --- dispatch ---------------------------------------------------------------

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
    error "usage: $0 [start|stop|restart|status|foreground]"
    error "       $0 [start-web|stop-web|restart-web|status-web]"
    error "       $0 [start-sv|stop-sv|status-sv]"
    error "       $0 sv <supervisorctl-args>     # passthrough"
    error "       (admin-api is supervised — use 'sv restart admin:admin-api')"
    exit 1
    ;;
esac
