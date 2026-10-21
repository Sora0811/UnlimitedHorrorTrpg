# -*- coding: utf-8 -*-
"""從拆分前的 left.htm 取出「原始 CHM 編排順序」，供 gen_nav_fs.py 使用。

來源：git 歷史 aca6893 的 Resource/234/left.htm（作者原始目錄的完整樹）。
輸出：build/original-order.json —— { "分類路徑": ["子項1","子項2",...] }

只有在需要重新取得原始順序時才跑這個；平時不需要。
"""
import json
import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMMIT = "aca6893"
NAV_PATH = "Resource/234/left.htm"

RENAME = {
    "規則部分": "規則",
    "資源部分": "資源",
    "暫不開放": "未開放",
    "怪物圖鑑": "ST幫助",
}


def main():
    out = subprocess.run(
        ["git", "show", f"{COMMIT}:{NAV_PATH}"],
        capture_output=True, cwd=ROOT,
    )
    if out.returncode != 0:
        print("取不到原始導航：", out.stderr.decode("utf-8", "replace"))
        return

    text = out.stdout.decode("utf-8", errors="replace")
    rows = [(int(a), int(b), t, f)
            for a, b, t, f in re.findall(r'd\.add\((\d+),(-?\d+),"([^"]*)","([^"]*)"\)', text)]
    by_id = {r[0]: r for r in rows}

    def chain(i):
        o, c, g = [], by_id.get(i), 0
        while c and g < 20:
            o.append(c[2])
            c = by_id.get(c[1]) if c[1] != -1 else None
            g += 1
        return list(reversed(o))

    order = {}
    for r in rows:
        ch = chain(r[0])
        if ch and ch[0] == "無限恐怖規則":
            ch = ch[1:]
        if len(ch) < 2:
            continue
        parent = "/".join(RENAME.get(p, p) for p in ch[:-1])
        order.setdefault(parent, [])
        if ch[-1] not in order[parent]:
            order[parent].append(ch[-1])

    dst = ROOT / "build" / "original-order.json"
    dst.write_text(json.dumps(order, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已輸出 {dst.relative_to(ROOT)}（{len(order)} 個分類）")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
