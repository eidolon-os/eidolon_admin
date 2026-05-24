#!/usr/bin/env bash
# Tiny wrapper used by supervisord program blocks that need a project's .env
# loaded before exec'ing the real binary.
#
# Usage:
#   with-env.sh <project_dir> <env_file_basename_or_path> -- <cmd...>
#
# Examples:
#   with-env.sh /path/to/eidolon_hub .env -- /path/to/.venv/bin/uvicorn hub.main:app
#   with-env.sh /path/to/eidolon_channel deploy/.livekit-channel.env -- python -m eidolon.livekit.agent.server
#
# - cd into <project_dir>
# - Parse <env_file> if present (KEY=VALUE only; safe against quoting like
#   space-bearing values that would break `source`)
# - exec the remaining argv
set -euo pipefail

if [[ $# -lt 3 ]]; then
  echo "usage: $0 <project_dir> <env_file> -- <cmd...>" >&2
  exit 2
fi

project_dir="$1"; shift
env_file="$1"; shift

if [[ "$1" != "--" ]]; then
  echo "expected '--' after env file path, got '$1'" >&2
  exit 2
fi
shift

cd "$project_dir"

if [[ -f "$env_file" ]]; then
  while IFS= read -r line || [[ -n "$line" ]]; do
    [[ -z "${line//[[:space:]]/}" ]] && continue
    [[ "${line#"${line%%[![:space:]]*}"}" == \#* ]] && continue
    [[ "$line" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]] || continue
    key="${BASH_REMATCH[1]}"
    val="${BASH_REMATCH[2]}"
    val="${val%"${val##*[![:space:]]}"}"
    if [[ "$val" =~ ^\"(.*)\"$ ]] || [[ "$val" =~ ^\'(.*)\'$ ]]; then
      val="${BASH_REMATCH[1]}"
    fi
    export "$key=$val"
  done < "$env_file"
fi

exec "$@"
