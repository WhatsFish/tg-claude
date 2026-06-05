#!/usr/bin/env bash
# Liveness heartbeat for the /status dashboard. Touches the heartbeat file only
# while the OpenClaw gateway (tg-claude.service) is actually active, so a dead
# bot stops refreshing it and the status page flips to warn/fail.
# Host dir ~/.local/share/cron-heartbeats is mounted read-only into the status
# container as /data/heartbeats; the job id in status/cron.ts must be "tg-claude".
set -euo pipefail

# cron runs with a bare environment, so `systemctl --user` can't find the user
# bus ("Failed to connect to bus"). Point it at the live user session bus.
export XDG_RUNTIME_DIR="${XDG_RUNTIME_DIR:-/run/user/$(id -u)}"
export DBUS_SESSION_BUS_ADDRESS="${DBUS_SESSION_BUS_ADDRESS:-unix:path=${XDG_RUNTIME_DIR}/bus}"

HEARTBEAT_DIR="${HEARTBEAT_DIR:-$HOME/.local/share/cron-heartbeats}"
JOB="tg-claude"

if systemctl --user is-active --quiet tg-claude; then
  mkdir -p "$HEARTBEAT_DIR"
  touch "$HEARTBEAT_DIR/$JOB"
fi
