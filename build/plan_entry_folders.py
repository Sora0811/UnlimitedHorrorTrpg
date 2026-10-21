# -*- coding: utf-8 -*-
"""把每個條目搬進自己的資料夾（以完整分類路徑為位置，內容存為 index.html）。

目的：一個條目 = 一個資料夾，日後的分支、補丁、變體、版本註記都放在同一層。

    Resource/資源/血統/人類/禍神血脈/index.html
    Resource/資源/血統/人類/禍神血脈/patch-2.34.yml     ← 日後
    Resource/資源/血統/人類/禍神血脈/variant-私規.html  ← 日後

用法
    python build/plan_entry_folders.py           # 只輸出計畫
    python build/plan_entry_folders.py --apply   # 實際搬移
"""
import json
import re
import shutil
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "Resource"
ILLEGAL = set('/\\:*?"<>|')
RESERVED = {
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}
MAX_SEG = 60      # 單一資料夾名稱長度上限


def safe(seg: str) -> str:
    out = "".join("_" if c in ILLEGAL else c for c in seg)
    out = out.strip().rstrip(". ").replace("\u3000", " ")
    while "  " in out:
        out = out.replace("  ", " ")
    if len(out) > MAX_SEG:
        out = out[:MAX_SEG].rstrip(". ")
    if out.upper() in RESERVED:
        out = out + "_"
    return out or "_"


def load_nav():
    nav = (RES / "left.htm").read_text(encoding="utf-8")
    rows = []
    for a, b, t, f in re.findall(r'd\.add\((\d+),(-?\d+),"([^"]*)","([^"]*)"\)', nav):
        rows.append({"id": int(a), "pid": int(b), "title": t, "file": f})
    by_id = {r["id"]: r for r in rows}
    for r in rows:
        chain, cur, g = [], r, 0
        while cur and g < 30:
            chain.append(cur["title"])
            cur = by_id.get(cur["pid"]) if cur["pid"] != -1 else None
            g += 1
        r["chain"] = list(reversed(chain))
    return rows


def build_plan(rows):
    plan = []
    used = {}          # 小寫路徑 -> 原始路徑（Windows 不分大小寫）
    for r in rows:
        f = r["file"]
        if not f:
            continue
        ch = [x for x in r["chain"] if x]
        if ch and ch[0] == "無限恐怖規則":
            ch = ch[1:]
        if not ch:
            continue
        parts = [safe(x) for x in ch]

        # 小寫撞名檢查
        key = "/".join(parts).lower()
        if key in used:
            print(f"  ! 大小寫撞名：{'/'.join(parts)}  vs  {used[key]}")
            parts[-1] = parts[-1] + "_" + safe(Path(f).stem)[:8]
            key = "/".join(parts).lower()
        used[key] = "/".join(parts)

        plan.append({
            "from": f,
            "to": "/".join(parts) + "/index.html",
            "title": r["title"],
            "chain": " / ".join(ch),
            "original": Path(f).name,
        })
    return plan


def main():
    apply = "--apply" in sys.argv
    rows = load_nav()
    plan = build_plan(rows)

    by_top = defaultdict(int)
    for p in plan:
        by_top[p["to"].split("/")[0]] += 1
    print(f"=== 計畫：{len(plan)} 個條目 → 各自一個資料夾 ===\n")
    for k, v in sorted(by_top.items()):
        print(f"  {k:<10} {v:>5}")
    print(f"\n總資料夾數（含分類）約 {len({p['to'].rsplit('/',1)[0] for p in plan}) + len({d for p in plan for d in _prefixes(p['to'])})}")

    out = ROOT / "build" / "entry-plan.json"
    out.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"對照表已寫入 {out.relative_to(ROOT)}")

    if not apply:
        print("\n（dry-run）範例：")
        for p in plan[:3] + plan[len(plan) // 2: len(plan) // 2 + 3]:
            print(f"  {p['from']}\n    → {p['to']}")
        return

    print("\n=== 開始搬移 ===")
    moved = 0
    for p in plan:
        src = RES / p["from"]
        dst = RES / p["to"]
        if not src.exists():
            print(f"  ! 來源不存在：{p['from']}")
            continue
        if dst.exists():
            print(f"  ! 目標已存在：{p['to']}")
            continue
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(src), str(dst))
        moved += 1
    print(f"已搬移 {moved} 個條目")


def _prefixes(path):
    segs = path.split("/")[:-1]
    for i in range(1, len(segs) + 1):
        yield "/".join(segs[:i])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
