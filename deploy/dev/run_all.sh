#!/usr/bin/env bash
# Eidolon dev stack — supervisord edition.
#
# This wrapper owns three things:
#   1. eidolon-admin gateway api  (uvicorn :9000)
#   2. eidolon-admin web (vite :9001)
#   3. supervisord daemon, which itself launches every enabled sub-project
#      defined under deploy/supervisor/enabled/*.conf.
#
# Per-program control (start/stop/restart memory-supervisor etc.) happens via
# the admin UI or `supervisorctl`. This script is just the top-level lifecycle.
#
# Usage:
#   ./deploy/dev/run_all.sh                # foreground admin only
#   ./deploy/dev/run_all.sh start          # supervisord + admin
#   ./deploy/dev/run_all.sh stop           # reverse
#   ./deploy/dev/run_all.sh restart
#   ./deploy/dev/run_all.sh status         # admin + supervisorctl status
#   ./deploy/dev/run_all.sh foreground     # admin in foreground (Ctrl+C)
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
#   admin/      supervisord + gateway api/web
#   nats/       server
#   livekit/    server
#   memory/     supervisor + discovery
#   hub/        api
#   agent/      main
#   channel/    worker
#   client-web/ dev
# supervisord requires log directories to exist before spawning; pre-create
# everything our own configs reference so the user never sees a phantom
# "no such file" on first start.
LOG_PROJECTS=(admin nats livekit memory hub agent channel)
for _p in "${LOG_PROJECTS[@]}"; do
  mkdir -p "${LOG_DIR}/${_p}"
done
mkdir -p "$VAR_DIR" "$RUN_DIR" "${LOG_DIR}/admin/childlogs"

# admin gateway pid/log (unique to avoid colliding with sub-projects' own files)
API_PID_FILE="${RUN_DIR}/eidolon-admin-gateway-api.pid"
WEB_PID_FILE="${RUN_DIR}/eidolon-admin-gateway-web.pid"
API_LOG_FILE="${LOG_DIR}/admin/gateway-api.log"
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

# --- admin gateway ----------------------------------------------------------

api_alive() { [[ -f "$API_PID_FILE" ]] && kill -0 "$(cat "$API_PID_FILE")" 2>/dev/null; }
web_alive() { [[ -f "$WEB_PID_FILE" ]] && kill -0 "$(cat "$WEB_PID_FILE")" 2>/dev/null; }

do_api_start() {
  ensure_api_deps
  if api_alive; then
    info "api already running (PID $(cat "$API_PID_FILE"))"
    return 0
  fi
  info "starting api (log $API_LOG_FILE)"
  nohup "${VENV}/bin/uvicorn" eidolon_admin_server.app.main:app \
    --host "$API_HOST" --port "$API_PORT" \
    >>"$API_LOG_FILE" 2>&1 &
  echo $! >"$API_PID_FILE"
  sleep 0.7
  if ! api_alive; then
    error "api died immediately; tail $API_LOG_FILE :"
    tail -30 "$API_LOG_FILE" >&2 || true
    rm -f "$API_PID_FILE"
    return 1
  fi
  info "api PID $(cat "$API_PID_FILE")  /  http://${API_HOST}:${API_PORT}/docs"
}

do_api_stop() {
  if ! api_alive; then
    info "api not running"
    rm -f "$API_PID_FILE" 2>/dev/null || true
    return 0
  fi
  local pid; pid="$(cat "$API_PID_FILE")"
  info "SIGTERM api $pid"
  kill -TERM "$pid"
  for _ in $(seq 1 30); do api_alive || break; sleep 0.5; done
  if api_alive; then warn "SIGKILL api"; kill -KILL "$pid" || true; fi
  rm -f "$API_PID_FILE"
  info "api stopped"
}

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
  info "SIGTERM web $pid"
  kill -TERM "$pid"
  sleep 0.5
  if web_alive; then kill -KILL "$pid" 2>/dev/null || true; fi
  rm -f "$WEB_PID_FILE"
  info "web stopped"
}

do_admin_start()   { do_api_start; do_web_start; }
do_admin_stop()    { do_web_stop;  do_api_stop;  }

do_admin_status() {
  header "admin :: api"
  if api_alive; then
    info "running PID $(cat "$API_PID_FILE")"
    echo "  URL: http://${API_HOST}:${API_PORT}/docs"
    echo "  Log: $API_LOG_FILE"
  else
    info "not running"
    rm -f "$API_PID_FILE" 2>/dev/null || true
  fi
  echo
  header "admin :: web"
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
    error "supervisord did not start; tail of $VAR_DIR/supervisord.log:"
    tail -30 "$VAR_DIR/supervisord.log" >&2 || true
    return 1
  fi
  info "supervisord PID $(cat "$SV_PID"), socket $SV_SOCK"
}

do_sv_stop() {
  if ! sv_alive; then
    info "supervisord not running"
    rm -f "$SV_PID" 2>/dev/null || true
    return 0
  fi
  info "supervisord shutdown (will stop all enabled programs)"
  "${VENV}/bin/supervisorctl" -c "$SV_CONF" shutdown || warn "shutdown rpc failed"
  for _ in $(seq 1 40); do sv_alive || break; sleep 0.5; done
  if sv_alive; then warn "SIGKILL supervisord"; kill -KILL "$(cat "$SV_PID")" || true; fi
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
  header "supervisord"
  do_sv_start
  echo
  header "admin"
  do_admin_start
}

do_stop() {
  header "admin"
  do_admin_stop
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
  do_admin_status
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
  echo "  Use '$0 start' for the full stack (supervisord + admin)."
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

  start-admin)   do_admin_start ;;
  stop-admin)    do_admin_stop ;;
  restart-admin) do_admin_stop; sleep 1; do_admin_start ;;
  status-admin)  do_admin_status ;;

  start-sv|sv-start)   do_sv_start ;;
  stop-sv|sv-stop)     do_sv_stop ;;
  status-sv|sv-status) do_sv_status ;;
  sv)
    shift
    do_sv_passthrough "$@"
    ;;

  -h|--help|help)
    sed -n '2,24p' "$0" | sed 's/^# \{0,1\}//'
    ;;
  *)
    error "usage: $0 [start|stop|restart|status|foreground]"
    error "       $0 [start-admin|stop-admin|restart-admin|status-admin]"
    error "       $0 [start-sv|stop-sv|status-sv]"
    error "       $0 sv <supervisorctl-args>     # passthrough"
    exit 1
    ;;
esac
