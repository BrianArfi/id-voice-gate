# Changelog

All notable changes to id-voice-gate are recorded here.
The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/).

## [1.0.0] - 2026-10-02

The first public release.

### Added
- `idgate check`: lint plus an LLM judge for one Indonesian text. Prints every finding, the judge's issues and a suggested rewrite.
- `idgate gate`: rewrites a failing text with the judge's rewrite and checks again, up to two rounds. Prints the final text on stdout and the status on stderr.
- `idgate lint` and `idgate sweep`: rules only, or one check per file in a folder.
- `idgate site`: finds every Indonesian string in markdown, text, HTML (text, `alt`, `title`, `aria-label`, `placeholder`, meta descriptions), JSON locale files and, with `--include-ts`, TS/TSX/JS string literals. Judges them in deterministic batches with `--workers`, and `--fix` writes accepted rewrites back in place.
- 24 deterministic rules in `idgate/rules.json`, `block` or `warn`, per heading or prose, some only for casual gw/lo text.
- Two style guides: `jakarta-casual` (gw/lo) and `formal` (Anda/kami), picked automatically from the text.
- Four judge backends: `claude-cli` (default, Claude Code with your own login), `anthropic-api` (plain urllib, no SDK), `command` (any shell command) and `none` (lint only, labelled as such).
- Fail-closed: a missing or logged-out judge, an API error, a broken answer or a missing guide fails the text with exit code 2.
- Rewrite guard: a rewrite that changes a number, URL, hashtag, mention, emoji, placeholder, quote, name, paragraph count or negation count is rejected and the judge is asked again.
- Shadow mode (`IDGATE_MODE=shadow` or `.idgate/mode`) for judge outages: verdicts are logged, the original text passes.
- Verdict cache keyed on text, guide version, kind, register, backend and model, and a decision log that stores a hash of the text, not the text.
- Extra checkers: register a Python function, or pass `--extra-check` with any command.
- `SKILL.md` for Claude Code and other agent harnesses, a pre-commit hook, a GitHub Actions example, and 59 offline tests with a fake judge.

[1.0.0]: https://github.com/BrianArfi/id-voice-gate/releases/tag/v1.0.0
