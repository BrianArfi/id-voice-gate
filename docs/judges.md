# Judges

[Back to the README](../README.md)

The judge is the part that hears whether a sentence sounds native. Pick a backend with `--judge` or `IDGATE_JUDGE`. Every backend gets the same prompt: the full style guide, the lint findings, the text, and the rules for a safe rewrite. Every backend fails closed.

| Backend | Needs | Default model | Use it when |
| :--- | :--- | :--- | :--- |
| `claude-cli` (default) | [Claude Code](https://claude.com/claude-code) installed and logged in | `sonnet` | You already use Claude Code. No API key, runs on your subscription |
| `anthropic-api` | `ANTHROPIC_API_KEY` | `claude-sonnet-5` | CI, servers, anywhere without a logged-in CLI |
| `command` | any command | your choice | A local model, another provider, a proxy, or a fake judge in tests |
| `none` | nothing | | Lint only. Every result says so, and it never counts as a pass to publish |

`--model` or `IDGATE_MODEL` overrides the model for any backend.

## claude-cli

```bash
idgate check --text "Gak ribet."                # uses `claude` on PATH
CLAUDE_BIN=/opt/claude/bin/claude idgate check --file draft.md
```

Runs `claude -p` with the prompt on stdin, tools disabled, in a neutral working directory so your project's `CLAUDE.md` does not reach the judge. A typical check takes 6 to 10 seconds. If `claude` is missing or logged out, the result is `judge_unavailable` and exit 2.

## anthropic-api

```bash
export ANTHROPIC_API_KEY=...        # from your secret store, never committed
idgate check --judge anthropic-api --file draft.md
```

Plain `urllib` against the Messages API, no SDK. `ANTHROPIC_BASE_URL` points it at a proxy. `IDGATE_MAX_TOKENS` (default 8192) caps the answer, which holds the full rewrite.

## command

The command gets the whole prompt on stdin and must print one JSON object:

```json
{"natural": false, "score": 2, "issues": [{"quote": "Tiga langkah, udah.", "why": "punchline iklan", "fix": "Cuma tiga langkah."}], "rewrite": "Cuma tiga langkah."}
```

```bash
idgate check --judge command --judge-cmd "ollama run qwen3:32b" --file draft.md
IDGATE_JUDGE=command IDGATE_JUDGE_CMD="python3 my_judge.py" idgate site locales
```

Exit code other than 0, or output with no JSON object, is `judge_unavailable`. One retry is made when the output is not valid JSON. [`tests/fake_judge.py`](../tests/fake_judge.py) is a complete offline example.

A smaller model is a weaker ear. Check its verdicts against a set of texts you already know are good and bad before you trust it as a gate.

## How strict is the judge?

Strict on purpose. In the real run recorded for the README, the default judge failed `Tiga langkah, udah.` with score 2 and passed a hand-written three-line post with score 5. It also, on one run, failed a bare `Cuma tiga langkah.` with no context around it as a slogan. A judge that occasionally holds a fine line costs a rewrite round; a judge that lets translated copy through costs readers. When it holds something you are sure is natural, publish it by hand and add the case to your own tests.

The judge is a model, so two runs can word their issues differently. The cache makes a verdict stable for the same text, guide and model.
