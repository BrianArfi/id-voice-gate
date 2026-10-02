# Integrations

[Back to the README](../README.md)

Put the gate where text leaves your hands: a commit, a pull request, a publish script, an agent. Lint is free and instant, so run it everywhere. The judge costs a few seconds per text, so run it where text is final.

## Claude Code (agent skill)

The repository root has a [`SKILL.md`](../SKILL.md), so it installs as a skill:

```bash
git clone https://github.com/BrianArfi/id-voice-gate ~/.claude/skills/id-voice-gate
pip install ~/.claude/skills/id-voice-gate     # optional: puts `idgate` on PATH
```

Then ask in plain words: "cek caption ini pakai id-voice-gate sebelum dipost", or "run id-voice-gate over src/locales/id.json and fix what fails". The skill tells the agent to publish only the gated text, to hold anything that fails or meets a dead judge, and never to rewrite text a human already approved.

## pre-commit

Lint only, so a commit never waits on a model:

```yaml
# .pre-commit-config.yaml
repos:
  - repo: https://github.com/BrianArfi/id-voice-gate
    rev: v1.0.0
    hooks:
      - id: idgate-site     # Indonesian strings in JSON, HTML, markdown, TS/TSX
      # - id: idgate-lint   # or: markdown and text files only
```

## GitHub Actions

Copy [`examples/github/id-voice-gate.yml`](../examples/github/id-voice-gate.yml) to `.github/workflows/`. It lints every pull request. When the repository has an `ANTHROPIC_API_KEY` secret, it also runs the judge and uploads a JSON report. A judge outage (exit 2) fails the job, the same as a failed text.

## A publish script

```bash
if idgate gate --file caption.txt --kind caption > final.txt; then
  publish final.txt
else
  echo "held: see the reason above" >&2
fi
```

```python
import idgate

g = idgate.gate(caption, kind="caption", context={"source": "ig_queue", "item_id": slot_id})
if not g["passed"]:
    hold(slot_id, reason="id-voice-gate: " + g["report"]["reason"])
else:
    publish(g["final_text"])
```

Text a human already approved: `gate --no-rewrite` (or `rewrite=False`). Showing a person one text and publishing another is worse than holding it.

## A website or app

```bash
idgate site src/locales public --lint-only                 # in the build, fails on a block rule
idgate site src/locales public --include-ts --fix --workers 4   # by hand, before a release
git diff                                                   # review what the judge changed
```

Exclude quotes from the scan with `--exclude`: testimonials and quoted posts are archive, not copy.

## Testing your own pipeline

Do not call a real model in unit tests. Either pass `judge_fn=` in Python, or use the `command` backend with a fake judge such as [`tests/fake_judge.py`](../tests/fake_judge.py):

```bash
IDGATE_JUDGE=command IDGATE_JUDGE_CMD="python3 tests/fake_judge.py" pytest
```

Set `IDGATE_DATA_DIR` to a temporary folder so the tests do not share a cache with real runs.
