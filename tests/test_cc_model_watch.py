import io
import json
import os
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import cc_model_watch as w


def write_transcript(lines):
    f = tempfile.NamedTemporaryFile(
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for line in lines:
        f.write(line + "\n")
    f.close()
    return f.name


def assistant(model):
    return json.dumps({"message": {"role": "assistant", "model": model}})


def stdin_json(selected, transcript, display="Fable 5", cwd="/tmp/demo"):
    return {
        "model": {"id": selected, "display_name": display},
        "transcript_path": transcript,
        "workspace": {"current_dir": cwd},
        "session_id": "test-session",
    }


class TestLastServedModel(unittest.TestCase):
    def test_returns_latest_assistant_model(self):
        path = write_transcript([assistant("claude-fable-5"), assistant("claude-opus-4-8")])
        self.assertEqual(w.last_served_model(path), "claude-opus-4-8")

    def test_skips_synthetic(self):
        path = write_transcript([assistant("claude-fable-5"), assistant("<synthetic>")])
        self.assertEqual(w.last_served_model(path), "claude-fable-5")

    def test_skips_malformed_lines(self):
        path = write_transcript([assistant("claude-fable-5"), '{"model": broken'])
        self.assertEqual(w.last_served_model(path), "claude-fable-5")

    def test_missing_file_returns_none(self):
        self.assertIsNone(w.last_served_model("/nonexistent/path.jsonl"))

    def test_empty_transcript_returns_none(self):
        self.assertIsNone(w.last_served_model(write_transcript([])))

    def test_ignores_user_messages_without_model(self):
        path = write_transcript(
            [assistant("claude-opus-4-8"), json.dumps({"message": {"role": "user"}})]
        )
        self.assertEqual(w.last_served_model(path), "claude-opus-4-8")

    def test_tail_window_respected(self):
        # Old model beyond the tail window must be invisible.
        filler = [assistant("claude-opus-4-8")] + [
            json.dumps({"pad": "x" * 100, "model": "decoy-not-a-message"})
        ] * 50
        path = write_transcript(filler)
        self.assertIsNone(w.last_served_model(path, tail_bytes=1000))


class TestDetect(unittest.TestCase):
    def test_mismatch_detected(self):
        path = write_transcript([assistant("claude-opus-4-8")])
        got = w.detect(stdin_json("claude-fable-5", path))
        self.assertEqual(got, ("claude-fable-5", "claude-opus-4-8"))

    def test_match_returns_none(self):
        path = write_transcript([assistant("claude-fable-5")])
        self.assertIsNone(w.detect(stdin_json("claude-fable-5", path)))

    def test_missing_fields_return_none(self):
        self.assertIsNone(w.detect({}))
        self.assertIsNone(w.detect({"model": {"id": "claude-fable-5"}}))


class TestOutput(unittest.TestCase):
    def run_main(self, argv, payload):
        buf = io.StringIO()
        real = sys.stdout
        sys.stdout = buf
        try:
            code = w.main(argv, stdin=json.dumps(payload))
        finally:
            sys.stdout = real
        return code, buf.getvalue()

    def test_segment_mode_silent_when_ok(self):
        path = write_transcript([assistant("claude-fable-5")])
        code, out = self.run_main([], stdin_json("claude-fable-5", path))
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_segment_mode_warns_on_switch(self):
        path = write_transcript([assistant("claude-opus-4-8")])
        code, out = self.run_main(["--no-color"], stdin_json("claude-fable-5", path))
        self.assertEqual(code, 0)
        self.assertIn("model switched fable-5 → opus-4-8", out)
        self.assertNotIn("\033[", out)

    def test_statusline_mode_full_line(self):
        path = write_transcript([assistant("claude-opus-4-8")])
        code, out = self.run_main(
            ["--statusline", "--no-color"], stdin_json("claude-fable-5", path)
        )
        self.assertEqual(code, 0)
        self.assertIn("Fable 5", out)
        self.assertIn("model switched", out)
        self.assertIn("demo", out)

    def test_statusline_mode_no_warning_when_ok(self):
        path = write_transcript([assistant("claude-fable-5")])
        code, out = self.run_main(["--statusline"], stdin_json("claude-fable-5", path))
        self.assertEqual(code, 0)
        self.assertNotIn("switched", out)
        self.assertIn("Fable 5", out)

    def test_empty_stdin_is_safe(self):
        code, out = self.run_main([], {})
        self.assertEqual(code, 0)
        self.assertEqual(out, "")

    def test_invalid_json_stdin_is_safe(self):
        buf = io.StringIO()
        real = sys.stdout
        sys.stdout = buf
        try:
            code = w.main([], stdin="not json {{{")
        finally:
            sys.stdout = real
        self.assertEqual(code, 0)
        self.assertEqual(buf.getvalue(), "")


class TestNotifyCooldown(unittest.TestCase):
    def test_second_notify_within_cooldown_is_suppressed(self):
        tmp = tempfile.mkdtemp()
        old = w.STATE_DIR
        w.STATE_DIR = tmp
        try:
            mismatch = ("claude-fable-5", "claude-opus-4-8")
            first = w.maybe_notify(mismatch, "s1")
            second = w.maybe_notify(mismatch, "s1")
            self.assertFalse(second)
            # first is True only if a notifier binary exists; stamp must exist either way
            self.assertTrue(os.listdir(tmp))
            self.assertIn(first, (True, False))
        finally:
            w.STATE_DIR = old


class TestNotifyDarwinArgv(unittest.TestCase):
    def test_osascript_uses_argv_not_interpolated_e_flag(self):
        """Model ids with AppleScript metacharacters must not enter a -e script."""
        tmp = tempfile.mkdtemp()
        old_state, old_platform = w.STATE_DIR, sys.platform
        w.STATE_DIR = tmp
        sys.platform = "darwin"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append((list(cmd), kwargs))
            return subprocess.CompletedProcess(cmd, 0)

        # Crafted served model: quote + AppleScript breakout attempt
        crafted = 'claude-evil"; do shell script "id'
        mismatch = ("claude-fable-5", crafted)
        try:
            with mock.patch.object(subprocess, "run", side_effect=fake_run):
                ok = w.maybe_notify(mismatch, "sec07")
            self.assertTrue(ok)
            self.assertEqual(len(calls), 1)
            cmd, kwargs = calls[0]
            self.assertEqual(cmd[0], "osascript")
            self.assertEqual(cmd[1], "-")
            self.assertNotIn("-e", cmd)
            # Body/title are separate argv after the script source marker.
            body = cmd[2]
            title = cmd[3]
            self.assertIn('evil"; do shell script "id', body)
            self.assertEqual(title, "Claude Code: model switched")
            # Script text is passed via stdin, not via .format into -e.
            script = kwargs.get("input") or ""
            self.assertIn("on run argv", script)
            self.assertNotIn(crafted, script)
            self.assertNotIn(body, script)
            # Cooldown stamp written even when subprocess is mocked.
            self.assertTrue(os.listdir(tmp))
        finally:
            w.STATE_DIR = old_state
            sys.platform = old_platform

    def test_linux_notify_send_argv_unchanged(self):
        tmp = tempfile.mkdtemp()
        old_state, old_platform = w.STATE_DIR, sys.platform
        w.STATE_DIR = tmp
        sys.platform = "linux"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0)

        mismatch = ("claude-fable-5", 'claude-x"; rm -rf tmp')
        try:
            with mock.patch.object(subprocess, "run", side_effect=fake_run):
                ok = w.maybe_notify(mismatch, "linux-sec")
            self.assertTrue(ok)
            self.assertEqual(calls[0][0], "notify-send")
            self.assertEqual(calls[0][1], "Claude Code: model switched")
            self.assertIn('x"; rm -rf tmp', calls[0][2])
        finally:
            w.STATE_DIR = old_state
            sys.platform = old_platform

    def test_cooldown_suppresses_second_call_with_mocked_subprocess(self):
        tmp = tempfile.mkdtemp()
        old_state, old_platform = w.STATE_DIR, sys.platform
        w.STATE_DIR = tmp
        sys.platform = "darwin"
        calls = []

        def fake_run(cmd, **kwargs):
            calls.append(list(cmd))
            return subprocess.CompletedProcess(cmd, 0)

        mismatch = ("claude-fable-5", "claude-opus-4-8")
        try:
            with mock.patch.object(subprocess, "run", side_effect=fake_run):
                first = w.maybe_notify(mismatch, "cool")
                second = w.maybe_notify(mismatch, "cool")
            self.assertTrue(first)
            self.assertFalse(second)
            self.assertEqual(len(calls), 1)
        finally:
            w.STATE_DIR = old_state
            sys.platform = old_platform


if __name__ == "__main__":
    unittest.main()
