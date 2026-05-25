#!/usr/bin/env bash
# Block until a TCP port accepts connections, then exec the real command.
#
#   wait-tcp.sh --host 127.0.0.1 --port 7880 --timeout 120 -- <cmd...>
#
# Used by channel-worker so it does not spam connection errors while livekit-server
# is still starting after a supervisord restart.
set -euo pipefail

host="127.0.0.1"
port=""
timeout=120

while [[ $# -gt 0 ]]; do
  case "$1" in
    --host) host="$2"; shift 2 ;;
    --port) port="$2"; shift 2 ;;
    --timeout) timeout="$2"; shift 2 ;;
    --)
      shift
      break
      ;;
    *)
      echo "wait-tcp: unknown argument: $1" >&2
      exit 2
      ;;
  esac
done

if [[ -z "$port" ]]; then
  echo "wait-tcp: --port is required" >&2
  exit 2
fi
if [[ $# -lt 1 ]]; then
  echo "wait-tcp: expected '-- <cmd...>'" >&2
  exit 2
fi

deadline=$((SECONDS + timeout))
echo "wait-tcp: waiting for ${host}:${port} (timeout ${timeout}s)..." >&2
while (( SECONDS < deadline )); do
  if nc -z "$host" "$port" 2>/dev/null; then
    echo "wait-tcp: ${host}:${port} is up" >&2
    exec "$@"
  fi
  sleep 0.5
done

echo "wait-tcp: timed out waiting for ${host}:${port}" >&2
exit 1
