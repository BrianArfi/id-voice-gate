#!/usr/bin/env python3
"""id-voice-gate tests. Fully offline: every judge is a fake.

    python3 -m unittest discover -s tests -v
    python3 tests/test_idgate.py

Live smoke test (calls the real `claude` CLI, two strings, about 30 seconds):
    IDGATE_LIVE=1 python3 -m unittest tests.test_idgate -k Live -v
"""
import http.server
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

import idgate  # noqa: E402
from idgate import core, judges, site  # noqa: E402

LIVE = os.environ.get("IDGATE_LIVE") == "1"
FAKE_JUDGE = os.path.join(HERE, "fake_judge.py")
FAKE_CMD = '"%s" "%s"' % (sys.executable, FAKE_JUDGE)

# Translated patterns that MUST be blocked by lint, whatever the judge says.
BAD = [
    ("post", "Tiga langkah, udah."),
    ("post", "Komentar nempel di bagian yang dimaksud."),
    ("post", "Feedback langsung nempel di bagian yang dimaksud. Gak ada lagi screenshot dicoret-coret."),
    ("post", "Laporan mingguan, notulen, dan prioritas pagi, dikerjain AI. Lo tinggal review."),
    ("post", "Gw sedang membangun komunitas bagi para profesional kantoran."),
    ("post", "Lo bisa pakai tool tersebut tiap hari."),
    ("post", "Buat gw ini merupakan cara paling cepet."),
    ("post", "Maksimalin jaringan kontak lo secara efektif."),
    ("post", "Upload file-nya. Beres."),
    ("post", "Gak ribet."),
    ("post", "Satu tempat buat semua kerjaan lo."),
    ("post", "Hasilnya: satu file rapi buat lo."),
    ("post", "Level lo lagi turun -- satu sesi aja cukup."),
    ("post", "Kena PHK itu nakutin\u2014gw pernah ngalamin."),
    ("post", "Jadilah orang yang diburu, bukan yang di-PHK. Gw bantu lo."),
    ("heading", "Kenapa Notula?"),
    ("heading", "Kenalan sama Notula"),
    ("heading", "Yang lo butuhin"),
    ("heading", "Gitu doang."),
    ("page", "## Kenapa Notula?\n\nGw ngajarin yang gw pakai tiap hari."),
    ("page", "### Itu aja.\n\nLo tinggal cek hasilnya."),
    ("message", "Prosesnya tiga langkah, selesai."),
]

# Natural lines. Lint must stay quiet on them.
GOOD = [
    "Cuma tiga langkah.",
    "AI yang nyusun laporan mingguan, notulen, dan prioritas pagi. Lo tinggal cek.",
    "Yang dulu 3 jam, jadi 20 menit. Gw pakai tiap hari.",
    "Tiap hari ada tool AI baru. Yang kemarin aja belum sempet dicoba.",
    "Rekaman Anda kami hapus dalam 30 hari.",
]


def judge_ok(text, kind, context, findings, guide):
    return {"natural": True, "score": 5, "issues": [], "rewrite": text}


def judge_bad(text, kind, context, findings, guide):
    return {"natural": False, "score": 2, "issues": [{"quote": text[:10], "why": "terjemahan", "fix": "x"}],
            "rewrite": text + " Gw jamin."}


def judge_down(*a, **k):
    raise RuntimeError("claude CLI exit 1: Not logged in")


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="idgate-")
        self._saved = dict(vars(core.SETTINGS))
        core.SETTINGS.data_dir = self.tmp
        core.SETTINGS.backend = "claude-cli"
        core.SETTINGS.judge_cmd = ""
        core.SETTINGS.extra_cmd = ""
        core.JUDGE_FN = None
        del core.EXTRA_CHECKS[:]
        os.environ.pop("IDGATE_MODE", None)

    def tearDown(self):
        vars(core.SETTINGS).update(self._saved)
        core.JUDGE_FN = None
        del core.EXTRA_CHECKS[:]
        os.environ.pop("IDGATE_MODE", None)
        shutil.rmtree(self.tmp, ignore_errors=True)

    def log_rows(self):
        p = os.path.join(self.tmp, "decisions.jsonl")
        if not os.path.exists(p):
            return []
        with open(p, encoding="utf-8") as fh:
            return [json.loads(line) for line in fh if line.strip()]

    def run_cli(self, *args, env=None, cwd=None):
        e = dict(os.environ, IDGATE_DATA_DIR=self.tmp, PYTHONIOENCODING="utf-8")
        for k in ("IDGATE_JUDGE", "IDGATE_JUDGE_CMD", "IDGATE_MODE", "FAKE_JUDGE_FAIL"):
            e.pop(k, None)
        e.update(env or {})
        return subprocess.run([sys.executable, "-m", "idgate"] + list(args), capture_output=True, text=True,
                              encoding="utf-8", env=dict(e, PYTHONPATH=ROOT), cwd=cwd or ROOT)


# ─────────────────────────── regression: the reason this exists ───────────────────────────

class TestRegression(Base):
    def test_translated_punchline_fails_even_with_a_happy_judge(self):
        r = idgate.check("Tiga langkah, udah.", "post", judge_fn=judge_ok)
        self.assertFalse(r["pass"])
        self.assertIn("punch_x_udah", r["blocking"])

    def test_natural_line_passes(self):
        r = idgate.check("Cuma tiga langkah, terus laporannya jadi sendiri.", "post", judge_fn=judge_ok)
        self.assertTrue(r["pass"], r["reason"])
        self.assertTrue(r["judge_available"])
        self.assertEqual(r["pass_kind"], "full")

    def test_cli_regression_pair(self):
        bad = self.run_cli("check", "--judge", "command", "--judge-cmd", FAKE_CMD, "--text", "Tiga langkah, udah.")
        self.assertEqual(bad.returncode, 1, bad.stdout + bad.stderr)
        self.assertIn("punch_x_udah", bad.stdout)
        good = self.run_cli("check", "--judge", "command", "--judge-cmd", FAKE_CMD, "--text", "Cuma tiga langkah.")
        self.assertEqual(good.returncode, 0, good.stdout + good.stderr)
        self.assertTrue(good.stdout.startswith("PASS"))


# ─────────────────────────── lint ───────────────────────────

class TestLint(Base):
    def test_bad_strings_blocked(self):
        for kind, text in BAD:
            with self.subTest(text=text):
                f = idgate.lint(text, kind)
                self.assertTrue(idgate.blocking(f), "should be blocked: %r -> %r" % (text, f))

    def test_good_strings_not_blocked(self):
        for text in GOOD:
            with self.subTest(text=text):
                self.assertEqual(idgate.blocking(idgate.lint(text, "post")), [])

    def _examples(self, name):
        with open(os.path.join(core.GUIDES_DIR, name + ".md"), encoding="utf-8") as fh:
            g = fh.read()
        return re.findall(r"\*\*Sebelum:\*\* (.+)", g), re.findall(r"\*\*Sesudah:\*\* (.+)", g)

    def test_casual_guide_after_examples_pass(self):
        before, after = self._examples("jakarta-casual")
        self.assertEqual(len(after), 15)
        for s in after:
            with self.subTest(after=s[:40]):
                self.assertEqual(idgate.blocking(idgate.lint(s, "post")), [])
        for i in (6, 7, 13):  # 7 dikerjain AI, 8 nempel, 14 " -- "
            self.assertTrue(idgate.blocking(idgate.lint(before[i], "post")), before[i])

    def test_formal_guide_after_examples_pass(self):
        before, after = self._examples("formal")
        self.assertEqual(len(after), 8)
        for s in after:
            with self.subTest(after=s[:40]):
                self.assertEqual(idgate.blocking(idgate.lint(s, "page", {"register": "formal"})), [])
        self.assertTrue(idgate.blocking(idgate.lint(before[3], "post")), before[3])  # Tiga langkah, selesai.

    def test_guides_and_rules_have_no_dash_chars(self):
        for name in idgate.list_guides():
            with open(idgate.guide_path(guide=name), encoding="utf-8") as fh:
                self.assertNotRegex(fh.read(), "[\u2014\u2013]", name)
        with open(core.DEFAULT_RULES, encoding="utf-8") as fh:
            self.assertNotRegex(fh.read(), "[\u2014\u2013]")

    def test_formal_words_need_casual_register(self):
        legal = "Data tersebut merupakan milik Anda dan kami simpan dengan aman."
        self.assertEqual(idgate.blocking(idgate.lint(legal, "page")), [])
        self.assertTrue(idgate.blocking(idgate.lint(legal + " Gw jamin.", "page")))
        self.assertTrue(idgate.blocking(idgate.lint(legal, "page", {"register": "casual"})))

    def test_quotes_code_and_urls_are_skipped(self):
        t = "> Kenapa X? -- kata dia\n\nCek di https://a.com/satu--dua ya, gw tunggu.\n\n```\na -- b\n```"
        self.assertEqual(idgate.blocking(idgate.lint(t, "post")), [])

    def test_lo_density_warns(self):
        t = "Lo buka file. Lo baca isinya. Lo tulis catatan. Lo kirim ke tim lo."
        self.assertIn("lo_density", [f["rule"] for f in idgate.lint(t, "post")])

    def test_skip_rule(self):
        self.assertEqual(idgate.blocking(idgate.lint("Gak ribet.", "post", {"skip_rules": ["punch_gak_ribet"]})), [])

    def test_rules_json_has_required_fields(self):
        for r in core.load_rules()["rules"]:
            with self.subTest(rule=r["id"]):
                for k in ("id", "scope", "severity", "why", "fix", "example_bad", "example_good"):
                    self.assertIn(k, r)
                if r.get("type", "regex") == "regex":
                    self.assertIn(r["scope"], ("heading", "prose", "any"))
                    bad = r["example_bad"].replace("\\u2014", "\u2014")
                    kind = "heading" if r["scope"] == "heading" else "post"
                    hit = [f["rule"] for f in idgate.lint(bad, kind, {"register": "casual"})]
                    self.assertIn(r["id"], hit, "example_bad does not trigger its own rule")

    def test_register_detection(self):
        self.assertEqual(idgate.detect_register("Gw pakai tiap hari."), "casual")
        self.assertEqual(idgate.detect_register("Kami simpan data Anda."), "formal")
        self.assertEqual(idgate.detect_register("x", {"register": "casual"}), "casual")


# ─────────────────────────── rewrite guard ───────────────────────────

class TestRewriteGuard(Base):
    def test_tokens_kept(self):
        o = "Cek https://example.com/kelas 3 hari lagi @notula \U0001F680\n\n#AI #Notula {{price}}"
        self.assertEqual(idgate.rewrite_problems(o, o.replace("Cek", "Lihat")), [])
        for broken in (o.replace("#AI ", ""), o.replace("https://example.com/kelas", "link"),
                       o.replace("3 hari", "5 hari"), o.replace("\n\n", " "), o.replace("{{price}}", ""),
                       o.replace("\U0001F680", ""), o.replace("@notula", "")):
            self.assertTrue(idgate.rewrite_problems(o, broken), broken)

    def test_negation_dropped_is_rejected(self):
        probs = idgate.rewrite_problems("AI bukan pengganti PM. Gw udah coba sendiri.",
                                        "AI itu pengganti PM. Gw udah coba sendiri.")
        self.assertTrue(any("negation" in p for p in probs), probs)
        self.assertEqual(idgate.rewrite_problems("Ini tidak ribet, gw jamin.", "Ini gak ribet, gw jamin."), [])

    def test_entity_swap_is_rejected(self):
        o = "Gw pakai Claude buat notulen, terus ChatGPT buat ide."
        probs = idgate.rewrite_problems(o, "Gw pakai Gemini buat notulen, terus ChatGPT buat ide.")
        self.assertTrue(any("names" in p for p in probs), probs)
        self.assertEqual(idgate.rewrite_problems(o, "Claude gw pakai buat notulen, terus ChatGPT buat ide."), [])

    def test_sentence_start_capital_is_not_an_entity(self):
        self.assertEqual(idgate.rewrite_problems("Bayangkan laporan lo jadi sendiri.",
                                                 "Bayangin laporan lo jadi sendiri."), [])

    def test_quote_kept_verbatim(self):
        o = 'Kata member: "aku jadi pulang lebih cepat tiap hari". Gw seneng dengernya.'
        probs = idgate.rewrite_problems(o, 'Kata member: "gw jadi pulang lebih cepet tiap hari". Gw seneng dengernya.')
        self.assertTrue(any("quote" in p for p in probs), probs)


# ─────────────────────────── check: fail-closed ───────────────────────────

class TestCheck(Base):
    def test_judge_low_score_fails(self):
        self.assertFalse(idgate.check("Gw pakai tiap hari.", "post", judge_fn=judge_bad)["pass"])

    def test_score_4_but_not_natural_fails(self):
        fn = lambda *a: {"natural": False, "score": 4, "issues": [], "rewrite": a[0]}  # noqa: E731
        self.assertFalse(idgate.check("Gw pakai tiap hari.", "post", judge_fn=fn)["pass"])

    def test_judge_down_fails_closed(self):
        r = idgate.check("Gw pakai tiap hari.", "post", judge_fn=judge_down)
        self.assertFalse(r["pass"])
        self.assertFalse(r["judge_available"])
        self.assertTrue(r["reason"].startswith("judge_unavailable"))

    def test_malformed_judge_fails_closed(self):
        r = idgate.check("Gw pakai tiap hari.", "post", judge_fn=lambda *a: {"score": "lima"})
        self.assertFalse(r["pass"])
        self.assertFalse(r["judge_available"])

    def test_missing_guide_fails_closed(self):
        r = idgate.check("Gw pakai tiap hari.", "post", {"guide": os.path.join(self.tmp, "nope.md")},
                         judge_fn=judge_ok)
        self.assertFalse(r["pass"])
        self.assertFalse(r["judge_available"])

    def test_missing_claude_binary_fails_closed(self):
        os.environ["CLAUDE_BIN"] = os.path.join(self.tmp, "no-such-claude")
        try:
            r = idgate.check("Gw pakai tiap hari.", "post")
        finally:
            os.environ.pop("CLAUDE_BIN", None)
        self.assertFalse(r["pass"])
        self.assertFalse(r["judge_available"])
        self.assertEqual(r["judge"]["backend"], "claude-cli")

    def test_backend_none_is_labelled_lint_only(self):
        core.SETTINGS.backend = "none"
        r = idgate.check("Gw pakai tiap hari.", "post")
        self.assertTrue(r["pass"])
        self.assertEqual(r["pass_kind"], "lint")
        self.assertIn("not a pass to publish", r["reason"])
        self.assertFalse(idgate.check("Tiga langkah, udah.", "post")["pass"])

    def test_guide_follows_register(self):
        seen = []

        def fn(text, kind, context, findings, guide):
            seen.append(guide.splitlines()[0])
            return judge_ok(text, kind, context, findings, guide)
        idgate.check("Gw pakai tiap hari.", "post", judge_fn=fn)
        idgate.check("Kami simpan data Anda dengan aman.", "page", judge_fn=fn)
        self.assertIn("santai", seen[0])
        self.assertIn("formal", seen[1])

    def test_log_rows(self):
        idgate.check("Gw pakai tiap hari.", "post", {"source": "tes", "item_id": "a1"}, judge_fn=judge_ok)
        rows = self.log_rows()
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["source"], "tes")
        self.assertEqual(rows[0]["judge"]["score"], 5)
        self.assertNotIn("text", rows[0], "the log keeps a hash, not the draft text")
        self.assertTrue(os.path.exists(os.path.join(self.tmp, ".gitignore")))

    def test_prompt_carries_guide_lint_and_text(self):
        f = idgate.lint("Tiga langkah, udah.", "post")
        p = idgate.build_judge_prompt("Tiga langkah, udah.", "post", {}, f, "PANDUAN-X")
        for part in ("PANDUAN-X", "punch_x_udah", "<text>\nTiga langkah, udah.\n</text>", '"natural"'):
            self.assertIn(part, p)
        self.assertNotRegex(p, "[\u2014\u2013]")


# ─────────────────────────── extra checkers ───────────────────────────

class TestExtraChecks(Base):
    def test_python_checker_blocks(self):
        @idgate.register_checker
        def no_competitor(text, kind, context):
            if "KompetitorX" in text:
                return [{"rule": "no_competitor", "severity": "block", "why": "jangan sebut kompetitor"}]
            return []
        r = idgate.check("Lebih cepet dari KompetitorX.", "post", judge_fn=judge_ok)
        self.assertFalse(r["pass"])
        self.assertIn("no_competitor", r["blocking"])
        self.assertTrue(idgate.check("Lebih cepet dari kemarin.", "post", judge_fn=judge_ok)["pass"])

    def test_broken_checker_only_warns(self):
        idgate.register_checker(lambda *a: 1 / 0)
        r = idgate.check("Gw pakai tiap hari.", "post", judge_fn=judge_ok)
        self.assertTrue(r["pass"])
        self.assertIn("extra_error", [f["rule"] for f in r["lint"]])

    def test_command_checker(self):
        script = os.path.join(self.tmp, "chk.py")
        with open(script, "w", encoding="utf-8") as fh:
            fh.write("import json,sys\nd=json.load(sys.stdin)\n"
                     "print(json.dumps([{'rule':'harga','severity':'block'}] if 'gratis' in d['text'] else []))\n")
        core.SETTINGS.extra_cmd = '"%s" "%s"' % (sys.executable, script)
        self.assertFalse(idgate.check("Semua gratis.", "post", judge_fn=judge_ok)["pass"])
        self.assertTrue(idgate.check("Semua murah.", "post", judge_fn=judge_ok)["pass"])


# ─────────────────────────── backends ───────────────────────────

class TestBackends(Base):
    def test_command_backend_and_cache(self):
        counter = os.path.join(self.tmp, "calls.txt")
        os.environ["FAKE_JUDGE_COUNTER"] = counter
        core.SETTINGS.backend, core.SETTINGS.judge_cmd = "command", FAKE_CMD
        try:
            a = idgate.check("Gw pakai tiap hari.", "post")
            b = idgate.check("Gw pakai tiap hari.", "post")
            c = idgate.check("Gw pakai tiap pagi.", "post")
        finally:
            os.environ.pop("FAKE_JUDGE_COUNTER")
        with open(counter) as fh:
            self.assertEqual(len(fh.read()), 2)
        self.assertTrue(a["pass"], a["reason"])
        self.assertFalse(a["judge"]["cached"])
        self.assertTrue(b["judge"]["cached"])
        self.assertFalse(c["judge"]["cached"])

    def test_command_backend_outage(self):
        core.SETTINGS.backend, core.SETTINGS.judge_cmd = "command", FAKE_CMD
        os.environ["FAKE_JUDGE_FAIL"] = "1"
        try:
            r = idgate.check("Gw pakai tiap hari.", "post", use_cache=False)
        finally:
            os.environ.pop("FAKE_JUDGE_FAIL")
        self.assertFalse(r["pass"])
        self.assertIn("not logged in", r["judge"]["error"])

    def test_claude_cli_backend_with_fake_binary(self):
        if os.name == "nt":
            fake = os.path.join(self.tmp, "claude.cmd")
            with open(fake, "w") as fh:
                fh.write('@"%s" "%s" %%*\n' % (sys.executable, FAKE_JUDGE))
        else:
            fake = os.path.join(self.tmp, "claude")
            with open(fake, "w") as fh:
                fh.write('#!/bin/sh\nexec "%s" "%s" "$@"\n' % (sys.executable, FAKE_JUDGE))
            os.chmod(fake, 0o755)
        os.environ["CLAUDE_BIN"] = fake
        try:
            r = idgate.check("Lo tinggal review.", "post", use_cache=False)
        finally:
            os.environ.pop("CLAUDE_BIN")
        self.assertEqual(r["judge"]["status"], "ok", r["judge"])
        self.assertFalse(r["pass"])
        self.assertEqual(r["judge"]["rewrite"], "Lo tinggal cek.")

    def test_anthropic_api_backend_against_local_server(self):
        seen = {}

        class H(http.server.BaseHTTPRequestHandler):
            def do_POST(self):
                body = json.loads(self.rfile.read(int(self.headers["content-length"])))
                seen.update(body=body, key=self.headers.get("x-api-key"), ver=self.headers.get("anthropic-version"))
                out = json.dumps({"content": [{"type": "text", "text": '```json\n{"natural": true, "score": 5, '
                                                                        '"issues": [], "rewrite": "x"}\n```'}]})
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.end_headers()
                self.wfile.write(out.encode())

            def log_message(self, *a):
                pass
        srv = http.server.HTTPServer(("127.0.0.1", 0), H)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        env = {"ANTHROPIC_BASE_URL": "http://127.0.0.1:%d" % srv.server_port, "ANTHROPIC_API_KEY": "test-key"}
        old = {k: os.environ.get(k) for k in env}
        os.environ.update(env)
        core.SETTINGS.backend = "anthropic-api"
        try:
            r = idgate.check("Gw pakai tiap hari.", "post", use_cache=False)
        finally:
            srv.shutdown()
            srv.server_close()
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v
        self.assertTrue(r["pass"], r["reason"])
        self.assertEqual(seen["body"]["model"], "claude-sonnet-5")
        self.assertEqual(seen["key"], "test-key")
        self.assertEqual(seen["ver"], "2023-06-01")

    def test_anthropic_api_without_key(self):
        old = os.environ.pop("ANTHROPIC_API_KEY", None)
        core.SETTINGS.backend = "anthropic-api"
        try:
            r = idgate.check("Gw pakai tiap hari.", "post", use_cache=False)
        finally:
            if old:
                os.environ["ANTHROPIC_API_KEY"] = old
        self.assertFalse(r["judge_available"])
        self.assertIn("ANTHROPIC_API_KEY", r["judge"]["error"])

    def test_parse_json_tolerates_chatter(self):
        self.assertEqual(judges.parse_json('Here you go:\n{"a": 1}\nDone.'), {"a": 1})
        with self.assertRaises(ValueError):
            judges.parse_json("no json here")


# ─────────────────────────── gate ───────────────────────────

class TestGate(Base):
    def test_rewrite_fixes_and_passes(self):
        bad = "Tiga langkah, udah.\n\nDaftar di https://example.com/kelas #AI"
        good = "Cuma tiga langkah.\n\nDaftar di https://example.com/kelas #AI"

        def fn(text, kind, context, findings, guide):
            if text == bad:
                return {"natural": False, "score": 2, "issues": [], "rewrite": good}
            return judge_ok(text, kind, context, findings, guide)
        g = idgate.gate(bad, "post", judge_fn=fn)
        self.assertTrue(g["passed"], g["report"]["reason"])
        self.assertEqual((g["rounds"], g["final_text"]), (1, good))
        self.assertTrue(g["report"]["changed"])
        self.assertEqual([r["event"] for r in self.log_rows()], ["check", "check", "gate_final"])

    def test_rewrite_that_drops_link_is_rejected_then_retried(self):
        bad = "Tiga langkah, udah.\n\nDaftar di https://example.com/kelas #AI"
        good = "Cuma tiga langkah.\n\nDaftar di https://example.com/kelas #AI"

        def fn(text, kind, context, findings, guide):
            if text == bad and not context.get("rewrite_feedback"):
                return {"natural": False, "score": 2, "issues": [], "rewrite": "Cuma tiga langkah.\n\nDaftar sekarang"}
            if text == bad:
                return {"natural": False, "score": 2, "issues": [], "rewrite": good}
            return judge_ok(text, kind, context, findings, guide)
        g = idgate.gate(bad, "post", judge_fn=fn)
        self.assertTrue(g["passed"], g["report"]["reason"])
        self.assertEqual(g["final_text"], good)
        self.assertIn("rewrite_rejected", g["report"]["history"][0])

    def test_max_rounds_then_hold(self):
        g = idgate.gate("Gw pakai tiap hari.", "post", judge_fn=judge_bad, max_rounds=2)
        self.assertFalse(g["passed"])
        self.assertEqual(g["rounds"], 2)

    def test_judge_down_holds_without_rewrite(self):
        g = idgate.gate("Gw pakai tiap hari.", "post", judge_fn=judge_down)
        self.assertFalse(g["passed"])
        self.assertEqual((g["rounds"], g["final_text"]), (0, "Gw pakai tiap hari."))
        self.assertFalse(g["report"]["judge_available"])

    def test_no_rewrite_mode(self):
        g = idgate.gate("Gw pakai tiap hari.", "post", judge_fn=judge_bad, rewrite=False)
        self.assertFalse(g["passed"])
        self.assertEqual(g["rounds"], 0)

    def test_gate_with_command_judge_end_to_end(self):
        core.SETTINGS.backend, core.SETTINGS.judge_cmd = "command", FAKE_CMD
        with open(os.path.join(ROOT, "examples", "draft-bad.md"), encoding="utf-8") as fh:
            bad = fh.read()
        g = idgate.gate(bad, "post")
        self.assertTrue(g["passed"], g["report"])
        want = ["AI yang nyusun notulen, daftar tugas, dan laporan mingguan.", "Lo tinggal cek.", "",
                "Rekam meeting, upload, kirim.", "Cuma tiga langkah.", ""]
        self.assertEqual(g["final_text"], "\n".join(want))


class TestMode(Base):
    def test_default_is_enforce(self):
        self.assertEqual(idgate.mode(), "enforce")
        self.assertFalse(idgate.gate("Upload file-nya. Beres.", "post", judge_fn=judge_down)["passed"])

    def test_shadow_passes_original_and_logs_verdict(self):
        os.environ["IDGATE_MODE"] = "shadow"
        text = "Upload file-nya. Beres."
        g = idgate.gate(text, "post", judge_fn=judge_bad)
        self.assertTrue(g["passed"])
        self.assertEqual(g["final_text"], text, "shadow never rewrites")
        self.assertFalse(g["report"]["verdict"]["pass"])
        rows = [r for r in self.log_rows() if r.get("event") == "check"]
        self.assertTrue(rows[0]["mode"] == "shadow" and rows[0]["verdict_pass"] is False)

    def test_mode_file(self):
        with open(os.path.join(self.tmp, "mode"), "w") as fh:
            fh.write("shadow\n")
        self.assertEqual(idgate.mode(), "shadow")
        with open(os.path.join(self.tmp, "mode"), "w") as fh:
            fh.write("nonsense\n")
        self.assertEqual(idgate.mode(), "enforce")


# ─────────────────────────── CLI ───────────────────────────

class TestCli(Base):
    def test_exit_codes(self):
        self.assertEqual(self.run_cli("check", "--lint-only", "--text", "Gw pakai tiap hari.").returncode, 0)
        self.assertEqual(self.run_cli("check", "--lint-only", "--text", "Tiga langkah, udah.").returncode, 1)
        down = self.run_cli("check", "--judge", "command", "--judge-cmd", FAKE_CMD, "--text", "Gw pakai tiap hari.",
                            env={"FAKE_JUDGE_FAIL": "1"})
        self.assertEqual(down.returncode, 2, down.stdout + down.stderr)
        self.assertIn("unavailable", down.stdout)
        none = self.run_cli("check", "--judge", "none", "--text", "Gw pakai tiap hari.")
        self.assertEqual(none.returncode, 0)
        self.assertIn("not a pass to publish", none.stdout)

    def test_gate_prints_final_text(self):
        r = self.run_cli("gate", "--judge", "command", "--judge-cmd", FAKE_CMD, "--file", "examples/draft-bad.md")
        self.assertEqual(r.returncode, 0, r.stdout + r.stderr)
        self.assertIn("Cuma tiga langkah.", r.stdout)
        self.assertIn("PASS", r.stderr)

    def test_lint_command(self):
        r = self.run_cli("lint", "--text", "Gak ribet.")
        self.assertEqual(r.returncode, 1)
        self.assertIn("punch_gak_ribet", r.stdout)

    def test_sweep_lint_only(self):
        d = os.path.join(self.tmp, "sw")
        os.makedirs(d)
        with open(os.path.join(d, "a.md"), "w", encoding="utf-8") as fh:
            fh.write("---\ntitle: x -- y\n---\nGw pakai tiap hari.\n")
        with open(os.path.join(d, "b.md"), "w", encoding="utf-8") as fh:
            fh.write("Satu tempat buat semua kerjaan lo.\n")
        r = self.run_cli("sweep", "--dir", d, "--lint-only")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("2 files, 1 failed", r.stdout)

    def test_mode_command(self):
        r = self.run_cli("mode", env={"IDGATE_MODE": "shadow"})
        self.assertEqual(r.stdout.strip(), "shadow")

    def test_version(self):
        self.assertIn(idgate.__version__, self.run_cli("--version").stdout)


# ─────────────────────────── site mode ───────────────────────────

class TestSite(Base):
    def setUp(self):
        super().setUp()
        self.site = os.path.join(self.tmp, "site")
        shutil.copytree(os.path.join(ROOT, "examples", "site"), self.site)

    def read(self, rel):
        with open(os.path.join(self.site, rel), encoding="utf-8") as fh:
            return fh.read()

    def test_extract_units(self):
        files = site.walk([self.site], include_ts=True)
        self.assertFalse(any(f.endswith("en.json") for f in files), "English locale is skipped")
        units = site.extract(files, include_ts=True)
        texts = {u["judgeText"]: u for u in units}
        self.assertEqual(texts["Kenalan sama Notula"]["scope"], "heading")
        self.assertEqual(texts["Tiga langkah, udah."]["register"], "casual")
        self.assertEqual(texts["Data yang kami simpan"]["register"], "formal")
        html_unit = [u for u in units if u["judgeText"].startswith("Rekaman Anda")][0]
        self.assertIn("<0>30 hari</0>", html_unit["judgeText"])
        self.assertIn("<1>tim kami</1>", html_unit["judgeText"])
        self.assertIn("Gak ribet.", texts)
        self.assertNotIn("Gak ribet.", {u["judgeText"] for u in site.extract(files, include_ts=False)})

    def test_site_lint_only_exit(self):
        r = self.run_cli("site", self.site, "--lint-only", "--include-ts")
        self.assertEqual(r.returncode, 1, r.stdout)
        self.assertIn("punch_x_udah", r.stdout)

    def test_site_fix_writes_back(self):
        r = self.run_cli("site", self.site, "--include-ts", "--fix", "--judge", "command", "--judge-cmd", FAKE_CMD,
                         "--workers", "2")
        self.assertIn("rewritten in place", r.stdout, r.stdout + r.stderr)
        loc = json.loads(self.read("locales/id.json"))
        self.assertEqual(loc["hero"]["title"], "Begini cara kerja Notula")
        self.assertEqual(loc["steps"]["items"][2], "Cuma tiga langkah.")
        self.assertEqual(loc["pricing"]["note"], "Gratis buat {{seats}} orang pertama di tim lo.")
        page = self.read("public/privasi.html")
        self.assertIn("<p>Layanan kami merekam meeting secara otomatis.</p>", page)
        self.assertIn("<strong>30 hari</strong>", page)
        self.assertIn('<a href="mailto:privasi@example.com">tim kami</a>', page)
        self.assertEqual(self.read("locales/en.json").count("Meet Notula"), 1, "English file untouched")
        after = self.run_cli("site", self.site, "--lint-only")
        self.assertEqual(after.returncode, 0, after.stdout)

    def test_render_rejects_broken_placeholders(self):
        files = site.walk([self.site])
        u = [u for u in site.extract(files) if u["judgeText"].startswith("Rekaman Anda")][0]
        self.assertIsNone(site.render_unit(u, "Rekaman Anda kami hapus dalam 30 hari."))
        ok = site.render_unit(u, u["judgeText"].replace("kalau ada pertanyaan", "jika ada pertanyaan"))
        self.assertIn("<strong>30 hari</strong>", ok)

    def test_split_back(self):
        b = {"items": [{"text": "Satu", "scope": "heading"}, {"text": "Dua {{x}}", "scope": "prose"}]}
        self.assertEqual(site.split_back(b, "## Satu lagi\n\nDua {{x}} lagi")[0], ["Satu lagi", "Dua {{x}} lagi"])
        self.assertEqual(site.split_back(b, "## Satu\n\nDua lagi")[0], ["Satu", None])
        self.assertIsNone(site.split_back(b, "Satu dan dua")[0])

    def test_ts_literals(self):
        src = "import x from 'paket-gak-ini';\n// 'komentar yang gak dicek'\nconst a = { title: 'Ini judul yang gak natural' };\n" \
              "const b = `pakai ${x} gak dicek`;\n"
        units = site.ts_units("a.ts", src)
        self.assertEqual([u["text"] for u in units], ["Ini judul yang gak natural"])
        self.assertEqual(site.render_unit(units[0], "Judul baru yang gak aneh"), "'Judul baru yang gak aneh'")


# ─────────────────────────── live ───────────────────────────

@unittest.skipUnless(LIVE, "live: set IDGATE_LIVE=1 (calls the real claude CLI)")
class TestLiveSmoke(Base):
    def test_live_judge_two_strings(self):
        bad = "Laporan mingguan, notulen, dan prioritas pagi, dikerjain AI. Lo tinggal review."
        good = "AI yang nyusun laporan mingguan, notulen, dan prioritas pagi. Lo tinggal cek."
        rb = idgate.check(bad, "post", use_cache=False)
        self.assertTrue(rb["judge_available"], rb["judge"])
        self.assertFalse(rb["pass"])
        rg = idgate.check(good, "post", use_cache=False)
        self.assertTrue(rg["judge_available"], rg["judge"])
        self.assertGreaterEqual(rg["judge"]["score"], 3, rg["judge"])
        print("\n[live] bad: score=%s rewrite=%r" % (rb["judge"]["score"], rb["judge"]["rewrite"]))
        print("[live] good: score=%s natural=%s" % (rg["judge"]["score"], rg["judge"]["natural"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
