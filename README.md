# tg-claude

Drive this VM from Telegram. Send the bot a message → [OpenClaw](https://openclaw.ai)
feeds it to the local **`claude` CLI** (Opus 4.8, reusing the host's Claude
subscription — **no Anthropic API key**) → Claude does the work across `~/src`
→ the reply comes back in Telegram.

It's the "control my server from my phone" setup, built by reusing OpenClaw's
native **CLI backend** rather than wiring up an API key.

## Architecture

```
You (phone, Telegram)  ⇄  Telegram Bot API   (outbound long-poll; no public ingress)
        │
   OpenClaw Gateway   (host, systemd --user, uid 1000, 127.0.0.1:18789)
        │   channel=telegram · dmPolicy=allowlist · allowFrom=[your id]
        │   model = claude-cli/claude-opus-4-8
        ▼
   claude CLI   (cwd ~/src · full tool access · reuses ~/.claude OAuth)
        ▼
   reply → Telegram
```

Runs on the host (not Docker), exactly like `cc-web`, because it needs the
host user's `~/.claude` OAuth and uid-1000 environment.

## Security model

The **only** gate is the Telegram allowlist. Claude runs non-interactively with
full read/write over `~/src` (async chat can't answer permission prompts), so:

- `channels.telegram.dmPolicy = "allowlist"` and `allowFrom = [<your numeric id>]`
  — nobody else's messages are processed.
- `channels.telegram.groupPolicy = "disabled"` — DMs only.
- Gateway binds loopback only; no nginx route, nothing new exposed publicly.

Get your numeric id from [@userinfobot](https://t.me/userinfobot). Treat the bot
token like a password — it lives in `~/.config/tg-claude.telegram-token` (0600),
never in git.

> ⚠️ **Billing note:** the local `claude` CLI runs on the host subscription;
> OpenClaw's transcripts report `cost.total = 0` (no marginal API charge). If
> Anthropic applies third-party-harness pay-as-you-go billing, watch your
> subscription usage after first use. `bin/cost-logger.py` records per-message
> token volume into the shared `cost_tracker` DB for visibility.

## Setup (reproduce from scratch)

```bash
npm install -g openclaw@latest                     # needs Node 24 (have v24.15.0)
openclaw setup --non-interactive --accept-risk --workspace /home/liharr/src
openclaw config set agents.defaults.model.primary claude-cli/claude-opus-4-8

# Telegram — create a bot via @BotFather, then:
umask 077; printf '%s' '<BOT_TOKEN>' > ~/.config/tg-claude.telegram-token
openclaw config patch --stdin <<'JSON5'
{ channels: { telegram: {
    enabled: true,
    tokenFile: "/home/liharr/.config/tg-claude.telegram-token",
    dmPolicy: "allowlist",
    allowFrom: ["<YOUR_TELEGRAM_USER_ID>"],
    groupPolicy: "disabled",
} } }
JSON5
openclaw config validate

# Service (systemd --user; linger already enabled for this user)
cp systemd/tg-claude.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now tg-claude
journalctl --user -u tg-claude -f          # watch it connect to Telegram
```

`config.example.json5` is the sanitised shape of the resulting `~/.openclaw/openclaw.json`.

## /status + cost integration

Wired into the fleet `/status` dashboard and `cost_tracker`:

- **`bin/heartbeat.sh`** — cron (1 min) touches `~/.local/share/cron-heartbeats/tg-claude`
  only while the service is active. Registered as job `tg-claude` in
  `status/web/src/lib/checks/cron.ts`.
- **`bin/cost-logger.py`** — cron (every few min) reads OpenClaw session
  transcripts and inserts per-message token usage as `cost_event` rows
  (`service='tg-claude'`), idempotent via transcript message ids.

Crontab:

```cron
* * * * * /home/liharr/src/tg-claude/bin/heartbeat.sh >/dev/null 2>&1
*/5 * * * * /usr/bin/python3 /home/liharr/src/tg-claude/bin/cost-logger.py >>/home/liharr/.local/state/tg-claude/cost-logger.log 2>&1
```

## Why not nginx / site-index / Umami?

Telegram is outbound long-polling, so there's no inbound HTTP to route or page
to visit — those fleet conventions don't apply here. /status liveness and cost
logging do, and are wired up above.
