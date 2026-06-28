#!/usr/bin/env bash
# Render a runtime LiveKit config with the current LAN IP before exec'ing
# livekit-server. Keep deploy/livekit/livekit.yaml portable: rtc.node_ip is a
# machine/network fact, not source configuration.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
TEMPLATE="${EIDOLON_LIVEKIT_TEMPLATE_CONFIG:-${ROOT}/deploy/livekit/livekit.yaml}"
GENERATED="${EIDOLON_LIVEKIT_GENERATED_CONFIG:-${ROOT}/var/livekit/livekit.generated.yaml}"
LIVEKIT_BIN="${EIDOLON_LIVEKIT_BIN:-/opt/homebrew/bin/livekit-server}"

detect_lan_ip() {
  if [[ -n "${EIDOLON_LIVEKIT_NODE_IP:-}" ]]; then
    printf '%s\n' "${EIDOLON_LIVEKIT_NODE_IP}"
    return 0
  fi

  python3 - <<'PY'
import socket

for target in ("8.8.8.8", "1.1.1.1"):
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect((target, 80))
        ip = sock.getsockname()[0]
        if ip and not ip.startswith("127."):
            print(ip)
            raise SystemExit(0)
    except OSError:
        pass
    finally:
        sock.close()
raise SystemExit(1)
PY
}

NODE_IP="$(detect_lan_ip)"
if [[ -z "$NODE_IP" ]]; then
  echo "livekit-dev: failed to detect LAN IP; set EIDOLON_LIVEKIT_NODE_IP" >&2
  exit 1
fi

mkdir -p "$(dirname "$GENERATED")"
python3 - "$TEMPLATE" "$GENERATED" "$NODE_IP" <<'PY'
from pathlib import Path
import sys

template = Path(sys.argv[1])
generated = Path(sys.argv[2])
node_ip = sys.argv[3]

lines = template.read_text(encoding="utf-8").splitlines(keepends=True)
out: list[str] = []
inserted = False

for line in lines:
    stripped = line.strip()
    if stripped.startswith("node_ip:"):
        continue
    out.append(line)
    if stripped == "rtc:":
        out.append(f"  node_ip: {node_ip}\n")
        inserted = True

if not inserted:
    out.extend(["\n", "rtc:\n", f"  node_ip: {node_ip}\n"])

generated.write_text("".join(out), encoding="utf-8")
PY

echo "livekit-dev: generated ${GENERATED} with rtc.node_ip=${NODE_IP}" >&2

if [[ "${EIDOLON_LIVEKIT_GENERATE_ONLY:-}" == "1" ]]; then
  printf '%s\n' "$GENERATED"
  exit 0
fi

exec "$LIVEKIT_BIN" --config "$GENERATED" "$@"
