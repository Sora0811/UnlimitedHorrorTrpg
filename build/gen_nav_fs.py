# -*- coding: utf-8 -*-
"""掃描實體結構，重建 left.htm。

以「實際存在的資料夾樹」為唯一來源，保證導航與檔案系統一致。
顯示順序由 ORDER 決定，不依賴資料夾名稱排序——資料夾叫什麼不影響它在導航上的位置。

規則：
  - 含 index.html 的資料夾 = 條目節點（連結指向該目錄）
  - 同時含 index.html 與子資料夾 = 分類節點（可點，且有子節點）
  - 未列在 ORDER 的分類，排在已列出者之後，並依名稱自然排序
"""
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "Resource"
VIRTUAL_ROOT = "無限恐怖規則"

# 頂層章節顯示順序
TOP_ORDER = ["規則", "資源", "未開放"]

# 各層分類的顯示順序（未列出的排在後面，依名稱自然排序）
ORDER = {
    "": ["規則", "資源", "未開放"],
    "規則": ["本書說明", "序", "遊戲玩法", "建立人物卡來參與遊戲", "戰鬥相關",
             "其他相關", "特殊規則部分", "關鍵字速查", "傳奇輪迴者", "ST幫助"],
    "資源": ["總綱", "血統", "改造", "瞳術", "稱號", "流派", "技藝",
             "修真", "魔導書", "能量池", "物品", "專長"],
}

SKIP = {"img", "_chm源檔"}


def natural_key(s):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", s)]


def sort_dirs(parent_rel: str, dirs):
    """依 ORDER 指定的順序排，未指定的接在後面。"""
    pref = ORDER.get(parent_rel, [])
    idx = {n: i for i, n in enumerate(pref)}
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
