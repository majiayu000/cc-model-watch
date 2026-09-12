#!/usr/bin/env python3
"""cc-model-watch — know instantly when Claude Code silently swaps your model.

Claude Code's statusline stdin JSON carries the model you SELECTED
(``model.id``), while the session transcript records the model that
ACTUALLY SERVED each assistant message (``message.model``). When they
disagree, you have been downgraded/switched — this tool makes that loud.

Zero dependencies (stdlib only). Two modes:

  segment mode (default) — prints only the warning segment (empty if OK),
      for embedding into any existing statusline script:
          WATCH=$(echo "$INPUT" | python3 cc_model_watch.py)

  --statusline — prints a complete minimal statusline
      (model | warning | dir), usable directly as the statusLine command.

Optional desktop notification on a fresh switch: --notify
(macOS osascript / Linux notify-send, rate-limited by a cooldown).
"""

import json
import os
import subprocess
import sys
import time

TAIL_BYTES = int(os.environ.get("CC_MODEL_WATCH_TAIL_BYTES", "200000"))
COOLDOWN_SECONDS = int(os.environ.get("CC_MODEL_WATCH_COOLDOWN", "300"))
STATE_DIR = os.environ.get(
    "CC_MODEL_WATCH_STATE_DIR",
    os.path.join(os.path.expanduser("~"), ".cache", "cc-model-watch"),
)


def last_served_model(transcript_path, tail_bytes=None):
    """Return the model id of the most recent assistant message, or None.

    Only tails the last ``tail_bytes`` of the transcript so the statusline
    stays fast on long sessions. Skips ``<synthetic>`` (error placeholder)
    entries and malformed lines.
    """
    if tail_bytes is None:
        tail_bytes = TAIL_BYTES
    try:
        with open(transcript_path, "rb") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - tail_bytes))
            lines = f.read().decode("utf-8", "ignore").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if '"model"' not in line:
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        model = (obj.get("message") or {}).get("model")
        if model and model != "<synthetic>":
            return model
    return None


def detect(stdin_obj):
    """Return (selected, served) when they disagree, else None."""
    selected = (stdin_obj.get("model") or {}).get("id") or ""
    transcript = stdin_obj.get("transcript_path") or ""
    if not selected or not transcript:
        return None
    served = last_served_model(transcript)
    if served and served != selected:
        return (selected, served)
    return None


def short(model_id):
    return model_id.replace("claude-", "")


def warning_segment(mismatch, color=True):
    a, b = short(mismatch[0]), short(mismatch[1])
    text = "\U0001f53b model switched {} → {}".format(a, b)
    if color:
        return "\033[1;31m{}\033[0m".format(text)
    return text


def maybe_notify(mismatch, session_id):
    """Fire one desktop notification per (session, served-model), rate-limited.

    Best-effort: this is an optional side channel — the statusline warning is
    the primary signal — so notifier absence or failure is intentionally
    non-fatal.
    """
    stamp = os.path.join(
        STATE_DIR, "notified-{}-{}".format(session_id or "global", short(mismatch[1]))
    )
    try:
        if os.path.exists(stamp) and time.time() - os.path.getmtime(stamp) < COOLDOWN_SECONDS:
            return False
        os.makedirs(STATE_DIR, exist_ok=True)
        with open(stamp, "w") as f:
            f.write(str(time.time()))
    except OSError:
        return False
    title = "Claude Code: model switched"
    body = "{} → {}".format(short(mismatch[0]), short(mismatch[1]))
    try:
        if sys.platform == "darwin":
            # Pass body/title as osascript argv — never interpolate into an
            # AppleScript -e string literal (SEC-07 command injection).
            script = (
                "on run argv\n"
                "  display notification (item 1 of argv) "
                "with title (item 2 of argv)\n"
                "end run\n"
            )
            subprocess.run(
                ["osascript", "-", body, title],
                input=script,
                capture_output=True,
                timeout=5,
                check=False,
                text=True,
            )
        else:
            subprocess.run(
                ["notify-send", title, body],
                capture_output=True,
                timeout=5,
                check=False,
            )
        return True
    except (OSError, subprocess.TimeoutExpired):
        return False


def main(argv=None, stdin=None):
    argv = sys.argv[1:] if argv is None else argv
    raw = (stdin if stdin is not None else sys.stdin.read()) or ""
    try:
        obj = json.loads(raw) if raw.strip() else {}
    except json.JSONDecodeError:
        obj = {}

    color = "--no-color" not in argv
    mismatch = detect(obj)
    if mismatch and "--notify" in argv:
        maybe_notify(mismatch, obj.get("session_id"))

    if "--statusline" in argv:
        parts = []
        display = (obj.get("model") or {}).get("display_name")
        if display:
            parts.append(display)
        if mismatch:
            parts.append(warning_segment(mismatch, color))
        cwd = os.path.basename(
            ((obj.get("workspace") or {}).get("current_dir") or "").rstrip("/")
        )
        if cwd:
            parts.append(cwd)
        print(" ｜ ".join(parts))
    elif mismatch:
        print(warning_segment(mismatch, color))
    return 0


if __name__ == "__main__":
    sys.exit(main())
