# cc-model-watch

[![CI](https://github.com/majiayu000/cc-model-watch/actions/workflows/ci.yml/badge.svg)](https://github.com/majiayu000/cc-model-watch/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://github.com/majiayu000/cc-model-watch/blob/main/LICENSE)
[![Python](https://img.shields.io/badge/python-3.8%2B-blue)](https://www.python.org/)

**A local Claude Code statusline warning for selected and served model mismatches.**

`cc-model-watch` is a Python 3.8+ script with no third-party dependencies. It
compares the selected model in Claude Code's statusline input with the latest
assistant model it can read from the transcript tail. A mismatch produces a red
statusline warning and an optional desktop notification; it does not establish
why the models differ.

[Install](#install) · [Configuration](#configuration) ·
[Known false positive](#known-false-positive)

Example warning:

```
Fable 5 ｜ 🔻 model switched fable-5 → opus-4-8 ｜ my-project
```

In a 14-day sample of one heavy user's local sessions, 6 out of 69 sessions
contained a mid-session model switch. You probably want to know when it happens
to you.

## How it works

Claude Code passes your **selected** model to statusline scripts via stdin JSON
(`model.id`). The session transcript (`transcript_path`) records the model that
**actually served** each assistant message (`message.model`). When they
disagree, the script reports a mismatch. No network calls, no scraping — both values
come from Claude Code itself, read locally.

Only the last ~200 KB of the transcript is read, so it stays fast on long
sessions (well under Claude Code's statusline timeout).

## Install

```bash
git clone https://github.com/majiayu000/cc-model-watch ~/.claude/cc-model-watch
```

Zero dependencies — Python 3.8+ standard library only.

### Option A: standalone statusline

In `~/.claude/settings.json`:

```json
{
  "statusLine": {
    "type": "command",
    "command": "python3 ~/.claude/cc-model-watch/cc_model_watch.py --statusline"
  }
}
```

Shows `model ｜ [warning] ｜ directory`.

### Option B: embed into your existing statusline (recommended)

Segment mode prints **only** the warning (empty when everything is fine), so
you can splice it into any statusline script — bash, ccstatusline custom
command, powerline segment:

```bash
INPUT=$(cat)
WATCH=$(printf '%s' "$INPUT" | python3 ~/.claude/cc-model-watch/cc_model_watch.py)
# ... build the rest of your line, prepend $WATCH when non-empty
```

### Option C: desktop notification

Add `--notify` to either mode to also fire a desktop notification
(macOS `osascript` / Linux `notify-send`) on a fresh switch, rate-limited per
session and model.

## Configuration

Everything is optional, via environment variables:

| Variable | Default | Meaning |
|---|---|---|
| `CC_MODEL_WATCH_TAIL_BYTES` | `200000` | How much of the transcript tail to scan |
| `CC_MODEL_WATCH_COOLDOWN` | `300` | Seconds between repeat notifications |
| `CC_MODEL_WATCH_STATE_DIR` | `~/.cache/cc-model-watch` | Notification stamp files |

Flags: `--statusline` (full line), `--notify` (desktop alert), `--no-color`.

## Known false positive

After you manually switch models with `/model`, the last transcript message
still carries the old model until the next reply arrives — so you'll see one
transient warning. It clears on the next assistant message.

## Why not just read the statusline model name?

The selected model name alone does not show the model recorded in the latest
assistant message. This tool compares those two local values. A manual `/model`
change can also produce a temporary mismatch, as described above.

## Development

```bash
python3 -m unittest discover -s tests -v
```

## License

MIT
