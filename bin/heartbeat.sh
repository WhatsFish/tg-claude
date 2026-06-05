#!/usr/bin/env bash
# Liveness heartbeat for the /status dashboard. Touches the heartbeat file only
# while the OpenClaw gateway (tg-claude.service) is actually active, so a dead
# bot stops refreshing it and the status page flips to warn/fail.
# Host dir ~/.local/share/cron-heartbeats is mounted read-only into the status
# container as /data/heartbeats; the job id in status/cron.ts must be "tg-claude".
set -euo pipefail

HEARTBEAT_DIR="${HEARTBEAT_DIR:-$HOME/.local/share/cron-heartbeats}"
JOB="tg-claude"

if systemctl --user is-active --quiet tg-claude; then
  mkdir -p "$HEARTBEAT_DIR"
  touch "$HEARTBEAT_DIR/$JOB"
fi
