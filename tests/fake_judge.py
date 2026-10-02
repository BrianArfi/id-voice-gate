#!/usr/bin/env python3
"""Offline stand-in for a real judge, used by the tests and as an example of the
`command` backend: reads the judge prompt on stdin, prints a JSON verdict.

It knows a few translated patterns and their natural rewrites. Anything else is
judged natural. Set FAKE_JUDGE_COUNTER to a file path to count calls, and
FAKE_JUDGE_FAIL=1 to simulate an outage (exit 1).
"""
import json
import os
import re
import sys

FIXES = [
    ("Laporan mingguan, notulen, dan prioritas pagi, dikerjain AI.",
     "AI yang nyusun laporan mingguan, notulen, dan prioritas pagi."),
    ("Notulen dan daftar tugas dikerjain AI.", "AI yang nyusun notulen dan daftar tugas."),
    ("Notulen, daftar tugas, dan laporan mingguan, dikerjain AI.",
     "AI yang nyusun notulen, daftar tugas, dan laporan mingguan."),
    ("Lo tinggal review.", "Lo tinggal cek."),
    ("Tiga langkah, udah.", "Cuma tiga langkah."),
    ("Kenalan sama Notula", "Begini cara kerja Notula"),
    ("Satu tempat buat semua catatan meeting lo.", "Semua catatan meeting lo masuk ke satu folder."),
    ("Pelajari lebih lanjut", "Lihat cara kerjanya"),
    ("Kami adalah layanan di mana pengguna dapat melakukan perekaman meeting secara otomatis.",
     "Layanan kami merekam meeting secara otomatis."),
]


def main():
    if os.environ.get("FAKE_JUDGE_FAIL"):
        sys.stderr.write("fake judge: not logged in\n")
        return 1
    counter = os.environ.get("FAKE_JUDGE_COUNTER")
    if counter:
        with open(counter, "a", encoding="utf-8") as fh:
            fh.write("x")
    prompt = sys.stdin.read()
    m = re.search(r"<text>\n(.*)\n</text>", prompt, re.S)
    text = m.group(1) if m else ""
    rewrite, issues = text, []
    for bad, good in FIXES:
        if bad in rewrite:
            rewrite = rewrite.replace(bad, good)
            issues.append({"quote": bad, "why": "kebaca kayak terjemahan", "fix": good})
    natural = not issues
    print(json.dumps({"natural": natural, "score": 5 if natural else 2, "issues": issues,
                      "rewrite": rewrite}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
