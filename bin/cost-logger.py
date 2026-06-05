#!/usr/bin/env python3
"""Read OpenClaw session transcripts and log per-message token usage into the
shared cost_tracker DB as cost_event rows. Best-effort, idempotent, stdlib-only.

OpenClaw drives the local `claude` CLI (subscription OAuth), so the transcript's
`message.usage.cost.total` is typically 0 (no marginal API charge) — the
meaningful signal is token volume, which we record in input_tokens/output_tokens
and stash the raw usage in metadata. Run from cron every few minutes.

Idempotency: each assistant message has a stable transcript `id`; we keep the
set of already-logged ids in a state file and only INSERT new ones.
"""
import json
import os
import subprocess
import sys
from glob import glob

SESSIONS_GLOB = os.path.expanduser("~/.openclaw/agents/*/sessions/*.jsonl")
STATE_FILE = os.path.expanduser("~/.local/state/tg-claude/cost-logged.json")
COST_ENV = os.path.expanduser("~/.config/cost-tracker.env")
SERVICE = "tg-claude"


def load_env(path):
    env = {}
    try:
        with open(path) as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip().strip('"').strip("'")
    except FileNotFoundError:
        return None
    return env


def load_state():
    try:
        with open(STATE_FILE) as fh:
            return set(json.load(fh).get("logged_ids", []))
    except (FileNotFoundError, ValueError):
        return set()


def save_state(ids):
    os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
    # Cap the state so it can't grow unbounded; keep the most recent 50k ids.
    trimmed = list(ids)[-50000:]
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w") as fh:
        json.dump({"logged_ids": trimmed}, fh)
    os.replace(tmp, STATE_FILE)


def sql_lit(s):
    return "'" + str(s).replace("'", "''") + "'"


def collect_new_rows(logged):
    rows = []
    seen_now = set()
    for path in glob(SESSIONS_GLOB):
        session_id = os.path.splitext(os.path.basename(path))[0]
        try:
            fh = open(path)
        except OSError:
            continue
        with fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    o = json.loads(line)
                except ValueError:
                    continue
                mid = o.get("id")
                msg = o.get("message")
                if not mid or not isinstance(msg, dict):
                    continue
                usage = msg.get("usage")
                if not isinstance(usage, dict) or msg.get("role") != "assistant":
                    continue
                if mid in logged or mid in seen_now:
                    continue
                seen_now.add(mid)
                cost = usage.get("cost", {}) if isinstance(usage.get("cost"), dict) else {}
                meta = {
                    "message_id": mid,
                    "session_id": session_id,
                    "ts": o.get("timestamp"),
                    "cacheRead": usage.get("cacheRead"),
                    "cacheWrite": usage.get("cacheWrite"),
                    "totalTokens": usage.get("totalTokens"),
                    "source": "openclaw-transcript",
                }
                rows.append({
                    "model": msg.get("model"),
                    "input_tokens": usage.get("input"),
                    "output_tokens": usage.get("output"),
                    "cost_usd": cost.get("total"),
                    "metadata": json.dumps(meta, separators=(",", ":")),
                })
    return rows, seen_now


def to_values_tuple(r):
    def num(x):
        return "NULL" if x is None else str(x)
    model = "NULL" if r["model"] is None else sql_lit(r["model"])
    return (
        f"({sql_lit(SERVICE)}, 'anthropic', {model}, "
        f"{num(r['input_tokens'])}, {num(r['output_tokens'])}, "
        f"{num(r['cost_usd'])}, NULL, {sql_lit(r['metadata'])}::jsonb)"
    )


def main():
    env = load_env(COST_ENV)
    if not env:
        return 0  # no cost DB configured; nothing to do
    logged = load_state()
    rows, seen_now = collect_new_rows(logged)
    if not rows:
        return 0
    values = ",\n".join(to_values_tuple(r) for r in rows)
    sql = (
        "INSERT INTO cost_event "
        "(service, provider, model, input_tokens, output_tokens, cost_usd, duration_ms, metadata) "
        f"VALUES\n{values};"
    )
    cmd = [
        "docker", "exec", "-e", f"PGPASSWORD={env['COST_PG_PASSWORD']}",
        env["COST_DB_CONTAINER"],
        "psql", "-h", env["COST_PG_HOST"], "-p", env["COST_PG_PORT"],
        "-U", env["COST_PG_USER"], "-d", env["COST_PG_DB"],
        "-v", "ON_ERROR_STOP=1", "-c", sql,
    ]
    try:
        res = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except Exception as e:  # noqa: BLE001 — best-effort, never raise
        sys.stderr.write(f"cost-logger: docker exec failed: {e}\n")
        return 0
    if res.returncode != 0:
        sys.stderr.write(f"cost-logger: psql failed: {res.stderr.strip()}\n")
        return 0  # do NOT mark logged, so we retry next run
    save_state(logged | seen_now)
    sys.stdout.write(f"cost-logger: inserted {len(rows)} cost_event row(s)\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
