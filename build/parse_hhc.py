# -*- coding: utf-8 -*-
"""解析 CHM 的 .hhc 目錄樹，輸出「頁面 -> 頂層章節」對照表。

.hhc 是 GB2312 編碼的 sitemap-like 檔案：
  <UL><LI><OBJECT type="text/sitemap">
      <param name="Name" value="標題">
      <param name="Local" value="檔名.htm">
  </OBJECT> ...
"""
import io
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
HHC = ROOT / "Resource" / "234" / "无限TRPG核心规则.hhc"
PAGES = ROOT / "Resource" / "234"

TOKEN = re.compile(
    r'<UL>|</UL>|<param\s+name="Name"\s+value="([^"]*)"|<param\s+name="Local"\s+value="([^"]*)"',
    re.I,
)


def parse_hhc():
    text = HHC.read_bytes().decode("gb18030", errors="replace")
    depth = 0
    nodes = []          # 扁平節點清單
    stack = []          # 目前路徑
    pending_name = None

    for m in TOKEN.finditer(text):
        tok = m.group(0).lower()
        if tok == "<ul>":
            depth += 1
            continue
        if tok == "</ul>":
            depth -= 1
            if stack and stack[-1]["depth"] >= depth + 1:
                pass
            continue
        if m.group(1) is not None:
            pending_name = m.group(1)
            continue
        if m.group(2) is not None:
            local = m.group(2).strip()
            node = {
                "depth": depth,
                "title": pending_name or "",
                "local": local,
            }
            nodes.append(node)
            pending_name = None

    return nodes


def attach_paths(nodes):
    """用 depth 重建父子關係，替每個節點標上頂層章節。"""
    path = []  # path[d-1] = 該層的標題
    for n in nodes:
        d = n["depth"]
        if d <= 0:
            continue
        path = path[: d - 1]
        while len(path) < d - 1:
            path.append("?")
        path.append(n["title"])
        n["path"] = list(path[:d])
        n["chapter"] = path[0] if path else "?"
        n["section"] = path[1] if len(path) >= 2 else ""
    return nodes


def main():
    nodes = attach_paths(parse_hhc())

    # 1) 頂層章節統計
    chapters = {}
    for n in nodes:
        chapters.setdefault(n["chapter"], {"nodes": 0, "pages": 0})
        chapters[n["chapter"]]["nodes"] += 1
        if n["local"] and (PAGES / n["local"]).exists():
            chapters[n["chapter"]]["pages"] += 1

    print("=== 頂層章節 ===")
    for k, v in chapters.items():
        print(f"  {k:<12} 節點 {v['nodes']:>5}  實體頁 {v['pages']:>5}")

    # 2) 每個頂層章節底下的二級分類
    print("\n=== 各章底下的二級分類 ===")
    for ch in chapters:
        secs = {}
        for n in nodes:
            if n["chapter"] != ch:
                continue
            secs.setdefault(n["section"], 0)
            if n["local"] and (PAGES / n["local"]).exists():
                secs[n["section"]] += 1
        print(f"\n  [{ch}]  {len(secs)} 個二級分類")
        for s, c in sorted(secs.items(), key=lambda x: -x[1]):
            print(f"    {s:<16} {c:>4} 頁")

    # 3) 對照表：實體頁面 -> (章節, 分類, 標題)
    mapping = {}
    for n in nodes:
        loc = n["local"]
        if not loc:
            continue
        if not (PAGES / loc).exists():
            continue  # $$unsavedpage 之類
        mapping.setdefault(loc, {
            "chapter": n["chapter"],
            "section": n["section"],
            "title": n["title"],
            "path": n["path"],
        })

    out = ROOT / "build" / "page-map.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=1, sort_keys=True),
        encoding="utf-8",
    )

    # 4) 完整性：有沒有實體頁面不在目錄裡
    all_pages = {p.name for p in PAGES.glob("*.htm")} | {p.name for p in PAGES.glob("*.html")}
    orphans = sorted(all_pages - set(mapping))
    print(f"\n=== 對照表 ===")
    print(f"  實體頁面      {len(all_pages)}")
    print(f"  已對應        {len(mapping)}")
    print(f"  未在目錄中    {len(orphans)}")
    for o in orphans[:40]:
        print(f"    - {o}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
