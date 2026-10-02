# id-voice-gate

**AI-written Indonesian reads like a translation, and readers notice. This catches it before it ships.**

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Version 1.0.0](https://img.shields.io/badge/version-1.0.0-green.svg)](CHANGELOG.md)
[![Made for Claude Code](https://img.shields.io/badge/made%20for-Claude%20Code-orange.svg)](docs/integrations.md#claude-code-agent-skill)

![Headline: "AI wrote it in English first. Readers can tell." Left, a draft post written by AI in Indonesian. "dikerjain AI" is underlined in lime and flagged as a calque of "done by AI", and "Tiga langkah, udah." is flagged as the ad punchline "Three steps, done". The footer reads FAIL, 2 lint blocks, judge natural=false, score 2. An arrow labelled idgate gate points to the right card, "What gets published": "AI yang nyusun notulen, daftar tugas, dan laporan mingguan" and "Cuma tiga langkah." with the changed words highlighted, a note that facts, numbers, names, links and negations must stay the same or the rewrite is rejected, and a footer reading PASS, 1 rewrite round, real Claude judge.](docs/hero.png)

## Why

- You let AI write your Indonesian posts, captions, landing pages or app strings. Every word is correct.
- It still reads wrong: "dikerjain AI", "Tiga langkah, udah.", "Kenalan sama X", "Satu tempat buat semuanya". The sentence was built in English, then swapped word by word.
- Readers say "kok aneh ya, kayak terjemahan". A grammar checker finds nothing, because there is no grammar mistake.
- So someone who speaks Indonesian has to read everything before it goes out. That person becomes the bottleneck.

## What it does

- **Fails the patterns that are always wrong**, instantly and offline: translated ad punchlines, calques, formal words next to gw/lo, em-dashes. 24 rules, each with a reason and a fix.
- **Asks a native-ear judge about the rest.** An LLM reads the text against a style guide and says natural or not, with a score, the exact phrases and a rewrite.
- **Rewrites safely.** `gate` uses the judge's rewrite only if every number, link, name, placeholder, quote and negation survives. Otherwise it rejects the rewrite and asks again.
- **Holds instead of guessing.** Still failing after two rounds, or the judge is down: exit code 1 or 2, and nothing gets published.
- **Covers a whole site or app.** `idgate site` finds every Indonesian string in markdown, HTML, JSON locale files and TS/TSX, judges them in batches and writes fixes back in place.
- **Casual or formal.** Ships a Jakarta casual guide (gw/lo) and a formal guide (Anda/kami), and picks one from the text.

![A terminal runs idgate against the real Claude judge. check on examples/draft-bad.md prints FAIL with two lint blocks, punch_x_udah on "Tiga langkah, udah." and calque_dikerjain_ai on "dikerjain AI", then the judge's verdict natural=False score=2, its two issues, and a suggested rewrite, exit 1. gate on the same file prints the rewritten post, "AI yang nyusun notulen, daftar tugas, dan laporan mingguan" and "Cuma tiga langkah.", and "[idgate] PASS, rounds=1", exit 0. check on examples/draft-good.md prints PASS natural, judge natural=True score=5, exit 0.](docs/demo.gif)

*Real output against the real judge (Claude Code CLI, sonnet), recorded by [docs/src/demo.py](docs/src/demo.py). The raw run is in [demo_session.json](docs/src/demo_session.json).*

## Quick start

Python 3.10 or later, no dependencies. The default judge is [Claude Code](https://claude.com/claude-code) with your own login, so there is no API key to set up.

```bash
# 1. Install
pip install git+https://github.com/BrianArfi/id-voice-gate

# 2. Lint: instant, offline
idgate lint --text "Tiga langkah, udah."

# 3. Judge one draft
idgate check --file draft.md

# 4. Gate it: rewrite if needed, print the text that is safe to publish
idgate gate --file draft.md > final.md
```

No Claude Code? Use `--judge anthropic-api` with `ANTHROPIC_API_KEY`, or `--judge command` with any model you run yourself ([judges](docs/judges.md)). Try it with no model at all:

```bash
git clone https://github.com/BrianArfi/id-voice-gate && cd id-voice-gate
python3 -m idgate site examples/site --lint-only --include-ts
python3 -m unittest discover -s tests      # 59 offline tests, fake judge
```

## Example

[`examples/draft-bad.md`](examples/draft-bad.md) is the kind of post an AI writes:

> Notulen, daftar tugas, dan laporan mingguan, dikerjain AI.
> Lo tinggal review.
>
> Rekam meeting, upload, kirim.
> Tiga langkah, udah.

`check` fails it on lint and on the judge, and exits 1:

```text
$ idgate check --file examples/draft-bad.md
FAIL  lint block: calque_dikerjain_ai, punch_x_udah (judge: natural=False score=2)
  [block] punch_x_udah  line 5: Tiga langkah, udah.
          fix: Kalimat utuh: 'Cuma tiga langkah.' atau 'Tiga langkah, terus jalan sendiri.'
  [block] calque_dikerjain_ai  line 1: dikerjain AI
          fix: Sebut pelakunya di depan: 'AI yang nyusun', 'biar AI yang beresin'.
  judge (claude-cli sonnet, 8.96s): natural=False score=2
    - "Notulen, daftar tugas, dan laporan mingguan, dikerjain AI.": Pasif terjemahan 'done by AI', pelaku hilang (panduan 6) -> AI yang nyusun notulen, daftar tugas, dan laporan mingguan.
    - "Tiga langkah, udah.": Punchline iklan terjemahan 'Three steps, done' (panduan 6) -> Cuma tiga langkah.
```

`gate` takes the rewrite, checks that nothing but wording changed, judges it again and prints what is safe to publish:

```text
$ idgate gate --file examples/draft-bad.md
AI yang nyusun notulen, daftar tugas, dan laporan mingguan.
Lo tinggal review.

Rekam meeting, upload, kirim.
Cuma tiga langkah.
[idgate] PASS, rounds=1, changed=True, natural
```

A post a person wrote by hand, [`examples/draft-good.md`](examples/draft-good.md), passes as it is:

```text
$ idgate check --file examples/draft-good.md
PASS  natural
  judge (claude-cli sonnet, 7.77s): natural=True score=5
```

### On a whole site

```text
$ idgate site examples/site --lint-only --include-ts
BLOCK  examples/site/locales/id.json:3  [kenalan_sama]  hero.title
       "Kenalan sama Notula"
BLOCK  examples/site/locales/id.json:4  [satu_tempat_buat]  hero.subtitle
       "Satu tempat buat semua catatan meeting lo."
...
[idgate] 17 Indonesian strings checked: 7 block, 1 warn (lint only)
```

Without `--lint-only` the strings are judged in batches, and `--fix` writes the accepted rewrites back into `id.json`, the HTML and the TSX, keeping every placeholder and inline tag in place.

---

## Documentation

- [How it works](docs/how-it-works.md): lint, judge, safe rewrite, fail-closed, shadow mode, cache, extra checkers
- [Commands, flags and exit codes](docs/commands.md): `check`, `gate`, `lint`, `sweep`, `site`, environment, Python API
- [Judges](docs/judges.md): Claude Code CLI, Anthropic API, any command, lint only
- [Rules and style guides](docs/rules.md): all 24 rules, the two guides, how to add a rule
- [Integrations](docs/integrations.md): Claude Code skill, pre-commit, GitHub Actions, publish scripts
- [SKILL.md](SKILL.md): the rules an AI agent follows when it uses this tool
- [Skill page with examples](https://brianarfi.com/skills/id-voice-gate?utm_source=github&utm_medium=readme&utm_campaign=ai-skills)

## FAQ

**Is this a grammar checker?**
No. It looks for Indonesian that was thought in English: calques, translated slogans, a formal word in a casual sentence. Spelling and grammar are a different tool.

**Does my text go anywhere?**
Only to the judge you pick: Claude through your own Claude Code login, the Anthropic API with your own key, or your own command. Lint runs locally. The decision log keeps a hash of the text, not the text.

**What happens when the judge is down?**
The text fails with exit code 2 and nothing is published. If an outage blocks your whole pipeline, switch to shadow mode (`echo shadow > .idgate/mode`): the original text passes and every verdict is still logged.

**Can the rewrite change my meaning?**
The gate rejects any rewrite that changes a number, date, link, hashtag, mention, emoji, placeholder, quote, product name, paragraph count or the number of negations. It does not check that your facts are true; it checks that they did not change.

**Does it only work for casual gw/lo writing?**
No. It picks the formal guide (Anda/kami) when the text has no gw/lo, and `--register` or `--guide` override that. You can also write your own guide.

**Can I use it for a language other than Indonesian?**
The engine does not care, but the rules and guides are Indonesian. Another language needs its own guide and rules file.

## Changelog

The full history is in [CHANGELOG.md](CHANGELOG.md). Latest: **[1.0.0] - 2026-10-02**, the first public release. `idgate --version` prints the installed version.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

More AI skills: https://brianarfi.com/skills
