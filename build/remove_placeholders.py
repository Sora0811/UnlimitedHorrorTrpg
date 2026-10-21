# -*- coding: utf-8 -*-
"""移除 WinCHM 空佔位頁（只有 "New Topic (Press Shift-Enter...)"，無實質內文）。

處理方式：
  - 把原檔備份到 _chm源檔/placeholders/（保留原路徑結構）
  - 從 Resource 刪除該 index.html
  - 目錄本身保留 → 導航自動變成「可展開但不可點」的分類節點

用法：
    python build/remove_placeholders.py            # 只列出
    python build/remove_placeholders.py --apply    # 真的刪
"""
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "Resource"
BACKUP = RES / "_chm源檔" / "placeholders"

STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
TITLE_RE = re.compile(r"<title>(.*?)</title>", re.S | re.I)
PLACEHOLDER_RE = re.compile(
    r"New\s*Topic\s*\(?Press Shift-Enter to insert a line break.*?return\.\)",
    re.S | re.I,
)


def plain(html: str) -> str:
    t = STYLE_RE.sub(" ", html)
    t = TAG_RE.sub(" ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", t).strip()


def is_placeholder(html: str) -> bool:
    """去掉標題與佔位句後，若幾乎沒有內容，就是佔位頁。"""
    text = plain(html)
    if "Press Shift-Enter" not in text:
        return False
    title_m = TITLE_RE.search(html)
    title = title_m.group(1).strip() if title_m else ""

    rest = PLACEHOLDER_RE.sub(" ", text)
    rest = re.sub(r"Press Enter to add a paragraph return\.?\)?", " ", rest, flags=re.I)
    rest = re.sub(r"\(?\s*\)?$", " ", rest)
    if title:
        rest = rest.replace(title, " ")
    rest = re.sub(r"\s+", " ", rest).strip(" :：()（）.&nbsp; .")
    return len(rest) <= 2


def main():
    apply = "--apply" in sys.argv
    targets = []
    for p in sorted(RES.rglob("index.html")):
        if "_chm源檔" in p.parts:
            continue
        try:
            html = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if is_placeholder(html):
            targets.append(p)

    print(f"=== 佔位頁：{len(targets)} 個 ===\n")
    for p in targets:
        print(f"  {p.relative_to(RES).as_posix()}")

    if not apply:
        print("\n（dry-run，加 --apply 才會刪除）")
        return

    print("\n=== 備份並刪除 ===")
    for p in targets:
        rel = p.relative_to(RES)
        dst = BACKUP / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        p.unlink()
    print(f"已備份到 {BACKUP.relative_to(ROOT)}")
    print(f"已刪除 {len(targets)} 個 index.html")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
