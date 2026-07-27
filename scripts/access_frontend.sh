#!/usr/bin/env bash

set -euo pipefail

readonly vps_host="${TRADEMIND_VPS_HOST:-root@169.58.27.182}"
readonly ssh_key="${TRADEMIND_SSH_KEY:-${HOME}/.ssh/trademind-contabo}"
readonly local_port="${TRADEMIND_LOCAL_PORT:-3000}"
readonly frontend_url="http://127.0.0.1:${local_port}"

if [[ ! -f "${ssh_key}" ]]; then
    echo "SSH key not found: ${ssh_key}" >&2
    exit 1
fi

if ! [[ "${local_port}" =~ ^[0-9]+$ ]] || ((local_port < 1 || local_port > 65535)); then
    echo "TRADEMIND_LOCAL_PORT must be a valid TCP port." >&2
    exit 1
fi

open_browser() {
    sleep 1
    if command -v open >/dev/null 2>&1; then
        open "${frontend_url}"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "${frontend_url}"
    else
        echo "Open ${frontend_url} in your browser."
    fi
}

echo "Opening TradeMind at ${frontend_url}"
echo "Keep this terminal open. Press Ctrl+C to disconnect."

open_browser &

exec ssh \
    -N \
    -i "${ssh_key}" \
    -L "${local_port}:127.0.0.1:3000" \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=60 \
    -o ServerAliveCountMax=3 \
    "${vps_host}"
