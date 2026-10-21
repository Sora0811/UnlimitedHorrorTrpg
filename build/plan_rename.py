# -*- coding: utf-8 -*-
"""產生檔名重整對照表（dry-run，不動任何檔案）。

原則
----
導航標題才是權威，檔名只是 CHM 編輯器留下的佔位名。
因此：**新檔名 = 導航標題**，只有以下情況例外

  A. 標題是純「總綱」        -> <父分類名>-總綱
  B. 標題含 Windows 非法字元  -> 以 _ 取代
  C. 同目錄內撞名            -> 標記為 CONFLICT，不自動編號，交由人工決定
  D. 標題與現有檔名相同      -> 不列入（無需改名）

輸出
----
build/rename-plan.json   給 apply_rename.py 用
build/rename-review.md   人類審閱用
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "Resource"

ILLEGAL = set('/\\:*?"<>|')
ZONGGANG = "總綱"

# 檔名長度上限（Windows 路徑考量）
MAXLEN = 80


def safe(name: str) -> str:
    out = "".join("_" if c in ILLEGAL else c for c in name)
    out = out.strip().rstrip(". ")
    return out[:MAXLEN]


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


def main():
    rows = load_nav()

    # 現存檔名（依目錄分組），用於偵測撞名
    existing = defaultdict(set)
    for p in RES.rglob("*.htm"):
        existing[str(p.parent)].add(p.stem)

    plan = []
    for r in rows:
        f = r["file"]
        if not f:
            continue
        path = RES / f
        stem = path.stem
        parent = str(path.parent)
        title = r["title"].strip()
        chain = [c for c in r["chain"] if c]

        if not title:
            continue

        # 規則 A：純「總綱」加上父分類
        if title == ZONGGANG:
            pname = chain[-2] if len(chain) >= 2 else ""
            new = safe(f"{pname}-總綱") if pname and pname != ZONGGANG else safe(title)
        else:
            new = safe(title)

        if not new or new == stem:
            continue

        # 撞名偵測：目標檔名（不等於自己）是否已被同目錄的其他檔案佔用
        others = existing[parent] - {stem}
        conflict = new in others

        # 也可能是「另一頁改名後」撞上，先記錄，稍後統一檢查
        plan.append({
            "from": f,
            "to": str(Path(f).parent / (new + ".htm")).replace("\\", "/"),
            "old_stem": stem,
            "new_stem": new,
            "title": title,
            "chain": " / ".join(chain[1:]),
            "conflict": conflict,
        })

    # 第二輪：偵測「兩頁改成同一個新檔名」
    by_target = defaultdict(list)
    for p in plan:
        by_target[p["to"]].append(p)
    for tgt, items in by_target.items():
        if len(items) > 1:
            for p in items:
                p["conflict"] = True

    # 第三輪：改名後是否與「不打算改名的既有檔案」撞名
    stay = set()
    renamed_stems = {(p["from"].rsplit("/", 1)[0], p["old_stem"]) for p in plan}
    for d, stems in existing.items():
        for s in stems:
            if (d.replace("/", "\\"), s) not in renamed_stems and (d, s) not in renamed_stems:
                stay.add((d, s))
    plan_stems = {(p["to"].rsplit("/", 1)[0], p["new_stem"]) for p in plan}
    for p in plan:
        if (p["to"].rsplit("/", 1)[0], p["new_stem"]) in stay:
            p["conflict"] = True

    ok = [p for p in plan if not p["conflict"]]
    bad = [p for p in plan if p["conflict"]]

    (ROOT / "build" / "rename-plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8"
    )

    lines = ["# 檔名重整對照表", "",
             f"- 總計 **{len(plan)}** 筆",
             f"- 可直接執行 **{len(ok)}** 筆",
             f"- 需人工決定 **{len(bad)}** 筆", ""]
    if bad:
        lines += ["## ⚠️ 需人工決定（撞名）", "", "| 舊檔名 | 目標 | 導航路徑 |", "|---|---|---|"]
        for p in bad:
            lines.append(f"| `{Path(p['from']).name}` | `{p['new_stem']}.htm` | {p['chain']} |")
        lines.append("")
    lines += ["## ✅ 可直接執行", "", "| 舊檔名 | 新檔名 | 導航路徑 |", "|---|---|---|"]
    for p in sorted(ok, key=lambda x: x["from"]):
        lines.append(f"| `{Path(p['from']).name}` | `{Path(p['to']).name}` | {p['chain']} |")
    lines.append("")
    (ROOT / "build" / "rename-review.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"對照表 {len(plan)} 筆")
    print(f"  可直接執行 {len(ok)}")
    print(f"  需人工決定 {len(bad)}")
    if bad:
        print("\n=== 撞名清單 ===")
        for p in bad:
            print(f"  {p['from']:<46} -> {p['new_stem']:<24} [{p['chain']}]")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
