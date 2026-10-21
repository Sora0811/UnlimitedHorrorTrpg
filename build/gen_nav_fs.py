# -*- coding: utf-8 -*-
"""掃描實體結構，重建 left.htm。

以「實際存在的資料夾樹」為唯一來源，保證導航與檔案系統一致。
顯示順序由 ORDER 決定，不依賴資料夾名稱——資料夾叫什麼不影響它在導航上的位置。

ORDER 的值有兩種寫法：
  - 清單 ["a","b"]        → 這幾個照此順序排在前面，其餘接在後面（自然排序）
  - None                  → 全部照 **原始 CHM 編排順序**（從 .hhc 取），其餘接在後面
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "Resource"
VIRTUAL_ROOT = "無限恐怖規則"

TOP_ORDER = ["規則", "資源", "未開放"]

# 顯示順序。key 為「相對於 Resource 的資料夾路徑」
ORDER = {
    "": ["規則", "資源", "未開放"],

    # 規則：ST 幫助移最後
    "規則": ["本書說明", "序", "遊戲玩法", "建立人物卡來參與遊戲", "戰鬥相關",
             "其他相關", "特殊規則部分", "關鍵字速查", "傳奇輪迴者", "ST幫助"],

    # 資源：專長提前、流派併入稱號、物品放最後
    "資源": ["總綱", "血統", "改造", "瞳術", "稱號", "技藝", "修真",
             "魔導書", "能量池", "專長", "物品"],

    # 專長：從規則搬過來的分組照指定順序排前面，再來超魔／輪迴隊
    "資源/專長": ["建卡專長", "生理系專長", "心智系專長", "互動系專長",
                  "戰鬥系專長", "缺陷、天賦和怪癖", "超魔專長", "輪迴隊專長"],

    # 建立人物卡：建卡 → 屬性概述 → 技能概述 → 技能（三個子系）→ 專長概述
    "規則/建立人物卡來參與遊戲": ["建卡", "屬性概述", "技能概述", "技能", "專長概述"],

    # 技能：概述已上移，這裡只放三個子系
    "規則/建立人物卡來參與遊戲/技能": ["生理系技能", "心智系技能", "互動系技能"],

    # 物品：原始編排順序（載具 → 科技本質 → 魔幻本質 → 特異本質 → 防具 → 附魔改造 → 劇情物品）
    "資源/物品": ["載具", "科技本質", "魔幻本質", "特異本質", "防具",
                  "物品附魔與改造", "劇情物品"],
}

# 未在 ORDER 指定的分類：照原始 CHM 編排順序（由 build/export_original_order.py 產出）
ORIG_PATH = Path(__file__).resolve().parent / "original-order.json"
ORIGINAL_ORDER = {}
if ORIG_PATH.exists():
    import json
    ORIGINAL_ORDER = json.loads(ORIG_PATH.read_text(encoding="utf-8"))

SKIP = {"img", "_chm源檔"}


def natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def sort_dirs(parent_rel: str, dirs):
    """排序優先序：ORDER 明確指定 > 原始 CHM 編排 > 自然排序。"""
    pref = ORDER.get(parent_rel)
    if pref is not None:
        idx = {n: i for i, n in enumerate(pref)}
        known = sorted([d for d in dirs if d.name in idx], key=lambda d: idx[d.name])
        rest = sorted([d for d in dirs if d.name not in idx], key=lambda d: natural_key(d.name))
        return known + rest

    # 沒有明確指定 -> 照原始 CHM 編排
    orig = ORIGINAL_ORDER.get(parent_rel, [])
    idx = {n: i for i, n in enumerate(orig)}
    known = sorted([d for d in dirs if d.name in idx], key=lambda d: idx[d.name])
    rest = sorted([d for d in dirs if d.name not in idx], key=lambda d: natural_key(d.name))
    return known + rest


def main():
    out = []
    nid = 0

    def add(title, pid, url=""):
        nonlocal nid
        i = nid
        nid += 1
        out.append((i, pid, title, url))
        return i

    def subdirs(d: Path):
        try:
            return [c for c in d.iterdir()
                    if c.is_dir() and not c.name.startswith(".") and c.name not in SKIP]
        except OSError:
            return []

    def walk(d: Path, parent_id: int, rel: str):
        for c in sort_dirs(rel, subdirs(d)):
            child_rel = f"{rel}/{c.name}" if rel else c.name
            has_idx = (c / "index.html").is_file()
            url = child_rel + "/" if has_idx else ""
            node = add(c.name, parent_id, url)
            if subdirs(c):
                walk(c, node, child_rel)

    root = add(VIRTUAL_ROOT, -1)
    for top in TOP_ORDER:
        d = RES / top
        if not d.is_dir():
            continue
        url = f"{top}/" if (d / "index.html").is_file() else ""
        node = add(top, root, url)
        walk(d, node, top)

    lines = [
        '<html>', '<head>', '    <meta charset="utf-8">',
        '    <link rel="StyleSheet" href="dtree.css" type="text/css" />',
        '    <script type="text/javascript" src="dtree.js"></script>',
        '</head>', '<body>', '<div class="dtree">',
        '<p><a href="javascript: d.openAll();">open all</a> | '
        '<a href="javascript: d.closeAll();">close all</a></p>',
        '<script type="text/javascript"><!--',
        'd = new dTree("d");',
    ]
    for i, pid, t, u in out:
        lines.append(f'd.add({i},{pid},"{t.replace(chr(34), chr(92) + chr(34))}","{u}");')
    lines += ['document.write(d);', '//-->', '</script>', '</div>', '</body>', '</html>']

    (RES / "left.htm").write_text("\n".join(lines) + "\n", encoding="utf-8")
    linked = sum(1 for _, _, _, u in out if u)
    print(f"已寫入 Resource/left.htm：{len(out)} 節點，{linked} 條目連結")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
