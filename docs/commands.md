# Commands, flags and exit codes

[Back to the README](../README.md)

`idgate` after `pip install`, or `python3 -m idgate` from a clone. Same thing.

## Exit codes

| Code | Meaning | What to do |
| :--- | :--- | :--- |
| `0` | Pass | Publish (for `gate`: publish the text it printed) |
| `1` | Fail: a lint block, or the judge said not natural | Hold it. Fix the text, or use the rewrite |
| `2` | Judge unavailable: CLI missing or logged out, API error, broken answer, missing guide | Hold it. Never publish on an outage |

`--lint-only` and `--judge none` never return 2. Their 0 means "no rule fired", which is an audit, not a pass to publish.

## check

Lint and judge one text. Prints every finding, the judge's issues and, on a failure, its suggested rewrite.

```bash
idgate check --text "Tiga langkah, udah."
idgate check --file draft.md --kind post
idgate check --file - < caption.txt --kind caption --json
```

## gate

Check; on a failure use the judge's rewrite and check again, up to `--max-rounds` (default 2). The final text goes to **stdout**, the status line to **stderr**, so it fits in a pipe:

```bash
idgate gate --file caption.txt --kind caption > final.txt && publish final.txt
idgate gate --file approved.md --no-rewrite     # a human already approved it: check only
```

## lint

Rules only. No model, no network, instant. Exit 1 on any `block` finding.

```bash
idgate lint --file draft.md
```

## sweep

One file is one text. Frontmatter is stripped first.

```bash
idgate sweep --dir content/blog --glob "*.md"
idgate sweep --dir content/blog --lint-only --limit 20
```

## site

Every Indonesian string in a project: markdown and text files, JSON locale files, HTML (text, `alt`, `title`, `aria-label`, `placeholder`, meta descriptions), and with `--include-ts` string literals in `.ts .tsx .js .jsx .mjs`.

```bash
idgate site src/locales public --lint-only            # fast, offline, for every commit
idgate site src/locales public --include-ts           # judge in batches, report only
idgate site src/locales public --include-ts --fix     # rewrite failing batches into the files
idgate site . --plan                                  # how many batches, no judge call
idgate site . --dump units.json                       # the extracted strings, nothing else
```

How it decides what to read:

- **JSON**: a file named `id.json`, `id-ID.json`, `*.id.json`, or inside an `id/` folder is all Indonesian. In other JSON files a string counts when it reads as Indonesian, or when it is the `id` value next to an `en` value. Keys starting with `$` or `_`, URLs, paths and emails are skipped. `en.json` and `/en/` folders are skipped.
- **HTML**: text inside block elements. Inline tags stay in place as `<0>..</0>`, other markup as `{expr_a}`. `script`, `style`, `pre`, `code` and `svg` are skipped.
- **TS/TSX/JS**: string literals only. Imports, object keys, `className`, `href` and similar attributes, template strings with `${}`, and keys ending in `En` are skipped. JSX text between tags is **not** extracted; move it into a locale file or a constant.
- **Scope**: a key or tag that looks like a title, label, button or CTA is judged as a heading. Everything else is prose.
- **Register**: per file. A file with gw/lo anywhere is casual; a string with Anda or kami and no gw/lo stays formal inside it.

Batches: strings are grouped per file (per top-level section in JSON), split by register, and cut at about 6,000 characters. A string that appears in four or more files (navigation, footer) is judged once. Grouping is deterministic, so an unchanged batch hits the cache.

`--fix` maps a batch rewrite back to its strings only when the paragraph count matches and every placeholder survives. Before writing, each file is read again and the string must still be where it was. Anything that does not map is left alone and printed as `SKIPPED`.

| Flag | Default | |
| :--- | :--- | :--- |
| `--workers N` | 4 | batches judged in parallel |
| `--rounds N` | 2 | rewrite rounds per batch with `--fix` |
| `--batch-chars N` | 6000 | batch size |
| `--only REGEX` | | files whose path matches |
| `--exclude GLOB` | | skip files (repeatable), for example quotes or testimonials |
| `--report FILE` | | per-batch results and every rewrite, as JSON |
| `--warn` | | with `--lint-only`, also print `warn` findings |

## mode, guides

```bash
idgate mode     # enforce or shadow
idgate guides   # the shipped style guides and their paths
```

## Flags shared by check, gate, lint and sweep

| Flag | Meaning |
| :--- | :--- |
| `--kind` | `post` (default), `caption`, `page`, `heading`, `message`. With `heading` the whole text is a heading. With `page`, `#` lines and lines that are entirely `**bold**` are headings |
| `--register` | `casual` or `formal`. Default: casual when the text uses gw/lo |
| `--guide` | a shipped guide name or a path |
| `--rules` | another `rules.json` |
| `--skip-rule ID` | skip one rule (repeatable) |
| `--extra-check CMD` | your own checker, see [how it works](how-it-works.md#extra-checkers) |
| `--audience TEXT` | who reads it, passed to the judge |
| `--judge`, `--model`, `--judge-cmd`, `--timeout` | see [judges](judges.md) |
| `--no-cache` | ask the judge again |
| `--json` | full result as JSON |
| `--source`, `--id` | labels in the decision log |
| `--data-dir` | where cache, log and mode live (default `.idgate`) |

## Environment

| Variable | Default | |
| :--- | :--- | :--- |
| `IDGATE_JUDGE` | `claude-cli` | judge backend |
| `IDGATE_MODEL` | per backend | judge model |
| `IDGATE_JUDGE_CMD` | | command for the `command` backend |
| `IDGATE_TIMEOUT` | `240` | seconds per judge call |
| `IDGATE_DATA_DIR` | `.idgate` | cache, log, mode |
| `IDGATE_MODE` | `enforce` | `shadow` lets the original text pass and logs the verdict |
| `IDGATE_GUIDE` | by register | style guide |
| `IDGATE_RULES` | shipped | rules file |
| `IDGATE_EXTRA_CHECK` | | extra checker command |
| `IDGATE_NO_LOG` | | `1` writes no decision log |
| `CLAUDE_BIN` | `claude` on PATH | Claude Code binary |
| `ANTHROPIC_API_KEY` | | for `anthropic-api` |
| `ANTHROPIC_BASE_URL` | `https://api.anthropic.com` | for a proxy |

## Python API

```python
import idgate

r = idgate.check(text, kind="post", context={"register": "casual", "source": "blog", "item_id": "42"})
r["pass"], r["reason"], r["blocking"], r["judge"]["issues"]

g = idgate.gate(text, kind="caption", rewrite=True, max_rounds=2)
g["passed"], g["final_text"], g["rounds"], g["report"]["history"]

idgate.lint(text, kind="heading")                       # findings only
idgate.rewrite_problems(original, rewrite)              # [] when a rewrite is safe
idgate.check(text, judge_fn=my_judge)                   # bring your own judge function
```

`judge_fn(text, kind, context, lint_findings, guide_text)` returns `{natural, score, issues, rewrite}` and raises when it cannot answer.
