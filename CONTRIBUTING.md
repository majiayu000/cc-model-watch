# Contributing to cc-model-watch

Thanks for your interest in contributing!

## Setup

```bash
git clone https://github.com/majiayu000/cc-model-watch
cd cc-model-watch
```

No dependencies to install — Python 3.8+ standard library only.

## Build

There is no build step. The tool is a single file: `cc_model_watch.py`.

## Test

```bash
python3 -m unittest discover -s tests -v
```

All tests must pass before submitting a PR. New behavior needs a matching test.

## Guidelines

- Keep it zero-dependency (stdlib only). PRs adding third-party packages will
  be declined unless there is a very strong reason.
- Keep the hot path fast: the script runs on every statusline render. Avoid
  network calls and full-file reads.
- One logical change per PR.

## Reporting issues

Use the issue templates. For suspected false positives/negatives, include the
relevant transcript lines (redact content — only the `message.model` fields
matter).
