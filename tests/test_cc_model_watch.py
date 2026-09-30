import io
import json
import os
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

    def test_latest_oversized_assistant_model_is_found(self):
        latest = json.dumps({
            "type": "assistant",
            "message": {"model": "claude-opus-4-8", "content": "x" * 250000},
        })
        path = write_transcript([assistant("claude-fable-5"), latest])
        self.addCleanup(os.unlink, path)
        self.assertEqual(w.last_served_model(path), "claude-opus-4-8")

    def test_oversized_tool_result_does_not_hide_model(self):
        tool_result = json.dumps({
            "type": "user",
            "message": {"role": "user", "content": "x" * 250000},
        })
        path = write_transcript([assistant("claude-opus-4-8"), tool_result])
        self.addCleanup(os.unlink, path)
        self.assertEqual(w.last_served_model(path), "claude-opus-4-8")
        self.assertEqual(
            w.detect(stdin_json("claude-fable-5", path)),
            ("claude-fable-5", "claude-opus-4-8"),
        )

    def test_ignores_non_assistant_model(self):
        path = write_transcript([
            assistant("claude-opus-4-8"),
            json.dumps({"message": {"role": "user", "model": "decoy"}}),
        ])
        self.addCleanup(os.unlink, path)
        self.assertEqual(w.last_served_model(path), "claude-opus-4-8")

    def test_skips_non_object_records_and_messages(self):
        for record in ([{"model": "decoy"}], {"message": ["model"]}):
            with self.subTest(record=record):
                path = write_transcript([
                    assistant("claude-opus-4-8"), json.dumps(record),
                ])
                self.addCleanup(os.unlink, path)
                self.assertEqual(w.last_served_model(path), "claude-opus-4-8")

    def test_skips_line_above_parse_cap_and_continues(self):
        latest = json.dumps({
            "type": "assistant",
            "message": {"model": "decoy", "content": "x" * w.MAX_LINE_BYTES},
        })
        path = write_transcript([assistant("claude-opus-4-8"), latest])
        self.addCleanup(os.unlink, path)
        self.assertEqual(w.last_served_model(path), "claude-opus-4-8")

    def test_does_not_parse_valid_json_suffix_of_partial_line(self):
        suffix = assistant("decoy")
        path = write_transcript([assistant("claude-opus-4-8"), "x" * 100 + suffix])
        self.addCleanup(os.unlink, path)
        self.assertIsNone(w.last_served_model(path, tail_bytes=len(suffix) + 1))

    def test_default_history_limit_is_bounded(self):
        path = write_transcript([assistant("claude-opus-4-8"), "x" * w.TAIL_BYTES])
        self.addCleanup(os.unlink, path)
        self.assertIsNone(w.last_served_model(path))

    def test_latest_record_without_final_newline(self):
        path = write_transcript([assistant("claude-fable-5")])
        self.addCleanup(os.unlink, path)
        with open(path, "ab") as f:
            f.write(assistant("claude-opus-4-8").encode("utf-8"))
        self.assertEqual(w.last_served_model(path), "claude-opus-4-8")

    def test_recent_model_stops_reading_before_old_history(self):
        data = ("x" * (2 * w.TAIL_BYTES) + "\n" + assistant("claude-opus-4-8"))
        transcript = io.BytesIO(data.encode("utf-8"))
        transcript.read = mock.Mock(wraps=transcript.read)
        with mock.patch("cc_model_watch.open", return_value=transcript):
            self.assertEqual(w.last_served_model("session.jsonl"), "claude-opus-4-8")
        self.assertEqual(transcript.read.call_count, 1)
        self.assertLess(transcript.read.call_args[0][0], w.TAIL_BYTES)

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


if __name__ == "__main__":
    unittest.main()
