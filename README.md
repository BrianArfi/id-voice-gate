# id-voice-gate

**Your AI writes Indonesian that reads like a translation. This catches it, rewrites it safely, and holds what it cannot fix, before anyone reads it.**

For Indonesian creators, marketers, PMs and developers who publish AI-written Indonesian: posts, captions, landing pages, app strings. Runs as a CLI or as a Claude Code skill.

[![License: Apache-2.0](https://img.shields.io/badge/license-Apache--2.0-blue.svg)](LICENSE)
[![Version 1.0.0](https://img.shields.io/badge/version-1.0.0-green.svg)](CHANGELOG.md)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue.svg)](pyproject.toml)
[![Made for Claude Code](https://img.shields.io/badge/made%20for-Claude%20Code-orange.svg)](docs/integrations.md#claude-code-agent-skill)

![Animated illustration. Headline: "AI wrote it in English first. Readers can tell." A draft post written by AI in Indonesian slides in. A lime underline draws under "dikerjain AI" and a tag pops up: calque, "done by AI". A second underline draws under "Tiga langkah, udah." with the tag: ad punchline, "Three steps, done". The footer reads FAIL, 2 lint blocks, judge natural=false, score 2. An arrow labelled idgate gate appears, and the "What gets published" card slides in: "AI yang nyusun notulen, daftar tugas, dan laporan mingguan" and "Cuma tiga langkah." light up as the changed words, a note says facts, numbers, names, links and negations must stay the same or the rewrite is rejected, and the footer reads PASS, 1 rewrite round, real Claude judge.](docs/hero.gif)

*Illustration of the real run shown in [See it run](#see-it-run).*

## The problem

Dina runs marketing for a small Jakarta startup. She asks her AI for an Instagram caption about the new meeting-notes feature and gets this:

> Notulen, daftar tugas, dan laporan mingguan, dikerjain AI.
> Lo tinggal review.
>
> Rekam meeting, upload, kirim.
> Tiga langkah, udah.

Every word is Indonesian and the grammar is fine. But "dikerjain AI" is "done by AI" swapped word by word, and "Tiga langkah, udah." is the English ad line "Three steps, done." A native reader hears the English underneath. The first comment is "kok kayak terjemahan ya" ("why does this sound like a translation?").

- **A grammar checker finds nothing**, because there is no grammar mistake. The sentence was built in English, then translated.
- **The patterns repeat.** "Kenalan sama X" (Meet X), "Satu tempat buat semua..." (One place for everything), "Pelajari lebih lanjut" (Learn more). Readers learn to spot them fast.
- **Someone has to read everything.** The one native speaker on the team checks every caption, page and app string by hand, and becomes the bottleneck.
- **Asking the AI to "make it sound natural" is a gamble.** It may change a price, drop a link, or turn "bukan" (not) into its opposite while it smooths the wording.
- **One slip ships to every user.** A translated button label in `id.json` sits on every screen until somebody notices.

## Who it is for

**Good fit if you...**

- publish Indonesian that an AI wrote or drafted: social posts, captions, newsletters, landing pages, docs
- ship an app or site with Indonesian strings in JSON locale files, HTML, markdown or TS/TSX
- write casual gw/lo copy or formal Anda/kami copy, and want each held to its own style guide
- want a hard gate in a script, a pre-commit hook or CI, with clear exit codes, not a suggestion box
- use Claude Code and want the agent to check Indonesian before it hands it to you

**Not for you if...**

- you need a spelling or grammar checker. This looks for translated phrasing, not typos.
- you need fact checking. It keeps your facts unchanged; it does not check that they are true.
- you write in another language. The engine is generic, but the 24 rules and both guides are Indonesian, and none ship for other languages.
- you want an editor that sets tone and taste. It catches what makes readers say "kayak terjemahan". The voice is still yours.

## Before and after

Real lines from the rules file. Each rule in [`idgate/rules.json`](idgate/rules.json) ships with a bad example and a fix; the tests check that every bad example triggers its rule. English glosses in italics.

| Before: what the AI wrote | After: reads like a person | Rule that caught it |
| :--- | :--- | :--- |
| Laporan mingguan dikerjain AI.<br>*"Weekly report, done by AI."* | AI yang nyusun laporan mingguan.<br>*"AI drafts the weekly report."* | `calque_dikerjain_ai` |
| Tiga langkah, udah.<br>*"Three steps, done."* | Cuma tiga langkah.<br>*"Just three steps."* | `punch_x_udah` |
| Upload file. Beres.<br>*"Upload the file. Done."* | Upload file-nya, laporannya jadi sendiri.<br>*"Upload the file and the report builds itself."* | `punch_beres_alone` |
| Kenalan sama Notula<br>*"Meet Notula" (as a heading)* | Begini cara kerja Notula<br>*"Here is how Notula works"* | `kenalan_sama` |
| Pelajari lebih lanjut<br>*"Learn more" (a button)* | Lihat harganya<br>*"See the price"* | `imperatif_formal` |
| Gw bantu kamu biar lo bisa.<br>*"I help you (kamu) so you (lo) can." Two words for "you".* | Gw bantu biar lo bisa.<br>*"I help so you can." One register.* | `register_campur` |

![Animated illustration titled "Same point. Reads like a person wrote it." Four rows, each with a rule name on the left: calque_dikerjain_ai, punch_x_udah, kenalan_sama, imperatif_formal. First the AI-written lines appear with an English gloss, and a red underline draws under the translated part of each: "dikerjain AI.", "Tiga langkah, udah.", "Kenalan sama", "Bayangkan". Then a lime bar wipes across each row, left to right, and leaves the fixed line with the new words highlighted: "AI yang nyusun laporan mingguan.", "Cuma tiga langkah.", "Begini cara kerja Notula", "Bayangin laporan lo jadi sendiri."](docs/before-after.gif)

*Illustration. The "after" lines are the fixes each rule ships with. In a real run the judge writes the rewrite for your exact text, and the gate checks it before using it. All 24 rules are listed in [docs/rules.md](docs/rules.md).*

## How it works

1. **Lint.** 24 regex rules, matched line by line, offline and instant. A `block` rule fails the text no matter what the judge says; a `warn` is passed to the judge as a hint. Rules know headings from prose, and some only fire next to casual gw/lo.
2. **Judge.** An LLM with a native ear reads the text against a full style guide (Jakarta casual or formal, picked from the text) and returns natural or not, a score from 1 to 5, the exact phrases, and a rewrite. Pass means no lint block, natural, and a score of 4 or 5.
3. **Rewrite** (`gate` only). The judge's rewrite is used only if nothing but wording changed: every number, date, price, link, hashtag, mention, emoji, placeholder, quote, name, paragraph and negation must survive. Otherwise it is rejected and the judge is asked again, with the reason.
4. **Re-check.** The rewrite is judged again, at most two rounds. Still failing: exit 1, hold. Judge down, logged out, or a broken answer: exit 2, hold. It never passes text because the judge was unavailable.

![Animated illustration titled "Lint, judge, rewrite, re-check. No guessing." Four boxes draw in left to right with arrows between them: 1 Lint, 24 rules, offline, a block fails the text; 2 Judge, LLM plus style guide, natural and score 4+; 3 Rewrite, uses the judge's rewrite if it is safe; 4 Re-check, the rewrite is judged again. A dot travels through the boxes, lighting each one lime. A bracket under boxes 2 to 4 reads "still failing? next round, at most 2". Then a dark panel, "Rewrite guard: these must not change", fills with chips: numbers, dates, prices; names, products; links, #tags, @mentions; emoji; placeholders; quotes, paragraphs; negations. An example appears: original "AI bukan pengganti PM", rewrite "AI itu pengganti PM" struck through, a red REJECTED stamp, and the reason "negation count changed (1 -> 0): the meaning may reverse". Last, three outcomes: PASS, publish the text gate printed, exit 0; HOLD, still failing after 2 rounds, exit 1; HOLD, judge down or broken answer, exit 2, fail-closed.](docs/how-it-works.gif)

*Illustration. The rejected example and its reason are the ones in [docs/how-it-works.md](docs/how-it-works.md) and the message the gate prints. Details, shadow mode, cache and extra checkers: [How it works](docs/how-it-works.md).*

## See it run

![A terminal runs idgate against the real Claude judge. check on examples/draft-bad.md prints FAIL with two lint blocks, punch_x_udah on "Tiga langkah, udah." and calque_dikerjain_ai on "dikerjain AI", then the judge's verdict natural=False score=2, its two issues, and a suggested rewrite, exit 1. gate on the same file prints the rewritten post, "AI yang nyusun notulen, daftar tugas, dan laporan mingguan" and "Cuma tiga langkah.", and "[idgate] PASS, rounds=1", exit 0. check on examples/draft-good.md prints PASS natural, judge natural=True score=5, exit 0.](docs/demo.gif)

*Real output against the real judge (Claude Code CLI, sonnet), recorded by [docs/src/demo.py](docs/src/demo.py). The raw run is in [demo_session.json](docs/src/demo_session.json).*

![A terminal runs "idgate site examples/site --lint-only --include-ts" on the bundled sample site. It prints seven BLOCK lines, each with the file, line, rule and string key: id.json line 3 kenalan_sama on "Kenalan sama Notula", line 4 satu_tempat_buat on "Satu tempat buat semua catatan meeting lo.", line 5 imperatif_formal on the button "Pelajari lebih lanjut", line 11 calque_dikerjain_ai, line 12 punch_x_udah, and in Pricing.tsx punch_gak_ribet on a title attribute "Gak ribet." and imperatif_formal on an aria-label "Mulai sekarang juga". Each has the fix from the rule. It ends with "[idgate] 17 Indonesian strings checked: 7 block, 1 warn (lint only)" and exit 1.](docs/demo-site.gif)

*Real output of site mode over [examples/site](examples/site), lint only, no model, recorded by [docs/src/demo_site.py](docs/src/demo_site.py). It reaches strings inside JSON, HTML and TSX attributes.*

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

As a Claude Code skill: `git clone https://github.com/BrianArfi/id-voice-gate ~/.claude/skills/id-voice-gate`, then ask in plain words, for example "cek caption ini pakai id-voice-gate sebelum dipost". See [integrations](docs/integrations.md) for pre-commit, GitHub Actions and publish scripts.

## Example

[`examples/draft-bad.md`](examples/draft-bad.md) is Dina's caption from above. `check` fails it on lint and on the judge, and exits 1:

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

**Do I need an API key?**
No, by default. The judge is the `claude` CLI with your own Claude Code login. You can switch to the Anthropic API with your own key, to any command you run, or to lint only with no model at all ([judges](docs/judges.md)).

**Does my text go anywhere?**
Only to the judge you pick: Claude through your own Claude Code login, the Anthropic API with your own key, or your own command. Lint runs locally. The decision log keeps a hash of the text, not the text.

**What happens when the judge is down?**
The text fails with exit code 2 and nothing is published. If an outage blocks your whole pipeline, switch to shadow mode (`echo shadow > .idgate/mode`): the original text passes and every verdict is still logged.

**Can the rewrite change my meaning?**
The gate rejects any rewrite that changes a number, date, link, hashtag, mention, emoji, placeholder, quote, product name, paragraph count or the number of negations. It does not check that your facts are true; it checks that they did not change.

**Does it only work for casual gw/lo writing?**
No. It picks the formal guide (Anda/kami) when the text has no gw/lo, and `--register` or `--guide` override that. You can also write your own guide.

**Will it flag my own hand-written posts?**
It should not, and a hand-written post in the examples passes. The `block` rules were calibrated against about 1,800 hand-written posts, and a rule that fired on natural writing was made a `warn` instead ([how](docs/rules.md#adding-or-changing-a-rule)).

**Can I add my own rules?**
Yes. Add a regex to a rules file and point `--rules` or `IDGATE_RULES` at it, or register a Python checker for things a regex cannot express, like a banned competitor name ([rules](docs/rules.md), [extra checkers](docs/how-it-works.md#extra-checkers)).

**Can I use it for a language other than Indonesian?**
The engine does not care, but the rules and guides are Indonesian. Another language needs its own guide and rules file.

## Changelog

The full history is in [CHANGELOG.md](CHANGELOG.md). Latest: **[1.0.0] - 2026-10-02**, the first public release. `idgate --version` prints the installed version.

## License

Apache-2.0. See [LICENSE](LICENSE) and [NOTICE](NOTICE).

More AI skills: [BrianArfi.com/skills](https://BrianArfi.com/skills)
