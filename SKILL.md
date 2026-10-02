---
name: id-voice-gate
description: Gate any AI-written Indonesian text (post, caption, web page, heading, chat message, app locale file) before it ships. Catches text that reads like a translation with deterministic lint plus an LLM judge, rewrites it safely, and holds it when it still fails or the judge is down. Use before publishing Indonesian copy, when reviewing an id.json locale or a site, or when a reader says the Indonesian sounds "aneh" or "kayak terjemahan".
---

# id-voice-gate

AI-written Indonesian often reads like a translation: the sentence was built in English, then swapped word by word. Readers notice. This skill checks Indonesian text before it is published and blocks the translated-sounding parts.

Three layers on every check:

1. **Lint**: regex rules in `idgate/rules.json`. A `block` rule fails the text. A `warn` rule is a hint for the judge.
2. **Judge**: an LLM reads the text against a style guide (`idgate/guides/`) and returns `{natural, score 1-5, issues, rewrite}`.
3. **Extra checks** (optional): your own checkers. Their `block` findings fail the text too.

**Pass = no block finding, judge says natural, score 4 or higher.** A judge that is down, not logged in, or returns broken JSON is a FAIL (exit 2). Never publish because "the judge was down".

## Install

```bash
pip install git+https://github.com/BrianArfi/id-voice-gate
# or, with no install, from a clone:
python3 -m idgate --help
```

As an agent skill: copy this repository into your skills folder, for example `cp -r id-voice-gate ~/.claude/skills/`. Then run `python3 -m idgate` from that folder, or install it with pip.

Python 3.10 or later, standard library only. The default judge is the `claude` CLI (Claude Code) with your own login, so no API key is needed.

## Commands

```bash
idgate check --text "Tiga langkah, udah."            # lint + judge, prints issues and a suggested rewrite
idgate gate  --file caption.txt --kind caption      # rewrite on failure; final text on stdout, status on stderr
idgate lint  --file draft.md                        # rules only, no model, instant
idgate sweep --dir content/blog --glob "*.md"       # one file = one text
idgate site  src/locales public --include-ts --fix  # every Indonesian string in a project, judged in batches
idgate mode                                         # enforce or shadow
```

Exit codes: **0 pass, 1 fail, 2 judge unavailable.** Treat 1 and 2 the same way: hold the text.

`--kind`: `post`, `caption`, `page`, `heading`, `message`. `--register casual|formal` overrides the automatic choice (casual when the text uses gw/lo). `--judge claude-cli|anthropic-api|command|none` picks the judge. `--judge none` and `--lint-only` are audits, never a pass to publish.

## Rules for the agent

1. **Gate at the publish point**, after the text is final and before it goes anywhere: a post queue, a website, a message. Not in the middle of drafting.
2. **Publish `final_text` from `gate`**, never the original. It may have been rewritten.
3. **`passed` false means hold it.** Report the reason to the user. Do not fall back to the original text, and do not post "because the judge was down".
4. **Text a human already approved gets a check only** (`gate --no-rewrite`, or `check`). Never publish a rewrite the human has not seen. If it fails, show the issues and ask.
5. **A judge rewrite changes wording only.** The gate rejects rewrites that change a number, URL, hashtag, mention, emoji, placeholder, quote, product name, paragraph count or negation count. If your pipeline has its own fact or claim checks, run them again on the rewrite.
6. **Do not edit the style guide or rules to make a text pass.** Fix the text. Change a rule only when it fires on natural writing by a native speaker, and add a test.
7. **Do not touch quotes.** Testimonials and quotes from other people stay as written, even when they read oddly.

## Python

```python
import idgate

r = idgate.check(text, kind="post", context={"source": "weekly_post", "item_id": "2026-10-02"})
g = idgate.gate(text, kind="caption")
if not g["passed"]:
    hold(reason=g["report"]["reason"])
else:
    publish(g["final_text"])
```

More: [README](README.md), [how it works](docs/how-it-works.md), [commands](docs/commands.md), [judges](docs/judges.md), [rules and guides](docs/rules.md), [integrations](docs/integrations.md).
