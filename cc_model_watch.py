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

TAIL_BYTES = int(os.environ.get("CC_MODEL_WATCH_TAIL_BYTES", "4194304"))
MAX_LINE_BYTES = 1024 * 1024
COOLDOWN_SECONDS = int(os.environ.get("CC_MODEL_WATCH_COOLDOWN", "300"))
STATE_DIR = os.environ.get(
    "CC_MODEL_WATCH_STATE_DIR",
    os.path.join(os.path.expanduser("~"), ".cache", "cc-model-watch"),
)


def _reverse_lines(f, tail_bytes):
    """Yield complete lines newest first, with bounded reads and line storage."""
    f.seek(0, 2)
    position = f.tell()
    start = max(0, position - tail_bytes)
    pending = b""
    oversized = False
    while position > start:
        size = min(65536, position - start)
        position -= size
        f.seek(position)
        parts = f.read(size).split(b"\n")
        if not oversized:
            pending = parts[-1] + pending
            oversized = len(pending) > MAX_LINE_BYTES
            if oversized:
                pending = b""
        if len(parts) > 1:
            if not oversized:
                yield pending
            yield from reversed(parts[1:-1])
            pending = parts[0]
            oversized = False
    # At the history boundary, pending may start in the middle of a record.
    if pending and not oversized:
        if start > 0:
            f.seek(start - 1)
            if f.read(1) != b"\n":
                return
        yield pending


def last_served_model(transcript_path, tail_bytes=None):
    """Return the model id of the most recent assistant message, or None.

    Scan complete lines backwards within ``tail_bytes`` (4 MiB by default).
    Skip lines over 1 MiB, synthetic entries and malformed records. Stop as
    soon as a model is found so normal statusline reads stay small.
    """
    if tail_bytes is None:
        tail_bytes = TAIL_BYTES
    try:
        with open(transcript_path, "rb") as f:
            for line in _reverse_lines(f, tail_bytes):
                if b'"model"' not in line:
                    continue
                try:
                    obj = json.loads(line.decode("utf-8", "ignore"))
                except json.JSONDecodeError:
                    continue
                if not isinstance(obj, dict):
                    continue
                message = obj.get("message")
                if not isinstance(message, dict):
                    continue
                if obj.get("type") != "assistant" and message.get("role") != "assistant":
                    continue
                model = message.get("model")
                if model and model != "<synthetic>":
                    return model
    except OSError:
        return None
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
    if sys.platform == "darwin":
        cmd = [
            "osascript", "-e",
            'display notification "{}" with title "{}"'.format(body, title),
        ]
    else:
        cmd = ["notify-send", title, body]
    try:
        subprocess.run(cmd, capture_output=True, timeout=5, check=False)
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
