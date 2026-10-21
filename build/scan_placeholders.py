# -*- coding: utf-8 -*-
"""掃描全站，找出 WinCHM 的空佔位頁。

佔位特徵：內文出現 "New Topic" 或 "Press Shift-Enter to insert a line break"。
這類頁面除了標題之外沒有任何實質內容。

分級：
  EMPTY      完全沒有內文（只剩佔位句）
  HAS_TITLE  有標題但內文是空的
  REAL       有實質內文（列出供對照）
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "Resource"

PLACEHOLDER_MARKS = [
    "Press Shift-Enter to insert a line break",
    "New Topic",
]

STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)


def plain_text(html: str) -> str:
    t = STYLE_RE.sub(" ", html)
    t = TAG_RE.sub(" ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", t).strip()


def main():
    rows = []
    for p in sorted(RES.rglob("index.html")):
        try:
            html = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        text = plain_text(html)
        title_m = TITLE_RE.search(html)
        title = title_m.group(1).strip() if title_m else ""

        hit = [m for m in PLACEHOLDER_MARKS if m in text]
        if not hit:
            continue

        # 去掉佔位句與標題後，還剩多少字？
        rest = text
        for m in PLACEHOLDER_MARKS:
            rest = rest.replace(m, " ")
        rest = re.sub(r"\(.*?insert a line break.*?\)", " ", rest)
        rest = re.sub(r"\(Press.*?\)", " ", rest)
        if title:
            rest = rest.replace(title, " ")
        rest = re.sub(r"\s+", " ", rest).strip(" :：")

        rows.append({
            "path": p.relative_to(RES).as_posix(),
            "size": p.stat().st_size,
            "title": title,
            "residue": len(rest),
            "residue_text": rest[:60],
        })

    print(f"=== 含佔位句的頁面：{len(rows)} 個 ===\n")
    for r in rows:
        kind = "EMPTY" if r["residue"] == 0 else ("HAS_TITLE" if r["residue"] < 5 else "REAL")
        print(f"[{kind:<9}] {r['size']:>7} b  {r['path']}")
        print(f"             標題={r['title']!r}  殘留={r['residue']}  {r['residue_text']!r}")

    empty = [r for r in rows if r["residue"] == 0]
    print(f"\n可安全移除（無殘留內文）：{len(empty)} 個")
    print(f"需保留（有內文）：{len(rows) - len(empty)} 個")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
