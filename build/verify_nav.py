# -*- coding: utf-8 -*-
"""驗證 left.htm 的所有連結都指向真實檔案，且沒有孤兒頁面。"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "Resource"
ADD_RE = re.compile(r'd\.add\(\s*\d+\s*,\s*-?\d+\s*,\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)')


def main():
    nav = (RES / "left.htm").read_text(encoding="utf-8")
    links = []
    for m in ADD_RE.finditer(nav):
        u = m.group(2)
        if u:
            links.append(u)

    missing = [l for l in links if not (RES / l).exists()]
    print(f"導航連結        {len(links)}")
    print(f"檔案不存在      {len(missing)}")
    for m in missing[:20]:
        print("   X", m)

    files = {
        str(p.relative_to(RES)).replace("\\", "/")
        for p in RES.rglob("*.htm")
        if p.name not in ("left.htm", "___left.htm")
    }
    orphan = sorted(files - set(links))
    print(f"\n實體 .htm       {len(files)}")
    print(f"未被導航收錄    {len(orphan)}")
    for o in orphan[:20]:
        print("   ?", o)

    # 重複連結
    from collections import Counter
    dup = {k: v for k, v in Counter(links).items() if v > 1}
    print(f"\n重複連結        {len(dup)}")
    for k, v in list(dup.items())[:10]:
        print(f"   {v}x  {k}")

    ok = not missing and not orphan and not dup
    print("\n" + ("✅ 全部通過" if ok else "⚠️ 有問題需處理"))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
