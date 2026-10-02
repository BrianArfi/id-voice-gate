"""Command line: idgate check | lint | gate | sweep | site | mode | guides.

Exit codes: 0 pass, 1 fail, 2 judge unavailable (fail-closed: hold the text).
"""
import argparse
import fnmatch
import json
import os
import re
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed

from . import __version__, core, judges, site


def _read_input(a):
    if a.text is not None:
        return a.text
    if a.file == "-":
        return sys.stdin.read()
    with open(a.file, encoding="utf-8") as fh:
        return fh.read()


def _apply_settings(a):
    s = core.SETTINGS
    if getattr(a, "judge", None):
        s.backend = a.judge
    if getattr(a, "model", None):
        s.model = a.model
    if getattr(a, "judge_cmd", None):
        s.judge_cmd = a.judge_cmd
    if getattr(a, "extra_check", None):
        s.extra_cmd = a.extra_check
    if getattr(a, "rules", None):
        s.rules_path = a.rules
    if getattr(a, "guide", None):
        s.guide = a.guide
    if getattr(a, "timeout", None):
        s.timeout = a.timeout
    if getattr(a, "data_dir", None):
        s.data_dir = a.data_dir


def _context(a):
    ctx = {"source": getattr(a, "source", None) or "cli"}
    if getattr(a, "id", None):
        ctx["item_id"] = a.id
    if getattr(a, "register", None):
        ctx["register"] = a.register
    if getattr(a, "skip_rule", None):
        ctx["skip_rules"] = list(a.skip_rule)
    if getattr(a, "audience", None):
        ctx["audience"] = a.audience
    return ctx


def _lint_only(a):
    return getattr(a, "lint_only", False) or core.SETTINGS.backend == "none"


def _exit_code(passed, judge_available, lint_only=False):
    if passed:
        return 0
    if not lint_only and judge_available is False:
        return 2
    return 1


def _judge_label(j):
    if j.get("cached"):
        return "%s %s, cached" % (j.get("backend"), j.get("model"))
    return "%s %s, %ss" % (j.get("backend"), j.get("model"), j.get("latency_s"))


def _print_check(res, as_json, show_rewrite=True):
    if as_json:
        print(json.dumps(res, ensure_ascii=False, indent=2))
        return
    print("%s  %s" % ("PASS" if res["pass"] else "FAIL", res["reason"]))
    for f in res["lint"]:
        loc = "line %s" % f["line"] if f.get("line") else "text"
        print("  [%s] %s  %s: %s" % (f["severity"], f["rule"], loc, f.get("match") or f.get("detail", "")))
        if f["severity"] == "block" and f.get("fix"):
            print("          fix: %s" % f["fix"])
    j = res["judge"]
    if j.get("status") == "ok":
        print("  judge (%s): natural=%s score=%s" % (_judge_label(j), j["natural"], j["score"]))
        for it in j.get("issues") or []:
            print('    - "%s": %s -> %s' % (it["quote"], it["why"], it["fix"]))
        if show_rewrite and not res["pass"] and j.get("rewrite"):
            print("  suggested rewrite:")
            for line in j["rewrite"].splitlines():
                print("    " + line)
    elif j.get("status") == "skipped":
        print("  judge: skipped (lint only: an audit, not a pass to publish)")
    else:
        print("  judge: %s (%s) %s" % (j.get("status"), j.get("backend"), j.get("error", "")))


def _cmd_check(a):
    _apply_settings(a)
    text = _read_input(a)
    lint_only = _lint_only(a)
    res = core.check(text, a.kind, _context(a), judge=not lint_only, use_cache=not a.no_cache)
    _print_check(res, a.json)
    return _exit_code(res["pass"], res["judge_available"], lint_only)


def _cmd_lint(a):
    _apply_settings(a)
    text = _read_input(a)
    ctx = _context(a)
    f = core.lint(text, a.kind, ctx) + core.run_extra_checks(text, a.kind, ctx)
    if a.json:
        print(json.dumps(f, ensure_ascii=False, indent=2))
    else:
        for x in f:
            print("[%s] %s  line %s: %s" % (x["severity"], x["rule"], x.get("line"), x.get("match") or x.get("detail", "")))
        n = len(core.blocking(f))
        print("%s  %d block, %d warn" % ("FAIL" if n else "PASS", n, len(f) - n))
    return 1 if core.blocking(f) else 0


def _cmd_gate(a):
    _apply_settings(a)
    text = _read_input(a)
    lint_only = _lint_only(a)
    if lint_only:
        res = core.check(text, a.kind, _context(a), judge=False)
        sys.stdout.write(text if text.endswith("\n") else text + "\n")
        sys.stderr.write("[idgate] %s, lint only (no judge, no rewrite), %s\n" % (
            "PASS" if res["pass"] else "FAIL", res["reason"]))
        return _exit_code(res["pass"], None, True)
    g = core.gate(text, a.kind, _context(a), rewrite=not a.no_rewrite, max_rounds=a.max_rounds,
                  use_cache=not a.no_cache)
    if a.json:
        print(json.dumps(g, ensure_ascii=False, indent=2))
    else:
        sys.stdout.write(g["final_text"] if g["final_text"].endswith("\n") else g["final_text"] + "\n")
        sys.stdout.flush()
        sys.stderr.write("[idgate] %s, rounds=%d, changed=%s, %s\n" % (
            "PASS" if g["passed"] else "FAIL (hold it, do not publish)", g["rounds"],
            g["report"]["changed"], g["report"]["reason"]))
    return _exit_code(g["passed"], g["report"]["judge_available"])


def _cmd_sweep(a):
    _apply_settings(a)
    files = []
    for root, dirs, names in os.walk(a.dir):
        dirs[:] = sorted(d for d in dirs if not d.startswith(".") and d not in site.SKIP_DIRS)
        for n in names:
            if fnmatch.fnmatch(n, a.glob):
                files.append(os.path.join(root, n))
    files.sort()
    if a.limit:
        files = files[: a.limit]
    lint_only = _lint_only(a)
    n_fail = n_unavail = 0
    rows = []
    for p in files:
        try:
            with open(p, encoding="utf-8") as fh:
                body = core.strip_frontmatter(fh.read())
        except (OSError, UnicodeDecodeError) as e:
            print("SKIP  %s (%s)" % (p, e))
            continue
        ctx = dict(_context(a), item_id=os.path.relpath(p, a.dir), source=a.source or "sweep")
        res = core.check(body, a.kind, ctx, judge=not lint_only, use_cache=not a.no_cache)
        if not res["pass"]:
            if res["judge_available"] is False and not res["blocking"]:
                n_unavail += 1
            else:
                n_fail += 1
        j = res["judge"]
        js = "score=%s" % j.get("score") if j.get("status") == "ok" else j.get("status")
        rows.append({"file": p, "pass": res["pass"], "blocking": res["blocking"], "judge": js,
                     "reason": res["reason"]})
        if not a.json:
            print("%s  %-56s %-9s block=%s" % ("PASS" if res["pass"] else "FAIL", os.path.relpath(p, a.dir)[:56],
                                              js, ",".join(res["blocking"]) or "-"))
    if a.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
    else:
        print("\n%d files, %d failed, %d judge unavailable%s" % (
            len(rows), n_fail, n_unavail, " (lint only)" if lint_only else ""))
    if n_unavail:
        return 2
    return 1 if n_fail else 0


# ─────────────────────────── site ───────────────────────────

def _site_lint(units, show_warn, as_json):
    blocks = warns = 0
    out = []
    for u in units:
        ctx = {"register": u["register"]}
        kind = "heading" if u["scope"] == "heading" else "page"
        for f in core.lint(u["text"], kind, ctx):
            if f["severity"] == "block":
                blocks += 1
            else:
                warns += 1
            out.append({"file": u["file"], "line": u["line"], "key": u["key"], "text": u["text"], **f})
            if as_json or (f["severity"] != "block" and not show_warn):
                continue
            print("%s  %s:%s  [%s]  %s" % ("BLOCK" if f["severity"] == "block" else "warn ", u["file"], u["line"],
                                          f["rule"], u["key"]))
            print('       "%s"' % u["text"][:160])
            if f["severity"] == "block":
                print('       hit: "%s". %s' % (f["match"], f["fix"]))
    if as_json:
        print(json.dumps(out, ensure_ascii=False, indent=1))
    else:
        print("[idgate] %d Indonesian strings checked: %d block, %d warn (lint only)" % (len(units), blocks, warns))
    return 1 if blocks else 0


def _run_batch(b, fix, rounds, use_cache):
    ctx = {"source": "site", "item_id": b["id"], "register": b["register"]}
    t0 = time.time()
    if fix:
        g = core.gate(b["text"], kind="page", context=ctx, rewrite=True, max_rounds=rounds, use_cache=use_cache)
        rep = g["report"]
        last = (rep.get("history") or [{}])[-1]
        res = {"passed": g["passed"], "reason": rep["reason"], "rounds": g["rounds"],
               "judge_available": rep.get("judge_available"), "final_text": g["final_text"],
               "changed": g["final_text"] != b["text"], "score": (last.get("judge") or {}).get("score"),
               "blocking": last.get("blocking"), "issues": (last.get("judge") or {}).get("issues")}
    else:
        r = core.check(b["text"], kind="page", context=ctx, use_cache=use_cache)
        j = r.get("judge") or {}
        res = {"passed": r["pass"], "reason": r["reason"], "rounds": 0, "judge_available": r.get("judge_available"),
               "final_text": b["text"], "changed": False, "score": j.get("score"), "blocking": r.get("blocking"),
               "issues": j.get("issues"), "rewrite": j.get("rewrite")}
    res["secs"] = round(time.time() - t0, 1)
    return res


def _cmd_site(a):
    _apply_settings(a)
    files = site.walk(a.paths, include_ts=a.include_ts, exclude=a.exclude or ())
    units = site.extract(files, include_ts=a.include_ts)
    if a.only:
        units = [u for u in units if re.search(a.only, u["file"])]
    if a.dump:
        slim = [{k: v for k, v in u.items() if k != "parts"} for u in units]
        with open(a.dump, "w", encoding="utf-8") as fh:
            json.dump({"units": slim}, fh, ensure_ascii=False, indent=1)
        print("[idgate] %d units from %d files written to %s" % (len(units), len(files), a.dump))
        return 0
    if _lint_only(a):
        return _site_lint(units, a.warn, a.json)

    batches = site.build_batches(units, a.batch_chars)
    print("[idgate] %d files, %d Indonesian strings, %d batches, %d characters" % (
        len(files), len(units), len(batches), sum(len(b["text"]) for b in batches)), flush=True)
    if a.plan or not batches:
        return 0
    lock = threading.Lock()
    rows, rewrites = [], []
    done = [0]

    def work(b):
        try:
            return b, _run_batch(b, a.fix, a.rounds, not a.no_cache)
        except Exception as e:  # noqa: BLE001  fail-closed
            return b, {"passed": False, "reason": "error: %s: %s" % (type(e).__name__, e),
                       "judge_available": False, "final_text": b["text"], "changed": False}

    with ThreadPoolExecutor(max_workers=max(1, a.workers)) as ex:
        futs = [ex.submit(work, b) for b in batches]
        for fut in as_completed(futs):
            b, res = fut.result()
            row = {k: res.get(k) for k in ("passed", "reason", "rounds", "score", "judge_available", "secs",
                                            "blocking", "issues")}
            row.update(id=b["id"], group=b["group"], chars=len(b["text"]), unresolved=[])
            if res.get("changed"):
                parts, why = site.split_back(b, res["final_text"])
                if parts is None:
                    row["unresolved"] = [it["text"][:200] for it in b["items"]]
                    row["reason"] += " | cannot map back: " + why
                else:
                    for it, new in zip(b["items"], parts):
                        if new is None:
                            row["unresolved"].append(it["text"][:200])
                            continue
                        if site._norm(new) == site._norm(it["text"]):
                            continue
                        for u in it["units"]:
                            rewrites.append({"id": u["id"], "file": u["file"], "from": u["judgeText"], "to": new,
                                             "batch": b["id"], "batch_passed": res["passed"]})
            with lock:
                rows.append(row)
                done[0] += 1
                st = "PASS" if res["passed"] else ("JUDGE-DOWN" if res.get("judge_available") is False else "FAIL")
                print("[%d/%d] %-10s %-50s score=%s rounds=%s %ss %s" % (
                    done[0], len(batches), st, b["id"][:50], res.get("score"), res.get("rounds"), res.get("secs"),
                    "" if res["passed"] else res["reason"][:120]), flush=True)
                if not res["passed"] and not a.fix:
                    for it in res.get("issues") or []:
                        print('         - "%s": %s -> %s' % (it["quote"][:80], it["why"], it["fix"][:80]))

    applied, skipped = [], []
    if a.fix and rewrites:
        applied, skipped = site.apply_rewrites(rewrites, include_ts=a.include_ts)
        for s in skipped:
            print("  SKIPPED %s: %s" % (s["id"], s["why"]))
    if a.report:
        with open(a.report, "w", encoding="utf-8") as fh:
            json.dump({"batches": rows, "rewrites": rewrites, "applied": applied, "skipped": skipped},
                      fh, ensure_ascii=False, indent=1)
    n_fail = sum(1 for r in rows if not r["passed"])
    n_dead = sum(1 for r in rows if not r["passed"] and r.get("judge_available") is False)
    print("[idgate] %d batches: %d passed, %d failed, %d judge unavailable%s" % (
        len(rows), len(rows) - n_fail, n_fail - n_dead, n_dead,
        ", %d strings rewritten in place, %d skipped" % (len(applied), len(skipped)) if a.fix else ""), flush=True)
    if n_dead:
        return 2
    return 1 if n_fail else 0


def _cmd_mode(a):
    _apply_settings(a)
    print(core.mode())
    return 0


def _cmd_guides(a):
    for g in core.list_guides():
        print("%-16s %s" % (g, os.path.join(core.GUIDES_DIR, g + ".md")))
    return 0


def build_parser():
    ap = argparse.ArgumentParser(
        prog="idgate",
        description="id-voice-gate: catch Indonesian text that reads like a translation, before it ships.",
        epilog="Exit codes: 0 pass, 1 fail, 2 judge unavailable (hold the text).")
    ap.add_argument("--version", action="version", version="idgate " + __version__)
    sub = ap.add_subparsers(dest="cmd", required=True)

    def judge_opts(p):
        p.add_argument("--judge", choices=judges.BACKENDS, help="judge backend (default claude-cli, env IDGATE_JUDGE)")
        p.add_argument("--model", help="judge model (default: sonnet for claude-cli, claude-sonnet-5 for anthropic-api)")
        p.add_argument("--judge-cmd", help="shell command for --judge command: prompt on stdin, JSON on stdout")
        p.add_argument("--timeout", type=int, help="judge timeout in seconds (default 240)")
        p.add_argument("--no-cache", action="store_true", help="ask the judge again even when a verdict is cached")

    def text_opts(p, needs_input=True):
        if needs_input:
            g = p.add_mutually_exclusive_group(required=True)
            g.add_argument("--file", help="file to read, or - for stdin")
            g.add_argument("--text", help="text to check")
        p.add_argument("--kind", default="post", choices=core.KINDS)
        p.add_argument("--register", choices=core.REGISTERS, help="default: casual when the text uses gw/lo, else formal")
        p.add_argument("--guide", help="style guide: a shipped name (jakarta-casual, formal) or a path")
        p.add_argument("--rules", help="rules.json to use instead of the shipped one")
        p.add_argument("--skip-rule", action="append", help="rule id to skip (repeatable)")
        p.add_argument("--extra-check", help="shell command: JSON {text, kind, register} on stdin, JSON list of findings out")
        p.add_argument("--audience", help="who reads it, passed to the judge")
        p.add_argument("--source", help="label written to the decision log")
        p.add_argument("--id", help="item id written to the decision log")
        p.add_argument("--data-dir", help="cache, log and mode file (default .idgate, env IDGATE_DATA_DIR)")
        p.add_argument("--json", action="store_true", help="full result as JSON")

    p = sub.add_parser("check", help="lint and judge one text")
    text_opts(p)
    judge_opts(p)
    p.add_argument("--lint-only", action="store_true", help="no judge: an audit, not a pass to publish")
    p.set_defaults(fn=_cmd_check)

    p = sub.add_parser("lint", help="deterministic rules only, no judge")
    text_opts(p)
    p.set_defaults(fn=_cmd_lint)

    p = sub.add_parser("gate", help="check, rewrite on failure, check again; prints the final text")
    text_opts(p)
    judge_opts(p)
    p.add_argument("--max-rounds", type=int, default=2)
    p.add_argument("--no-rewrite", action="store_true", help="check only: for text a human already approved")
    p.add_argument("--lint-only", action="store_true")
    p.set_defaults(fn=_cmd_gate)

    p = sub.add_parser("sweep", help="check every file in a folder, one file = one text")
    text_opts(p, needs_input=False)
    judge_opts(p)
    p.add_argument("--dir", required=True)
    p.add_argument("--glob", default="*.md")
    p.add_argument("--limit", type=int, default=0)
    p.add_argument("--lint-only", action="store_true")
    p.set_defaults(fn=_cmd_sweep)

    p = sub.add_parser("site", help="scan a site or app: md, txt, html, JSON locales (and TS/TSX strings)")
    p.add_argument("paths", nargs="+", help="files or folders")
    judge_opts(p)
    p.add_argument("--include-ts", action="store_true", help="also string literals in .ts .tsx .js .jsx .mjs")
    p.add_argument("--exclude", action="append", help="glob of files to skip, relative path (repeatable)")
    p.add_argument("--only", help="regex on file path")
    p.add_argument("--lint-only", action="store_true")
    p.add_argument("--warn", action="store_true", help="with --lint-only: print warn findings too")
    p.add_argument("--fix", action="store_true", help="rewrite failing batches and write the result back into the files")
    p.add_argument("--rounds", type=int, default=2, help="rewrite rounds per batch with --fix")
    p.add_argument("--workers", type=int, default=4, help="batches judged in parallel")
    p.add_argument("--batch-chars", type=int, default=site.BATCH_CHARS)
    p.add_argument("--plan", action="store_true", help="count batches, call no judge")
    p.add_argument("--dump", help="write the extracted strings to a JSON file and stop")
    p.add_argument("--report", help="write per-batch results and rewrites to a JSON file")
    p.add_argument("--rules")
    p.add_argument("--guide")
    p.add_argument("--data-dir")
    p.add_argument("--json", action="store_true")
    p.set_defaults(fn=_cmd_site)

    p = sub.add_parser("mode", help="print the active mode: enforce or shadow")
    p.add_argument("--data-dir")
    p.set_defaults(fn=_cmd_mode)

    p = sub.add_parser("guides", help="list the shipped style guides")
    p.set_defaults(fn=_cmd_guides)
    return ap


def main(argv=None):
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except (AttributeError, ValueError):
            pass
    a = build_parser().parse_args(argv)
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())
