"""Site and locale mode: find every Indonesian string in a project, judge it in
batches, and write accepted rewrites back into the source files.

A "unit" is one string a reader sees: a markdown or text file, a string in a JSON
locale file, a run of text inside an HTML element (inline tags kept as <0>..</0>,
other markup as {expr_a}), an HTML attribute (alt, title, aria-label, placeholder,
meta description), and with --include-ts a string literal in TS/TSX/JS/JSX.

Each unit remembers its exact position in its file, so a rewrite can be put back
without hand editing. Before writing, the file is read again and the unit must
still hold the same text, and every placeholder must survive. Anything else is
skipped and reported.

Units are grouped per file (per top-level section for JSON), split by register
(gw/lo vs formal), and cut into batches of about 6,000 characters. One batch is
judged as one 'page', so the judge reads the copy in context. Grouping is
deterministic, so an unchanged batch hits the cache.
"""
import fnmatch
import html
import json
import os
import re
from collections import Counter, OrderedDict, defaultdict

BATCH_CHARS = 6000
SHARED_MIN_FILES = 4
PARA = re.compile(r"\n\s*\n")
TOKENS = re.compile(r"</?\d+>|\{expr_[a-z]+\}|\{\{[^}]+\}\}")

TEXT_EXT = (".md", ".markdown", ".txt")
HTML_EXT = (".html", ".htm")
JSON_EXT = (".json",)
TS_EXT = (".ts", ".tsx", ".js", ".jsx", ".mjs")
SKIP_DIRS = {"node_modules", "dist", "build", "out", "coverage", "vendor", "__pycache__"}

# ─────────────────────────── language detection ───────────────────────────

ID_WORDS = set("""yang dan di ke dari untuk buat gak ga nggak enggak engga tidak tak bukan lo lu gw gue elo aja saja
udah sudah belum bisa ini itu dengan sama juga atau kalau kalo karena jadi lagi mau ada akan bakal cuma cuman hanya semua kita
kami kamu anda cara tiap setiap lebih banyak sendiri pakai pake biar supaya masih baru lihat liat daftar gabung gratis harga kelas
sesi tulisan masuk keluar kirim simpan hapus batal tutup buka cari kembali balik beranda halaman tentang hubungi bulan hari minggu
jam menit detik tahun orang kerja kerjaan kantor belajar sekarang selengkapnya tanya jawab berhasil gagal sedang memuat tunggu
coba ulang salin tersalin unduh pilih ubah selesai beres kelar simpel gampang doang gitu gini kenapa apa siapa gimana bagaimana
kapan mana berapa sebelum sesudah setelah pertama terakhir punya bikin dapet dapat langsung nanti dulu sih kok deh dong nih tuh
para oleh pada bagi terhadap tersebut merupakan adalah akun sandi masukan umpan balik peserta materi lanjut lanjutkan tampilkan
sembunyikan tersedia segera hadir kenalan butuhin perlu tempat satu dua tiga empat lima enam tujuh delapan sembilan sepuluh
ribu juta rupiah tanggal waktu jadwal acara sertifikat anggota tim produk kursus nonton dengar baca tulis kerjain dikerjain
nyoba tentu soal tanpa lewat antara atas bawah dalam luar depan belakang besok kemarin hasil hasilnya intinya mulai
selamat terima kasih maaf silakan klik tombol isi kosong benar salah semuanya ngobrol obrolan cerita catatan""".split())
EN_WORDS = set("""the and of to is are you your for with this that in on it be will can not from by an we our how what why
more about get all use here no yes my me i do does has have was were they their which when where who into out up just than then
them there these those would should could been being its it's also only any each other some such very can't don't""".split())


def lang_score(text):
    t = re.sub(r"</?\d+>|\{\{[^}]*\}\}|\{[a-z_]+\}|https?://\S+", " ", str(text).lower())
    words = re.findall(r"[a-z]+(?:'[a-z]+)?", t)
    i = sum(1 for w in words if w in ID_WORDS or (len(w) >= 5 and w.endswith("nya")))
    e = sum(1 for w in words if w in EN_WORDS)
    return i, e


def looks_indonesian(text):
    i, e = lang_score(text)
    return i > 0 and i >= e


def _has_letters(s):
    return bool(re.search(r"[A-Za-z\u00C0-\u024F]{2,}", s or ""))


def _looks_like_code(s):
    return len(re.findall(r"[{};]", s)) >= 4 or bool(re.match(r"^\s*[.#@]?[a-z-]+\s*\{", s))


def _line_of(src, pos):
    return src.count("\n", 0, pos) + 1


def _expr_name(i):
    s = ""
    i += 1
    while i > 0:
        r = (i - 1) % 26
        s = chr(97 + r) + s
        i = (i - 1) // 26
    return "expr_" + s


PROSE_KEY = re.compile(r"(subtitle|subheading|description|desc|body|text|blurb|intro|lead|note|hint|placeholder|"
                       r"paragraph|answer|detail|summary|excerpt|copy|message|msg|quote|tagline)", re.I)
HEADING_KEY = re.compile(r"(title|heading|headline|^h[1-6]$|eyebrow|kicker|label|cta|button|btn|^tab|tabs?$|"
                         r"question|^q$|badge|chip)", re.I)


def scope_for_key(key):
    if not key:
        return "prose"
    if PROSE_KEY.search(key):
        return "prose"
    if HEADING_KEY.search(key):
        return "heading"
    return "prose"


def _is_en_key(k):
    return k == "en" or k.endswith("En") or bool(re.match(r"^en[A-Z_]", k)) or k.endswith("_en")


# ─────────────────────────── markdown / text ───────────────────────────

_FRONTMATTER = re.compile(r"\A---\s*\n.*?\n---\s*\n", re.S)


def text_units(file, src):
    m = _FRONTMATTER.match(src)
    start = m.end() if m else 0
    body = src[start:]
    if not body.strip() or not looks_indonesian(body):
        return []
    return [{"kind": "md", "file": file, "start": start, "end": len(src), "line": _line_of(src, start),
             "key": "", "scope": "prose", "group": file, "text": body.strip(), "judgeText": body.strip()}]


# ─────────────────────────── JSON ───────────────────────────

_JSON_TOK = re.compile(r'"(?:[^"\\]|\\.)*"|[{}\[\],:]')


def json_strings(src, offset=0):
    """String tokens of a JSON document with their path. -> [{start, end, value, path}]"""
    out, stack = [], []
    pos = 0
    while True:
        m = _JSON_TOK.search(src, pos)
        if not m:
            break
        tok, pos = m.group(0), m.end()
        top = stack[-1] if stack else None
        if tok == "{":
            stack.append({"type": "obj", "key": None})
            continue
        if tok == "[":
            stack.append({"type": "arr", "idx": 0})
            continue
        if tok in "}]":
            if stack:
                stack.pop()
            continue
        if tok == ",":
            if top and top["type"] == "arr":
                top["idx"] += 1
            continue
        if tok == ":":
            continue
        if top and top["type"] == "obj" and re.match(r"\s*:", src[pos:pos + 64]):
            top["key"] = json.loads(tok)
            continue
        path = [s["idx"] if s["type"] == "arr" else s["key"] for s in stack]
        out.append({"start": offset + m.start(), "end": offset + m.end(), "value": json.loads(tok), "path": path})
    return out


def _is_id_locale(file):
    base = os.path.basename(file).lower()
    parts = file.replace("\\", "/").lower().split("/")
    return base in ("id.json", "id-id.json", "id_id.json") or base.endswith(".id.json") or "id" in parts[:-1]


def _get_at(obj, path):
    for k in path:
        try:
            obj = obj[k]
        except (KeyError, IndexError, TypeError):
            return None
    return obj


def json_units(file, src):
    try:
        parsed = json.loads(src)
    except ValueError:
        return []
    all_id = _is_id_locale(file)
    units = []
    for s in json_strings(src):
        key = s["path"][-1] if s["path"] else None
        parent_key = s["path"][-2] if len(s["path"]) > 1 else None
        if isinstance(key, str) and (key.startswith("$") or key.startswith("_")):
            continue
        if key == "en":
            continue
        text = s["value"]
        if not _has_letters(text) or re.match(r"^(https?:|/|#|mailto:)", text) or re.match(r"^[\w.-]+@[\w.-]+$", text):
            continue
        is_id = all_id
        if not is_id and key == "id":
            parent = _get_at(parsed, s["path"][:-1])
            is_id = isinstance(parent, dict) and "en" in parent
        if not is_id:
            is_id = looks_indonesian(text)
        if not is_id:
            continue
        key_name = str(parent_key) if key == "id" or isinstance(key, int) else str(key)
        top = [k for k in s["path"][:2] if isinstance(k, str) and k != key]
        units.append({
            "kind": "json", "file": file, "start": s["start"], "end": s["end"], "line": _line_of(src, s["start"]),
            "key": ".".join(str(k) for k in s["path"]), "scope": scope_for_key(key_name),
            "group": "%s#%s" % (file, ".".join(top)) if top else file,
            "text": text, "judgeText": text,
        })
    return units


# ─────────────────────────── TS / TSX / JS string literals ───────────────────────────

_JS_ESC = {"n": "\n", "r": "\r", "t": "\t", "b": "\b", "f": "\f", "v": "\v", "0": "\0"}


def _js_unescape(raw):
    out, i = [], 0
    while i < len(raw):
        c = raw[i]
        if c != "\\" or i + 1 >= len(raw):
            out.append(c)
            i += 1
            continue
        n = raw[i + 1]
        if n == "u" and raw[i + 2:i + 3] == "{":
            j = raw.index("}", i)
            out.append(chr(int(raw[i + 3:j], 16)))
            i = j + 1
        elif n == "u":
            out.append(chr(int(raw[i + 2:i + 6], 16)))
            i += 6
        elif n == "x":
            out.append(chr(int(raw[i + 2:i + 4], 16)))
            i += 4
        elif n == "\n":
            i += 2
        else:
            out.append(_JS_ESC.get(n, n))
            i += 2
    return "".join(out)


def _js_escape(s, q):
    r = s.replace("\\", "\\\\")
    if q == "`":
        return q + r.replace("`", "\\`").replace("${", "\\${") + q
    r = r.replace("\n", "\\n").replace("\r", "\\r").replace("\t", "\\t").replace(q, "\\" + q)
    return q + r + q


def ts_units(file, src):
    """String literals only ('..', "..", and `..` without ${}). JSX text is not extracted."""
    units = []
    i, n = 0, len(src)
    while i < n:
        c = src[i]
        if c == "/" and src[i + 1:i + 2] == "/":
            j = src.find("\n", i)
            i = n if j < 0 else j
            continue
        if c == "/" and src[i + 1:i + 2] == "*":
            j = src.find("*/", i + 2)
            i = n if j < 0 else j + 2
            continue
        if c not in "'\"`":
            i += 1
            continue
        j = i + 1
        has_expr = False
        while j < n and src[j] != c:
            if src[j] == "\\":
                j += 2
                continue
            if c != "`" and src[j] == "\n":
                break
            if c == "`" and src[j] == "$" and src[j + 1:j + 2] == "{":
                has_expr = True
            j += 1
        if j >= n or src[j] != c:
            i = j + 1
            continue
        start, end = i, j + 1
        i = end
        if has_expr:
            continue
        raw = src[start + 1:end - 1]
        before = src[max(0, start - 80):start]
        after = src[end:end + 40]
        if re.search(r"\b(import|from|require\()\s*$", before) or re.match(r"\s*:", after) and re.search(r"[{,]\s*$", before):
            continue
        if re.search(r"\b(className|class|href|to|src|id|key|type|variant|size|name|rel|target|role|style)\s*=\s*\{?\s*$", before):
            continue
        try:
            text = _js_unescape(raw)
        except (ValueError, IndexError):
            continue
        if not _has_letters(text) or re.match(r"^(https?:|/|#|mailto:|data:)", text) or _looks_like_code(text):
            continue
        km = re.search(r"([A-Za-z_$][\w$]*)\s*[:=]\s*$", before)
        key = km.group(1) if km else ""
        if key and _is_en_key(key):
            continue
        if not looks_indonesian(text):
            continue
        units.append({"kind": "tsstr", "file": file, "start": start, "end": end, "line": _line_of(src, start),
                      "key": key, "scope": scope_for_key(key), "group": file, "text": text, "judgeText": text,
                      "quote": c})
    return units


# ─────────────────────────── HTML ───────────────────────────

BLOCK_TAGS = set("""p h1 h2 h3 h4 h5 h6 li td th dt dd figcaption blockquote button summary label div section article
header footer nav main ul ol dl table thead tbody tr form aside details figure body html head title option select
textarea br hr img input meta link iframe video audio source canvas caption fieldset legend address picture""".split())
INLINE_HTML = set("a strong b em i span small mark sup sub abbr time u s kbd q cite".split())
SKIP_HTML = set("script style svg pre code noscript template math".split())
HEADING_TAGS = re.compile(r"^(h[1-6]|title|button|summary|th|label|option)$")
META_TEXT = re.compile(r"^(description|og:title|og:description|twitter:title|twitter:description|og:image:alt|"
                       r"twitter:image:alt)$", re.I)
TEXT_ATTRS = ("alt", "title", "aria-label", "placeholder")
_HTML_TOK = re.compile(r"<!--[\s\S]*?-->|<!doctype[^>]*>|</?([a-zA-Z][a-zA-Z0-9-]*)\b((?:[^>\"']|\"[^\"]*\"|'[^']*')*)>|[^<]+|<",
                       re.I)
_ATTR = re.compile(r"([a-zA-Z_:][-a-zA-Z0-9_:.]*)\s*=\s*(\"([^\"]*)\"|'([^']*)')")


def _enc_text(s):
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\u00a0", "&nbsp;")


def _enc_attr(s):
    return _enc_text(s).replace('"', "&quot;")


def html_units(file, src):
    units = []
    stack = []
    run = []

    def flush():
        nonlocal run
        while run and run[0]["type"] == "text" and not run[0]["raw"].strip():
            run.pop(0)
        while run and run[-1]["type"] == "text" and not run[-1]["raw"].strip():
            run.pop()
        if not any(p["type"] == "text" and p["raw"].strip() for p in run):
            run = []
            return
        parts = []
        k = 0
        while k < len(run):
            p = run[k]
            nxt = run[k + 1] if k + 1 < len(run) else None
            nxt2 = run[k + 2] if k + 2 < len(run) else None
            if p["type"] == "open" and nxt2 and nxt["type"] == "text" and nxt2["type"] == "close" and nxt2["tag"] == p["tag"]:
                parts.append({"type": "el", "start": p["start"], "end": nxt2["end"], "open": p["raw"],
                              "close": nxt2["raw"], "innerRaw": nxt["raw"]})
                k += 3
                continue
            if p["type"] == "open" and nxt and nxt["type"] == "close" and nxt["tag"] == p["tag"]:
                parts.append({"type": "expr", "start": p["start"], "end": nxt["end"], "raw": p["raw"] + nxt["raw"]})
                k += 2
                continue
            if p["type"] == "text":
                parts.append(p)
            else:
                parts.append({"type": "expr", "start": p["start"], "end": p["end"], "raw": p["raw"]})
            k += 1
        n_el = n_expr = 0
        display, judge = "", ""
        for p in parts:
            if p["type"] == "text":
                d = html.unescape(p["raw"])
                display += d
                judge += d
            elif p["type"] == "el":
                p["n"] = n_el
                n_el += 1
                d = html.unescape(p["innerRaw"])
                display += d
                judge += "<%d>%s</%d>" % (p["n"], d, p["n"])
            else:
                p["name"] = _expr_name(n_expr)
                n_expr += 1
                display += " "
                judge += "{%s}" % p["name"]
        text = re.sub(r"\s+", " ", display).strip()
        run = []
        if not _has_letters(text) or not looks_indonesian(text):
            return
        tag = stack[-1] if stack else ""
        units.append({"kind": "html", "file": file, "start": parts[0]["start"], "end": parts[-1]["end"],
                      "line": _line_of(src, parts[0]["start"]), "key": "<%s>" % tag, "tag": tag,
                      "scope": "heading" if HEADING_TAGS.match(tag) else "prose", "group": file,
                      "text": text, "judgeText": re.sub(r"\s+", " ", judge).strip(), "parts": parts})

    def attr_units(tag, attrs, tag_start, attrs_offset):
        found = {}
        for m in _ATTR.finditer(attrs):
            found[m.group(1).lower()] = m

        def push(m, scope, key):
            raw = m.group(3) if m.group(3) is not None else m.group(4)
            text = html.unescape(raw)
            if not _has_letters(text) or not looks_indonesian(text):
                return
            start = tag_start + attrs_offset + m.start() + m.group(0).index(m.group(2)) + 1
            units.append({"kind": "attr", "file": file, "start": start, "end": start + len(raw),
                          "line": _line_of(src, start), "key": key, "tag": tag, "scope": scope, "group": file,
                          "text": text, "judgeText": text})

        if tag == "meta" and "content" in found:
            nm = found.get("name") or found.get("property")
            name = (nm.group(3) if nm.group(3) is not None else nm.group(4)) if nm else ""
            if META_TEXT.match(name):
                push(found["content"], "heading" if "title" in name.lower() else "prose", "meta:" + name)
            return
        for a in TEXT_ATTRS:
            if a in found:
                push(found[a], "prose" if a == "alt" else "heading", "@" + a)

    pos = 0
    while True:
        m = _HTML_TOK.search(src, pos)
        if not m:
            break
        tok, start, end = m.group(0), m.start(), m.end()
        pos = end
        if tok.startswith("<!--") or tok.lower().startswith("<!doctype"):
            continue
        if m.group(1):
            tag = m.group(1).lower()
            closing = tok[1] == "/"
            if not closing and tag in SKIP_HTML:
                flush()
                cm = re.compile(r"</%s\s*>" % tag, re.I).search(src, end)
                pos = cm.end() if cm else len(src)
                continue
            if not closing and m.group(2):
                attr_units(tag, m.group(2), start, 1 + len(m.group(1)))
            if tag in INLINE_HTML:
                run.append({"type": "close" if closing else "open", "tag": tag, "start": start, "end": end, "raw": tok})
                continue
            flush()
            self_close = tok.endswith("/>") or tag in ("br", "hr", "img", "input", "meta", "link", "source")
            if closing:
                if tag in stack:
                    idx = len(stack) - 1 - stack[::-1].index(tag)
                    del stack[idx:]
            elif not self_close:
                stack.append(tag)
            continue
        run.append({"type": "text", "start": start, "end": end, "raw": tok})
    flush()
    return units


# ─────────────────────────── extraction ───────────────────────────

_CASUAL = re.compile(r"\b(gw|gue|gua|lo|lu|elo|elu)\b", re.I)


def units_for_file(file, src, include_ts=False):
    low = file.lower()
    if low.endswith(TEXT_EXT):
        units = text_units(file, src)
    elif low.endswith(JSON_EXT):
        units = json_units(file, src)
    elif low.endswith(HTML_EXT):
        units = html_units(file, src)
    elif include_ts and low.endswith(TS_EXT) and not low.endswith(".d.ts"):
        units = ts_units(file, src)
    else:
        units = []
    for u in units:
        u["id"] = "%s#%d" % (u["file"], u["start"])
    return units


def walk(paths, include_ts=False, exclude=()):
    exts = TEXT_EXT + HTML_EXT + JSON_EXT + (TS_EXT if include_ts else ())
    out = []
    for p in paths:
        if os.path.isfile(p):
            out.append(p)
            continue
        for root, dirs, names in os.walk(p):
            dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
            for nm in sorted(names):
                if nm.lower().endswith(exts) and not nm.startswith("."):
                    out.append(os.path.join(root, nm))
    rel = []
    for f in out:
        r = os.path.relpath(f).replace("\\", "/")
        if any(fnmatch.fnmatch(r, g) for g in exclude):
            continue
        base = r.rsplit("/", 1)[-1].lower()
        if base in ("package.json", "package-lock.json", "tsconfig.json", "en.json") or base.endswith(".en.json") \
                or "/en/" in "/" + r:
            continue
        rel.append(r)
    return sorted(set(rel))


def extract(files, include_ts=False):
    units = []
    for f in files:
        try:
            with open(f, encoding="utf-8", newline="") as fh:
                src = fh.read()
        except (OSError, UnicodeDecodeError):
            continue
        units.extend(units_for_file(f, src, include_ts))
    # Register per file: one file speaks in one voice. A string that is clearly formal
    # (Anda/kami, no gw/lo) stays formal even inside a casual file.
    casual = {u["file"] for u in units if _CASUAL.search(u["text"])}
    for u in units:
        formal = re.search(r"\b(Anda|kami)\b", u["text"]) and not _CASUAL.search(u["text"])
        u["register"] = "casual" if u["file"] in casual and not formal else "formal"
    return units


# ─────────────────────────── batches ───────────────────────────

def _norm(s):
    return re.sub(r"\s+", " ", s or "").strip()


def build_batches(units, batch_chars=BATCH_CHARS):
    """Deterministic: the same units give the same batches and the same text."""
    files_by_text = defaultdict(set)
    for u in units:
        files_by_text[u["judgeText"]].add(u["file"])
    shared = {t for t, f in files_by_text.items() if len(f) >= SHARED_MIN_FILES}
    groups = OrderedDict()
    for u in units:
        g = "_shared" if u["judgeText"] in shared else u["group"]
        items = groups.setdefault((g, u["register"]), OrderedDict())
        item = items.setdefault((u["judgeText"], u["scope"]), {"text": u["judgeText"], "scope": u["scope"], "units": []})
        item["units"].append(u)
    batches = []
    for (g, reg), items in sorted(groups.items(), key=lambda kv: kv[0]):
        cur, size, n = [], 0, 0

        def emit(chunk, n):
            batches.append({"id": "%s|%s|%d" % (g, reg, n), "group": g, "register": reg, "items": chunk})

        for it in items.values():
            if PARA.search(it["text"]):  # multi-paragraph text: its own batch
                if cur:
                    emit(cur, n)
                    n += 1
                    cur, size = [], 0
                emit([it], n)
                n += 1
                continue
            ln = len(it["text"]) + 2
            if cur and size + ln > batch_chars:
                emit(cur, n)
                n += 1
                cur, size = [], 0
            cur.append(it)
            size += ln
        if cur:
            emit(cur, n)
    for b in batches:
        b["text"] = batch_text(b)
    return batches


def batch_text(b):
    if len(b["items"]) == 1 and PARA.search(b["items"][0]["text"]):
        return b["items"][0]["text"]
    out = []
    for it in b["items"]:
        t = _norm(it["text"]) if "\n" not in it["text"] else it["text"].strip()
        out.append("## " + t if it["scope"] == "heading" else t)
    return "\n\n".join(out)


def split_back(b, final_text):
    """-> (list of new texts per item, None per item that cannot map back) or (None, reason)."""
    items = b["items"]
    if len(items) == 1 and PARA.search(items[0]["text"]):
        return [final_text.strip()], None
    paras = [p.strip() for p in PARA.split(final_text.strip()) if p.strip()]
    if len(paras) != len(items):
        return None, "paragraph count changed (%d -> %d)" % (len(items), len(paras))
    out = []
    for it, p in zip(items, paras):
        if it["scope"] == "heading" or p.startswith("## "):
            p = re.sub(r"^#{1,6}\s+", "", p)
        if Counter(TOKENS.findall(it["text"])) != Counter(TOKENS.findall(p)):
            out.append(None)
        else:
            out.append(p if "\n" in it["text"] else _norm(p))
    return out, None


# ─────────────────────────── write back ───────────────────────────

def _split_judge(text):
    out, last = [], 0
    for m in re.finditer(r"<(\d+)>([\s\S]*?)</\1>|\{(expr_[a-z]+)\}", text):
        out.append({"type": "text", "value": text[last:m.start()]})
        if m.group(1) is not None:
            out.append({"type": "el", "n": int(m.group(1)), "inner": m.group(2)})
        else:
            out.append({"type": "expr", "name": m.group(3)})
        last = m.end()
    out.append({"type": "text", "value": text[last:]})
    return out


def _same(raw, nv):
    return _norm(html.unescape(raw)) == _norm(nv)


def _lead(raw):
    return re.match(r"^\s*", raw).group(0)


def _trail(raw):
    return re.search(r"\s*$", raw).group(0)


def _render_parts(unit, new_judge):
    segs = _split_judge(new_judge)
    orig = unit["parts"]
    o_tok = [p for p in orig if p["type"] != "text"]
    n_tok = [s for s in segs if s["type"] != "text"]
    if len(o_tok) != len(n_tok):
        return None
    for o, nn in zip(o_tok, n_tok):
        if o["type"] != nn["type"]:
            return None
        if o["type"] == "el" and o["n"] != nn["n"]:
            return None
        if o["type"] == "expr" and o["name"] != nn["name"]:
            return None
    o_gaps = [[]]
    for p in orig:
        if p["type"] == "text":
            o_gaps[-1].append(p)
        else:
            o_gaps.append([])
    n_gaps = [s["value"] for s in segs if s["type"] == "text"]
    out = ""
    for k, o in enumerate(o_gaps):
        nv = n_gaps[k] if k < len(n_gaps) else ""
        if not o:
            g = _enc_text(re.sub(r"\s+", " ", nv)) if nv else ""
        else:
            raw = "".join(p["raw"] for p in o)
            body = _norm(nv)
            if _same(raw, nv):
                g = raw
            elif re.search(r"\n[ \t]*\n", raw.strip()):
                return None
            elif not raw.strip():
                g = raw if not body else ((" " if nv[:1].isspace() else "") + _enc_text(body) + (" " if nv[-1:].isspace() else ""))
            elif not body:
                g = " " if re.search(r"\s", nv) else ""
            else:
                g = _lead(raw) + _enc_text(body) + _trail(raw)
        out += g
        if k < len(o_tok):
            o, nn = o_tok[k], n_tok[k]
            if o["type"] == "expr":
                out += o["raw"]
            elif _same(o["innerRaw"], nn["inner"]):
                out += o["open"] + o["innerRaw"] + o["close"]
            else:
                out += o["open"] + _lead(o["innerRaw"]) + _enc_text(_norm(nn["inner"])) + _trail(o["innerRaw"]) + o["close"]
    return out


def render_unit(unit, new_judge):
    """-> replacement for src[unit.start:unit.end], or None when it cannot be placed safely."""
    k = unit["kind"]
    if k == "json":
        return json.dumps(new_judge, ensure_ascii=False)
    if k == "attr":
        return _enc_attr(new_judge)
    if k == "tsstr":
        return _js_escape(new_judge, unit["quote"])
    if k == "md":
        return new_judge.rstrip("\n") + "\n"
    if k == "html":
        return _render_parts(unit, new_judge)
    return None


def apply_rewrites(rewrites, include_ts=True):
    """rewrites: [{id, file, from, to}]. Units are found again from the CURRENT file
    content, so a stale position is caught. -> (applied ids, [{id, why}] skipped)."""
    by_file = defaultdict(list)
    for r in rewrites:
        by_file[r["file"]].append(r)
    applied, skipped = [], []
    for file, lst in by_file.items():
        with open(file, encoding="utf-8", newline="") as fh:
            src = fh.read()
        units = {u["id"]: u for u in units_for_file(file, src, include_ts)}
        edits = []
        for r in lst:
            u = units.get(r["id"])
            if not u or u["judgeText"] != r["from"]:
                skipped.append({"id": r["id"], "why": "unit changed since it was read"})
                continue
            rep = render_unit(u, r["to"])
            if rep is None:
                skipped.append({"id": r["id"], "why": "placeholder structure changed, or unsafe characters"})
                continue
            edits.append((u["start"], u["end"], rep, r))
        edits.sort(key=lambda e: -e[0])
        for start, end, rep, _r in edits:
            src = src[:start] + rep + src[end:]
        if edits:
            with open(file, "w", encoding="utf-8", newline="") as fh:
                fh.write(src)
        after = {_norm(u["judgeText"]) for u in units_for_file(file, src, include_ts)}
        for _s, _e, _rep, r in edits:
            want = _norm(r["to"])
            if want in after or not looks_indonesian(want):
                applied.append(r["id"])
            else:
                skipped.append({"id": r["id"], "why": "written, but the new text does not read back the same (check by hand)"})
    return applied, skipped
