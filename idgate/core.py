"""Core of id-voice-gate: lint, judge, check, gate.

Three layers per check:

1. lint:  regex rules from rules.json. A `block` finding fails the text. A `warn`
          finding is a hint passed to the judge.
2. judge: an LLM reads the text against a style guide and returns
          {natural, score 1-5, issues, rewrite}. The backend is pluggable
          (see idgate.judges).
3. extra: optional checkers you register yourself (Python functions or a shell
          command). Their `block` findings fail the text too.

PASS = no lint block, no extra block, judge.natural is true and judge.score >= 4.

Fail-closed: a judge that is down, times out, is not logged in or returns
broken JSON means NOT passed (exit 2 on the CLI). Never publish on a judge outage.
Standard library only.
"""
import hashlib
import json
import os
import re
import subprocess
import sys
import threading
import time

from . import judges as _judges

PKG_DIR = os.path.dirname(os.path.abspath(__file__))
GUIDES_DIR = os.path.join(PKG_DIR, "guides")
DEFAULT_RULES = os.path.join(PKG_DIR, "rules.json")

KINDS = ("post", "caption", "page", "heading", "message")
REGISTERS = ("casual", "formal")
PASS_SCORE = 4
# Bump when the meaning of the judge prompt changes, so old cache entries are not reused.
JUDGE_PROMPT_VERSION = "1"
MODES = ("enforce", "shadow")

GUIDE_BY_REGISTER = {"casual": "jakarta-casual", "formal": "formal"}

# Python hook for tests or callers that bring their own judge. Signature:
#   fn(text, kind, context, lint_findings, guide_text) -> dict {natural, score, issues, rewrite}
# Raise = judge unavailable (fail-closed).
JUDGE_FN = None

# Extra checkers: fn(text, kind, context) -> list of findings
# {rule, severity: block|warn, why, fix, match}. Raise = ignored with a warn finding.
EXTRA_CHECKS = []


class Settings:
    """Runtime settings. Read from the environment once, overridable per call."""

    def __init__(self):
        self.data_dir = os.environ.get("IDGATE_DATA_DIR") or ".idgate"
        self.rules_path = os.environ.get("IDGATE_RULES") or DEFAULT_RULES
        self.guide = os.environ.get("IDGATE_GUIDE") or ""  # path or name; "" = by register
        self.backend = os.environ.get("IDGATE_JUDGE") or "claude-cli"
        self.model = os.environ.get("IDGATE_MODEL") or ""
        self.timeout = int(os.environ.get("IDGATE_TIMEOUT") or 240)
        self.judge_cmd = os.environ.get("IDGATE_JUDGE_CMD") or ""
        self.extra_cmd = os.environ.get("IDGATE_EXTRA_CHECK") or ""


SETTINGS = Settings()


def register_checker(fn):
    """Add an extra checker. Returns fn, so it works as a decorator."""
    EXTRA_CHECKS.append(fn)
    return fn


# ───────────────────────────── mode ─────────────────────────────
# enforce (default): failures are held. shadow: the verdict is still computed and
# logged, but the ORIGINAL text always passes and is never rewritten. Use it during
# a judge outage instead of ripping the gate out. Order: env IDGATE_MODE, then the
# file <data_dir>/mode, then enforce.

def mode():
    m = (os.environ.get("IDGATE_MODE") or "").strip().lower()
    if not m:
        try:
            with open(os.path.join(SETTINGS.data_dir, "mode"), encoding="utf-8") as fh:
                m = fh.read().strip().lower()
        except OSError:
            m = ""
    return m if m in MODES else "enforce"


# ───────────────────────────── files ─────────────────────────────

def _ensure_data_dir():
    os.makedirs(SETTINGS.data_dir, exist_ok=True)
    gi = os.path.join(SETTINGS.data_dir, ".gitignore")
    if not os.path.exists(gi):
        try:
            with open(gi, "w", encoding="utf-8") as fh:
                fh.write("# id-voice-gate cache and logs hold draft text: never commit them\n*\n")
        except OSError:
            pass


def log_path():
    return os.path.join(SETTINGS.data_dir, "decisions.jsonl")


_RULES = {}


def load_rules(path=None):
    path = path or SETTINGS.rules_path
    m = os.path.getmtime(path)
    hit = _RULES.get(path)
    if hit and hit[0] == m:
        return hit[1]
    with open(path, encoding="utf-8") as fh:
        data = json.load(fh)
    for r in data["rules"]:
        if r.get("type", "regex") == "regex":
            fl = re.IGNORECASE if "i" in (r.get("flags") or "") else 0
            r["_re"] = re.compile(r["pattern"], fl)
    _RULES[path] = (m, data)
    return data


def list_guides():
    return sorted(n[:-3] for n in os.listdir(GUIDES_DIR) if n.endswith(".md"))


def guide_path(register="casual", guide=None):
    """Resolve a guide: explicit path, a shipped guide name, or the default for the register."""
    g = guide or SETTINGS.guide
    if g:
        if os.path.exists(g):
            return g
        p = os.path.join(GUIDES_DIR, g if g.endswith(".md") else g + ".md")
        return p
    return os.path.join(GUIDES_DIR, GUIDE_BY_REGISTER.get(register, "jakarta-casual") + ".md")


def guide_text(register="casual", guide=None):
    try:
        with open(guide_path(register, guide), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def guide_version(text):
    if text is None:
        return "missing"
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


# ───────────────────────────── preprocessing ─────────────────────────────

_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)
_FENCE = re.compile(r"^```.*?^```\s*$", re.S | re.M)
_INLINE_CODE = re.compile(r"`[^`\n]+`")
_URL = re.compile(r"https?://\S+|www\.\S+")
_CASUAL = re.compile(r"\b(gw|gue|gua|lo|lu|elo|elu)\b", re.I)
_HEADING_MD = re.compile(r"^\s{0,3}#{1,6}\s+(.*)$")
_HEADING_BOLD = re.compile(r"^\s*\*\*([^*]{1,120})\*\*\s*$")
_JSX_TAG = re.compile(r"</?\d+>")


def strip_frontmatter(text):
    return _FRONTMATTER.sub("", text or "", count=1)


def _lint_lines(text, kind):
    """-> list of (lineno, line, scope). Drops frontmatter, code, URLs and '>' quote lines."""
    body = strip_frontmatter(text)
    body = _FENCE.sub(lambda m: "\n" * m.group(0).count("\n"), body)
    out = []
    for i, raw in enumerate(body.split("\n"), 1):
        line = _JSX_TAG.sub("", _URL.sub(" ", _INLINE_CODE.sub(" ", raw))).rstrip()
        if not line.strip() or line.lstrip().startswith(">"):
            continue
        if kind == "heading":
            out.append((i, line.strip().strip("#*").strip(), "heading"))
            continue
        m = _HEADING_MD.match(line) or _HEADING_BOLD.match(line)
        if m:
            out.append((i, m.group(1).strip().strip("*").strip(), "heading"))
        else:
            out.append((i, line.strip(), "prose"))
    return out


def detect_register(text, context=None):
    """casual when the text uses gw/lo (or context says so), else formal."""
    reg = (context or {}).get("register")
    if reg in REGISTERS:
        return reg
    return "casual" if _CASUAL.search(text or "") else "formal"


_SENT_SPLIT = re.compile(r"(?<=[.!?])\s+|\n+")
_LO = re.compile(r"\b(lo|lu|elo)\b", re.I)


def _severity(rule, scope):
    sev = rule.get("severity", "warn")
    if isinstance(sev, dict):
        return sev.get(scope) or sev.get("any") or "warn"
    return sev


# ───────────────────────────── lint ─────────────────────────────

def lint(text, kind="post", context=None, rules_path=None):
    """Deterministic lint. -> list of findings {rule, severity, scope, line, match, why, fix}."""
    context = context or {}
    rules = load_rules(rules_path)["rules"]
    skip = set(context.get("skip_rules") or ())
    casual = detect_register(text, context) == "casual"
    lines = _lint_lines(text, kind)
    findings = []
    for r in rules:
        if r["id"] in skip:
            continue
        if r.get("requires") == "casual" and not casual:
            continue
        scope = r.get("scope", "any")
        if r.get("type") == "metric":
            findings += _metric(r, [ln for ln in lines if scope in ("any", ln[2])])
            continue
        for lineno, line, lscope in lines:
            if scope != "any" and scope != lscope:
                continue
            m = r["_re"].search(line)
            if m:
                findings.append({
                    "rule": r["id"], "severity": _severity(r, lscope), "scope": lscope,
                    "line": lineno, "match": m.group(0).strip()[:80],
                    "why": r.get("why", ""), "fix": r.get("fix", ""),
                })
    return findings


def _metric(rule, lines):
    if rule["id"] != "lo_density":
        return []
    sents = []
    for _, line, _ in lines:
        sents += [s for s in _SENT_SPLIT.split(line) if len(s.split()) >= 2]
    out = []
    if not sents:
        return out
    per = [len(_LO.findall(s)) for s in sents]
    worst = max(per)
    if worst > int(rule.get("max_per_sentence", 2)):
        s = sents[per.index(worst)]
        out.append({"rule": rule["id"], "severity": _severity(rule, "prose"), "scope": "prose",
                    "line": None, "match": s[:80], "why": rule["why"], "fix": rule["fix"],
                    "detail": "%d x 'lo' in one sentence" % worst})
    share = sum(1 for n in per if n) / len(per)
    if len(sents) >= int(rule.get("min_sentences", 4)) and share > float(rule.get("max_share", 0.6)):
        out.append({"rule": rule["id"], "severity": _severity(rule, "prose"), "scope": "prose",
                    "line": None, "match": "", "why": rule["why"], "fix": rule["fix"],
                    "detail": "'lo' in %.0f%% of sentences" % (share * 100)})
    return out


def blocking(findings):
    return [f for f in findings if f["severity"] == "block"]


def run_extra_checks(text, kind, context):
    """Registered Python checkers plus the optional shell command (IDGATE_EXTRA_CHECK).
    The command gets JSON {text, kind, register} on stdin and prints a JSON list of findings."""
    out = []
    for fn in list(EXTRA_CHECKS):
        try:
            for f in fn(text, kind, context) or []:
                out.append(_norm_finding(f, getattr(fn, "__name__", "extra")))
        except Exception as e:  # noqa: BLE001
            out.append({"rule": "extra_error", "severity": "warn", "scope": "any", "line": None,
                        "match": "", "why": "%s: %s" % (type(e).__name__, str(e)[:200]), "fix": ""})
    cmd = context.get("extra_cmd") or SETTINGS.extra_cmd
    if cmd:
        payload = json.dumps({"text": text, "kind": kind, "register": context.get("register")},
                             ensure_ascii=False)
        try:
            p = subprocess.run(cmd, shell=True, input=payload, capture_output=True, text=True,
                               encoding="utf-8", timeout=SETTINGS.timeout)
            if p.returncode != 0:
                raise RuntimeError("exit %s: %s" % (p.returncode, (p.stderr or "").strip()[:200]))
            data = json.loads(p.stdout or "[]")
            for f in data if isinstance(data, list) else []:
                out.append(_norm_finding(f, "extra_cmd"))
        except Exception as e:  # noqa: BLE001
            out.append({"rule": "extra_error", "severity": "warn", "scope": "any", "line": None,
                        "match": "", "why": "extra check command: %s" % str(e)[:200], "fix": ""})
    return out


def _norm_finding(f, default_rule):
    f = dict(f or {})
    sev = f.get("severity") if f.get("severity") in ("block", "warn") else "warn"
    return {"rule": str(f.get("rule") or default_rule), "severity": sev, "scope": f.get("scope", "any"),
            "line": f.get("line"), "match": str(f.get("match", ""))[:80],
            "why": str(f.get("why", ""))[:300], "fix": str(f.get("fix", ""))[:300], "extra": True}


# ───────────────────────────── protected tokens ─────────────────────────────

_HASHTAG = re.compile(r"(?<![\w&])#\w+")
_MENTION = re.compile(r"(?<![\w.])@[\w.]+\w")
_PLACEHOLDER = re.compile(r"\{\{[^}]+\}\}|<\d+>|</\d+>|\{[a-z_]+\}")
_NUMBER = re.compile(r"\d+(?:[.,:]\d+)*")
_EMOJI = re.compile("[\U0001F000-\U0001FAFF\u2600-\u27BF\u2B00-\u2BFF]")


def protected_tokens(text):
    t = text or ""
    return {
        "url": sorted(set(u.rstrip(".,)") for u in _URL.findall(t))),
        "hashtag": sorted(set(h.lower() for h in _HASHTAG.findall(t))),
        "mention": sorted(set(_MENTION.findall(t))),
        "placeholder": sorted(_PLACEHOLDER.findall(t)),
        "number": sorted(set(_NUMBER.findall(_URL.sub(" ", t)))),
        "emoji": sorted(set(_EMOJI.findall(t))),
    }


def _paragraphs(text):
    return len([p for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip()])


# Quotes from other people: the content must stay exactly as written.
_QUOTED = re.compile("\"([^\"\\n]{6,})\"|\u201c([^\u201d\\n]{6,})\u201d")
# Negations: the count must stay the same. A dropped "bukan" flips the meaning.
_NEGATION = re.compile(r"\b(tidak|tak|bukan|bukannya|gak|ga|nggak|ngga|enggak|engga|kagak|"
                       r"ndak|gk|tdk|belum|blm|jangan|jgn|no|not|never)\b", re.I)
_WORD = re.compile(r"[A-Za-z][A-Za-z0-9'\-]*[A-Za-z0-9]|[A-Za-z]")
_SENT_START = re.compile(r"(?:^|[.!?:;]\s+|\n)\W*([A-Za-z][A-Za-z0-9'\-]*)")


def _clean_for_entities(text):
    t = _URL.sub(" ", text or "")
    t = _HASHTAG.sub(" ", t)
    t = _PLACEHOLDER.sub(" ", t)
    return _MENTION.sub(" ", t)


def entities(text):
    """Names, products and models: mixed-case or digit words (ChatGPT, GPT-5, iPhone),
    all-caps abbreviations (AI, PM, WA), and Capitalised words that do NOT start a
    sentence. -> lowercase set. Sentence starts are skipped: that capital is grammar."""
    t = _clean_for_entities(text)
    starts = {m.start(1) for m in _SENT_START.finditer(t)}
    out = set()
    for m in _WORD.finditer(t):
        w = m.group(0).strip("'-")
        if len(w) < 2:
            continue
        mixed = any(c.isupper() for c in w[1:]) or any(c.isdigit() for c in w)
        caps = w.isupper()
        if mixed or caps or (w[0].isupper() and m.start() not in starts):
            out.add(w.lower())
    return out


def _words_lower(text):
    return {w.lower().strip("'-") for w in _WORD.findall(_clean_for_entities(text))}


def rewrite_problems(original, rewrite):
    """What a rewrite broke: tokens lost or added, paragraph count, edited quotes,
    names lost or added, negation count. [] = safe to use."""
    if not rewrite or not rewrite.strip():
        return ["empty rewrite"]
    a, b = protected_tokens(original), protected_tokens(rewrite)
    probs = []
    for k in a:
        if a[k] != b[k]:
            lost = [x for x in a[k] if x not in b[k]]
            added = [x for x in b[k] if x not in a[k]]
            probs.append("%s changed (lost %s, added %s)" % (k, lost[:5], added[:5]))
    if _paragraphs(original) != _paragraphs(rewrite):
        probs.append("paragraph count changed (%d -> %d)" % (_paragraphs(original), _paragraphs(rewrite)))
    rw_flat = " ".join(rewrite.split())
    edited = [q for m in _QUOTED.finditer(original) for q in m.groups() if q
              and " ".join(q.split()) not in rw_flat]
    if edited:
        probs.append("quote edited: %s" % [q[:40] for q in edited[:3]])
    ea, eb = entities(original), entities(rewrite)
    wa, wb = _words_lower(original), _words_lower(rewrite)
    lost = sorted(e for e in ea if e not in wb)
    added = sorted(e for e in eb if e not in wa)
    if lost or added:
        probs.append("names or products changed (lost %s, added %s)" % (lost[:5], added[:5]))
    na, nb = len(_NEGATION.findall(original)), len(_NEGATION.findall(rewrite))
    if na != nb:
        probs.append("negation count changed (%d -> %d): the meaning may reverse" % (na, nb))
    return probs


# ───────────────────────────── judge ─────────────────────────────

KIND_LABEL = {
    "post": "a social media post (LinkedIn, Threads, Instagram), line-per-phrase layout",
    "caption": "a short social media caption",
    "page": "website page copy (markdown). '## ' marks a heading",
    "heading": "a heading or button label",
    "message": "a chat message (WhatsApp, DM)",
}


def build_judge_prompt(text, kind, context, findings, guide, feedback=None):
    context = context or {}
    lint_lines = []
    for f in findings:
        lint_lines.append("- [%s] %s: \"%s\" (%s) Fix: %s" % (
            f["severity"].upper(), f["rule"], f.get("match") or f.get("detail", ""),
            f.get("why", ""), f.get("fix", "")))
    lint_block = "\n".join(lint_lines) or "(none)"
    register = detect_register(text, context)
    reg_label = "casual Jakarta gw/lo" if register == "casual" else "formal Indonesian (Anda/kami)"
    extra = ""
    if context.get("audience"):
        extra += "Audience: %s\n" % context["audience"]
    if context.get("author"):
        extra += "Written in the voice of: %s\n" % context["author"]
    if feedback:
        extra += ("Your previous rewrite was rejected because: %s. This time keep those items "
                  "exactly as in the original.\n" % "; ".join(feedback))
    tester = ("A friendly Jakarta office colleague reading it out loud" if register == "casual"
              else "A polite native Indonesian professional reading it out loud")
    return f"""You are a strict native Indonesian editor from Jakarta. You check Indonesian text that an AI wrote, before it is published.

Question: does this text read like a native speaker wrote it, or like it was thought in English and translated? Readers complain that AI copy sounds "aneh", like a translation. Be strict. {tester} is the test.

Style guide (single source of truth, follow it exactly):
<guide>
{guide}
</guide>

Text type: {KIND_LABEL.get(kind, kind)}
Register: {reg_label}
{extra}
Deterministic lint findings. BLOCK items must be gone from your rewrite. WARN items are hints only; ignore them when the line is natural:
{lint_block}

Text to judge:
<text>
{text}
</text>

Scoring:
5 = native, nothing to change.
4 = native, only tiny nits.
3 = noticeably translated or stiff in places.
2 = mostly translated.
1 = machine translation.
natural = true only when a native speaker would say every sentence as it is.

Rewrite rules (the rewrite is used automatically, so follow them exactly):
- Change wording only. Keep every fact, number, date, price, name, product name, URL, hashtag, @mention, emoji and placeholder ({{{{x}}}}, {{expr_a}}, <0>...</0>) exactly as written.
- Keep every negation (bukan, tidak, gak, belum, jangan): same meaning, same number of negation words.
- Keep the same paragraphs (blank lines in the same places) and the same layout: one phrase per line stays one phrase per line, headings stay headings.
- No new claims, no new facts, no new sentences that add meaning.
- No em-dash, no en-dash, no " -- ".
- Keep the register: gw/lo stays gw/lo; a formal Anda/kami text stays formal.
- Do not rewrite quoted material from other people (testimonials, text inside quotation marks).
- If nothing needs to change, return the text unchanged.

Return ONLY one JSON object, no code fence, no other text:
{{"natural": true|false, "score": 1-5, "issues": [{{"quote": "exact substring of the text", "why": "short reason in Indonesian", "fix": "replacement"}}], "rewrite": "full corrected text"}}"""


def normalize_verdict(v):
    """Validate the judge's answer. Raises JudgeUnavailable when the shape is broken (fail-closed)."""
    if not isinstance(v, dict):
        raise _judges.JudgeUnavailable("judge answer is not a JSON object")
    nat = v.get("natural")
    if isinstance(nat, str):
        nat = nat.strip().lower() == "true"
    if not isinstance(nat, bool):
        raise _judges.JudgeUnavailable("judge gave no boolean 'natural'")
    try:
        score = int(round(float(v.get("score"))))
    except (TypeError, ValueError):
        raise _judges.JudgeUnavailable("judge gave no numeric 'score'")
    score = max(1, min(5, score))
    issues = []
    for it in v.get("issues") or []:
        if isinstance(it, dict):
            issues.append({k: str(it.get(k, ""))[:400] for k in ("quote", "why", "fix")})
    rw = v.get("rewrite")
    return {"natural": nat, "score": score, "issues": issues,
            "rewrite": rw if isinstance(rw, str) else None}


def _backend(context):
    return (context or {}).get("judge") or SETTINGS.backend


def _cache_key(text, kind, gv, register, backend, model):
    h = hashlib.sha256()
    h.update((text or "").encode("utf-8"))
    h.update(("\n\x00%s\x00%s\x00%s\x00%s\x00%s\x00%s" % (
        gv, kind, JUDGE_PROMPT_VERSION, backend, model, register)).encode("utf-8"))
    return h.hexdigest()


def _cache_get(key):
    p = os.path.join(SETTINGS.data_dir, "cache", key[:2], key + ".json")
    try:
        with open(p, encoding="utf-8") as fh:
            return json.load(fh)
    except (OSError, ValueError):
        return None


def _cache_put(key, verdict):
    try:
        _ensure_data_dir()
        d = os.path.join(SETTINGS.data_dir, "cache", key[:2])
        os.makedirs(d, exist_ok=True)
        tmp = os.path.join(d, key + ".%d.tmp" % os.getpid())
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(verdict, fh, ensure_ascii=False)
        os.replace(tmp, os.path.join(d, key + ".json"))
    except OSError:
        pass


def run_judge(text, kind, context, findings, judge_fn=None, use_cache=True, feedback=None):
    """-> verdict dict + {status, cached, model, backend}. status ok | unavailable | skipped.
    Never raises."""
    context = context or {}
    register = detect_register(text, context)
    guide = guide_text(register, context.get("guide"))
    gv = guide_version(guide)
    fn = judge_fn or JUDGE_FN
    backend = "custom" if fn is not None else _backend(context)
    if backend == "none" and fn is None:
        return {"status": "skipped", "backend": "none"}
    if guide is None:
        return {"status": "unavailable", "backend": backend,
                "error": "style guide not found: %s" % guide_path(register, context.get("guide"))}
    model = "" if fn is not None else _judges.model_for(backend, context.get("model") or SETTINGS.model)
    key = _cache_key(text, kind, gv, register, backend, model)
    if use_cache and not feedback and fn is None:
        hit = _cache_get(key)
        if hit:
            hit.update(status="ok", cached=True)
            return hit
    t0 = time.time()
    try:
        ctx = dict(context, rewrite_feedback=feedback) if feedback else context
        if fn is not None:
            raw = fn(text, kind, ctx, findings, guide)
        else:
            prompt = build_judge_prompt(text, kind, context, findings, guide, feedback)
            raw = _judges.call(backend, prompt, model=model, timeout=SETTINGS.timeout,
                               command=context.get("judge_cmd") or SETTINGS.judge_cmd)
        v = normalize_verdict(raw)
    except _judges.JudgeUnavailable as e:
        return {"status": "unavailable", "backend": backend, "error": str(e)[:400]}
    except Exception as e:  # noqa: BLE001  (a broken fake judge = same thing, fail-closed)
        return {"status": "unavailable", "backend": backend,
                "error": "%s: %s" % (type(e).__name__, str(e)[:300])}
    v["backend"] = backend
    v["model"] = model or backend
    v["latency_s"] = round(time.time() - t0, 2)
    if use_cache and fn is None:
        _cache_put(key, v)
    v.update(status="ok", cached=False)
    return v


# ───────────────────────────── check / gate ─────────────────────────────

_LOG_LOCK = threading.Lock()


def _log(row):
    if os.environ.get("IDGATE_NO_LOG"):
        return
    try:
        _ensure_data_dir()
        with _LOG_LOCK, open(log_path(), "a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")
    except OSError as e:
        sys.stderr.write("[idgate] could not write log: %s\n" % e)


def check(text, kind="post", context=None, judge=True, judge_fn=None, use_cache=True,
          log=True, _feedback=None, _meta=None):
    """One check. -> {pass, reason, lint, blocking, judge, ...}. Never raises.

    judge=False (or backend 'none') = lint only. That result is an audit, not
    permission to publish (pass_kind='lint').
    """
    context = dict(context or {})
    if kind not in KINDS:
        kind = "post"
    text = text if isinstance(text, str) else ""
    findings = lint(text, kind, context) + run_extra_checks(text, kind, context)
    blocks = blocking(findings)
    jv = run_judge(text, kind, context, findings, judge_fn, use_cache, _feedback) if judge \
        else {"status": "skipped", "backend": "none"}
    judged = jv.get("status") != "skipped"

    if not text.strip():
        passed, reason = False, "empty text"
    elif blocks:
        passed, reason = False, "lint block: " + ", ".join(sorted({f["rule"] for f in blocks}))
    elif not judged:
        passed, reason = True, "lint clean (lint only, no judge: not a pass to publish)"
    elif jv.get("status") != "ok":
        passed, reason = False, "judge_unavailable: " + str(jv.get("error", ""))[:200]
    elif not jv.get("natural") or jv.get("score", 0) < PASS_SCORE:
        passed, reason = False, "judge: natural=%s score=%s" % (jv.get("natural"), jv.get("score"))
    else:
        passed, reason = True, "natural"
    if blocks and jv.get("status") == "ok":
        reason += " (judge: natural=%s score=%s)" % (jv.get("natural"), jv.get("score"))
    gate_mode = mode()
    verdict = {"pass": passed, "reason": reason}
    if gate_mode == "shadow" and not passed and text.strip():
        passed, reason = True, "shadow (real verdict: %s)" % reason

    register = detect_register(text, context)
    gtext = guide_text(register, context.get("guide"))
    res = {
        "pass": passed, "reason": reason, "kind": kind, "mode": gate_mode,
        "verdict": verdict, "register": register,
        "judge_available": (jv.get("status") == "ok") if judged else None,
        "pass_kind": "full" if judged else "lint",
        "lint": findings, "blocking": [f["rule"] for f in blocks],
        "judge": jv,
        "guide": os.path.basename(guide_path(register, context.get("guide"))),
        "guide_version": guide_version(gtext),
        "text_sha": hashlib.sha256(text.encode("utf-8")).hexdigest()[:16],
    }
    if log:
        row = {"ts": round(time.time(), 3), "event": "check", "source": context.get("source"),
               "item_id": context.get("item_id"), "kind": kind, "chars": len(text),
               "text_sha": res["text_sha"], "guide_version": res["guide_version"],
               "passed": passed, "reason": reason, "pass_kind": res["pass_kind"],
               "mode": gate_mode, "verdict_pass": verdict["pass"],
               "lint_block": res["blocking"],
               "lint_warn": sorted({f["rule"] for f in findings if f["severity"] == "warn"}),
               "judge": {k: jv.get(k) for k in ("status", "natural", "score", "cached", "backend",
                                                  "model", "latency_s", "error") if k in jv}}
        if _meta:
            row.update(_meta)
        _log(row)
    return res


def gate(text, kind="post", context=None, rewrite=True, max_rounds=2, judge_fn=None,
         use_cache=True, log=True):
    """Check; on failure take the judge's rewrite, check again, at most max_rounds times.

    -> {final_text, passed, rounds, report}. passed False = HOLD the item. final_text is
    then only the last candidate, never permission to publish.
    """
    context = dict(context or {})
    gid = hashlib.sha256(("%s%s" % (time.time(), text)).encode("utf-8")).hexdigest()[:12]
    current = text
    history = []
    rounds = 0
    shadow = mode() == "shadow"
    res = check(current, kind, context, judge_fn=judge_fn, use_cache=use_cache, log=log,
                _meta={"gate_id": gid, "round": 0})
    history.append(_slim(res, current))
    while not res["pass"] and rewrite and rounds < max_rounds and not shadow:
        jv = res["judge"]
        if jv.get("status") != "ok":
            break  # judge down or lint only: no rewrite, fail-closed
        cand = jv.get("rewrite")
        if not cand or cand.strip() == current.strip():
            history[-1]["note"] = "judge returned no different rewrite"
            break
        rounds += 1
        probs = rewrite_problems(text, cand)
        if probs:
            # The rewrite broke a fact or token: drop it, ask the judge again with the reason.
            history[-1]["rewrite_rejected"] = probs
            res = check(current, kind, context, judge_fn=judge_fn, use_cache=False, log=log,
                        _feedback=probs, _meta={"gate_id": gid, "round": rounds, "retry_after_reject": True})
            history.append(_slim(res, current))
            continue
        current = cand
        res = check(current, kind, context, judge_fn=judge_fn, use_cache=use_cache, log=log,
                    _meta={"gate_id": gid, "round": rounds})
        history.append(_slim(res, current))

    passed = bool(res["pass"])
    report = {
        "gate_id": gid, "kind": kind, "reason": res["reason"],
        "judge_available": res.get("judge_available"), "pass_kind": res.get("pass_kind"),
        "changed": current != text, "original_text": text,
        "history": history, "guide": res["guide"], "guide_version": res["guide_version"],
        "mode": res.get("mode"), "verdict": res.get("verdict"),
    }
    if log:
        _log({"ts": round(time.time(), 3), "event": "gate_final", "gate_id": gid,
              "source": context.get("source"), "item_id": context.get("item_id"),
              "kind": kind, "passed": passed, "rounds": rounds, "reason": res["reason"],
              "changed": current != text, "final_sha": res["text_sha"]})
    return {"final_text": current, "passed": passed, "rounds": rounds, "report": report}


def _slim(res, text):
    j = res.get("judge") or {}
    return {"pass": res["pass"], "reason": res["reason"], "blocking": res["blocking"],
            "warn": sorted({f["rule"] for f in res["lint"] if f["severity"] == "warn"}),
            "judge": {k: j.get(k) for k in ("status", "natural", "score", "issues", "error", "cached")
                      if k in j},
            "text": text}
