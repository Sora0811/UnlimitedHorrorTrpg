# -*- coding: utf-8 -*-
"""把 Resource/234/ 的 1172 頁重新組織成「規則 / 資源 / 未開放」三塊。

設計原則
--------
1. 檔名一律不動（保留與原始 CHM 的一對一追溯性；README §6 已定此原則）。
2. 目錄名用繁體，取自 left.htm 的繁體標題（該檔已是繁體，不重跑 s2t）。
3. 導航樹的唯一來源是 left.htm 的 dTree.add()，不是檔案系統。
4. 先 dry-run 產出對照表，人工核對後才加 --apply 真的搬。

用法
----
    python build/restructure.py            # 只輸出計畫
    python build/restructure.py --apply    # 真的搬
"""
import json
import re
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "Resource" / "234"
RES = ROOT / "Resource"

# dTree.add(id, parentId, "標題", "檔名")
ADD_RE = re.compile(
    r'd\.add\(\s*(\d+)\s*,\s*(-?\d+)\s*,\s*"((?:[^"\\]|\\.)*)"\s*,\s*"((?:[^"\\]|\\.)*)"\s*\)'
)

# 頂層章節 -> (輸出目錄, 顯示名)
BLOCKS = {
    "規則部分": ("規則", "規則"),
    "資源部分": ("資源", "資源"),
    "怪物圖鑑": ("規則/ST幫助", "ST幫助"),
    "暫不開放": ("未開放", "未開放"),
}

# 章節目錄名標準化（left.htm 是繁體，這裡只是把冗長的名字收短一點）
DIR_RENAME = {
    "建立人物卡來參與遊戲": "建立人物卡",
    "特殊規則部分": "特殊規則",
    "戰鬥相關": "戰鬥",
    "其他相關": "其他規則",
    "本書說明": "本書說明",
    "關鍵字速查": "關鍵字速查",
    "傳奇輪迴者": "傳奇輪迴者",
    "遊戲玩法": "遊戲玩法",
    "序": "序",
}


def parse_nav():
    """解析 left.htm，回傳 [{'id','parent','title','file'}]（保持檔案順序＝樹的順序）。"""
    text = (SRC / "left.htm").read_text(encoding="utf-8", errors="replace")
    nodes = []
    for m in ADD_RE.finditer(text):
        nodes.append({
            "id": int(m.group(1)),
            "parent": int(m.group(2)),
            "title": m.group(3).replace('\\"', '"'),
            "file": m.group(4).replace('\\"', '"'),
        })
    return nodes


def build_tree(nodes):
    """由 parent 關係展開每個節點的路徑（標題鏈）。"""
    by_id = {n["id"]: n for n in nodes}
    for n in nodes:
        path, cur, guard = [], n, 0
        while cur is not None and guard < 20:
            path.append(cur["title"])
            cur = by_id.get(cur["parent"]) if cur["parent"] != -1 else None
            guard += 1
        n["path"] = list(reversed(path))
    return nodes


ROOT_TITLE = "無限恐怖規則"   # dTree 的虛擬根，不是真章節


def target_dir(node):
    """節點 -> 目標目錄（相對於 Resource/）。"""
    path = [p for p in node["path"] if p != ROOT_TITLE]
    top = path[0] if path else ""

    if top in ("怪物圖鑑",):
        # 怪物圖鑑整章搬進 規則/ST幫助；怪物種類當子目錄
        sub = path[2] if len(path) >= 3 else ""
        return "規則/ST幫助" + (f"/{sub}" if sub else "")

    if top == "暫不開放":
        return "未開放"

    if top == "規則部分":
        if len(path) < 2:
            return "規則"
        sec = DIR_RENAME.get(path[1], path[1])
        return f"規則/{sec}"

    if top == "資源部分":
        if len(path) < 2:
            return "資源"
        sec = DIR_RENAME.get(path[1], path[1])
        return f"資源/{sec}"

    return "未分類"


def main():
    apply = "--apply" in sys.argv
    nodes = build_tree(parse_nav())

    # 只取「有實際檔案」的節點
    plan = []
    seen = set()
    for n in nodes:
        f = n["file"]
        if not f or not f.lower().endswith((".htm", ".html")):
            continue
        src = SRC / f
        if not src.exists():
            continue          # $$unsavedpage 等分組節點
        if f in seen:
            print(f"  ! 重複檔名，只取第一個：{f}")
            continue
        seen.add(f)

        d = target_dir(n)
        plan.append({
            "file": f,
            "from": str(src.relative_to(ROOT)).replace("\\", "/"),
            "to": f"{d}/{f}".replace("//", "/"),
            "title": n["title"],
            "section": d,
            "path": n["path"],
        })

    # 未在導航中的頁面
    orphans = []
    for p in sorted(SRC.glob("*.htm")):
        if p.name not in seen and p.name not in ("left.htm", "___left.htm"):
            orphans.append(p.name)

    # --- 統計 ---
    from collections import Counter
    cnt = Counter(x["to"].rsplit("/", 1)[0] for x in plan)
    print(f"=== 搬移計畫：{len(plan)} 頁 ===\n")
    for d, c in sorted(cnt.items()):
        print(f"  {d:<28} {c:>4} 頁")

    if orphans:
        print(f"\n=== 未在導航中（不搬）：{len(orphans)} ===")
        for o in orphans:
            print(f"  - {o}")

    # --- 輸出清單 ---
    out = ROOT / "build" / "move-plan.json"
    out.write_text(
        json.dumps({"pages": plan, "orphans": orphans}, ensure_ascii=False, indent=1),
        encoding="utf-8",
    )
    print(f"\n對照表已寫入 {out.relative_to(ROOT)}")

    if not apply:
        print("\n（dry-run，未動任何檔案。加 --apply 才會真的搬。）")
        return

    # --- 實際搬移 ---
    print("\n=== 開始搬移 ===")
    moved = 0
    for item in plan:
        src = ROOT / item["from"]
        dst = RES / item["to"]
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            print(f"  ! 目標已存在，略過：{item['to']}")
            continue
        shutil.move(str(src), str(dst))
        moved += 1
    print(f"已搬移 {moved} 頁")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
