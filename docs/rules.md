# Rules and style guides

[Back to the README](../README.md)

Two files carry the language knowledge, and neither is code:

- **Style guides** in `idgate/guides/` ([jakarta-casual](../idgate/guides/jakarta-casual.md), [formal](../idgate/guides/formal.md)). The judge reads the whole guide on every call. Change the guide to change what "natural" means. Written in Indonesian, for Indonesian writers.
- **Rules** in [`idgate/rules.json`](../idgate/rules.json). Regexes for the patterns that are always wrong, derived from the guide, so they fail the text even when the judge is lenient.

## The shipped guides

| Guide | For | Pronouns |
| :--- | :--- | :--- |
| [`jakarta-casual`](../idgate/guides/jakarta-casual.md) | social posts, captions, landing pages, chat | gw/lo |
| [`formal`](../idgate/guides/formal.md) | legal pages, client email, product docs, app UI | Anda/kami |

Both open with the same diagnosis (a sentence thought in English, swapped word by word), then cover register, sentence rhythm, loanwords, the banned translation patterns, punctuation, headings and buttons, what a rewrite may never change, and before/after examples. Use your own with `--guide path/to/guide.md`; keep it in the same shape so the judge has examples to imitate.

## The rules

| Rule | Severity | Catches | Example |
| :--- | :--- | :--- | :--- |
| `dash_em_en` | block | em-dash and en-dash | `nakutin` + U+2014 + `gw pernah` |
| `dash_double` | block | ` -- ` as a typed dash | `3 hari lagi -- satu sesi` |
| `punch_x_udah` | block | "Three steps, done" | `Tiga langkah, udah.` |
| `punch_beres_alone` | block | "X. Done." | `Upload file. Beres.` |
| `punch_gak_ribet` | block | "No hassle." | `Gak ribet.` |
| `punch_itu_aja` | block in headings, warn in prose | "That's it." | `Itu aja.` |
| `heading_slogan` | block | one-word slogan headings | `Simpel.` |
| `kenalan_sama` | block in headings, warn in prose | "Meet X" | `Kenalan sama Notula` |
| `bukan_cuma_tapi` | warn | "Not just X but Y" | `Bukan cuma teori, tapi praktik` |
| `satu_tempat_buat` | block | "One place for everything" | `Satu tempat buat semua kerjaan lo` |
| `yang_lo_butuhin` | block in headings, warn in prose | "What you need" | `Yang lo butuhin` |
| `heading_kenapa_x` | block | "Why X?" as a section heading | `Kenapa Notula?` |
| `heading_fragment` | warn | verbless "X, Y." heading | `Prototype, hari yang sama.` |
| `formal_keras` | block, casual only | stiff formal words next to gw/lo | `tersebut`, `merupakan`, `bagi para` |
| `formal_ringan` | warn, casual only | softer formal words next to gw/lo | `melakukan`, `sedang`, `menggunakan` |
| `calque_nempel` | block | "pinned to" | `nempel di bagian yang dimaksud` |
| `calque_dikerjain_ai` | block | "done by AI" | `Laporan mingguan dikerjain AI` |
| `calque_di_mana` | warn | "where" as a connector | `platform di mana pengguna...` |
| `label_colon` | block | slide labels in prose | `Hasilnya: satu file rapi` |
| `imperatif_formal` | block | template imperatives and buttons | `Bayangkan...`, `Pelajari lebih lanjut` |
| `opener_klise` | warn | cliché openers and closers | `Di era digital...`, `Yuk simak` |
| `serapan_hindari` | warn | loanwords that read translated | `seamless`, `unlock`, `journey` |
| `register_campur` | warn, casual only | kamu or Anda mixed into gw/lo | `Gw bantu kamu biar lo bisa` |
| `lo_density` | warn, casual only | "lo" in almost every sentence | `Lo buka. Lo baca. Lo tulis.` |

Each rule in the file carries its own `why`, `fix`, `example_bad` and `example_good`. The tests check that every `example_bad` triggers its own rule.

## Adding or changing a rule

1. **Put the pattern in the guide first.** The rules are derived from the guide, never the other way round.
2. **Add an object to `rules.json`**: `id`, `pattern` (a Python regex, matched per line), `flags` (`i` for case-insensitive), `scope` (`heading`, `prose` or `any`), `severity` (`block`, `warn`, or a dict per scope), `requires: "casual"` when it is only wrong next to gw/lo, plus `why`, `fix`, `example_bad`, `example_good`. Write dashes as `\\u2014`, never as the character.
3. **Calibrate on human writing.** Run the rule over text that native speakers wrote by hand, for example your own old posts:
   ```bash
   idgate sweep --dir my-hand-written-posts --glob "*.txt" --lint-only
   ```
   A `block` rule should fire on almost none of them. If it fires on natural human writing, make it a `warn` or narrow the regex. (The shipped rules were calibrated this way against about 1,800 hand-written posts: `melakukan` appeared in 28 of them, which is why it is a `warn`.)
4. **Add the bad example to `BAD` in [`tests/test_idgate.py`](../tests/test_idgate.py)** and run the tests.

To ship your own rules without forking, point `--rules` or `IDGATE_RULES` at your file.
