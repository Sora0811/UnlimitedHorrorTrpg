# -*- coding: utf-8 -*-
"""把 build/extract-all.json 轉成可讀的 Markdown 資源書。

用法：
    python build/export_md.py                       # 轉全部（資源區）
    python build/export_md.py --path 資源/血統/黑暗生物/骷髏   # 只轉指定頁
    python build/export_md.py --out DIR            # 指定輸出目錄
    python build/export_md.py --section 資源        # 只轉某分區

設計：
- 一個來源頁 → 一個 .md 檔，放在輸出目錄的對應相對路徑下
- 等級 → 二級標題，能力 → 三級標題，變體 → 三級標題
- 欄位 → 定義列表（**欄位**：值）
- fields_extra_multi 另起一節「其他標籤」，保留原標籤
- notes 中的自身名稱重複項會略過
"""
import argparse
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "build/md"


def fmt_price(p):
    """price dict → 可讀字串。"""
    if not p:
        return None
    br = p.get("branch")
    pts = p.get("points")
    xp = p.get("xp")
    if br and pts is not None:
        return f"{br}+{pts}"
    if xp is not None:
        return f"{xp} XP"
    return None


def md_escape(s):
    """轉義會破壞 Markdown 的字元。"""
    if s is None:
        return ""
    s = str(s)
    s = s.replace("\\", "\\\\")
    s = re.sub(r"([*_`])", r"\\\1", s)
    s = re.sub(r"^(\s*)([-+*]\s)", r"\1\\\2", s)
    s = re.sub(r"^(\s*)(\d+)\.", r"\1\\\2.", s)
    return s


def md_inline(s):
    """單行值：轉義 + 把換行換成 <br>（避免變成巢狀列表）。"""
    s = md_escape(str(s).strip())
    return s.replace("\n", "<br>")


def render_fields(fields, out, skip=()):
    """輸出欄位。

    單值欄位 → `**欄位**：值`
    多值欄位 → `**欄位**` 下縮排列出（避免「（續）」重複標籤）
    """
    if not fields:
        return
    for k, vals in fields.items():
        if k in skip or not vals:
            continue
        if len(vals) == 1:
            out.append(f"**{md_escape(k)}**：{md_inline(vals[0])}")
            out.append("")
        else:
            out.append(f"**{md_escape(k)}**")
            out.append("")
            out.append("<ul>")
            for v in vals:
                out.append(f"<li>{md_inline(v)}</li>")
            out.append("</ul>")
            out.append("")


def render_extra(extra, out):
    """非 schema 標籤另起一節。

    用定義列表式（「**標籤**：值」）而非 `- ` 列表：原文常有「某某：」結尾
    後接子項，連續列表會被 Markdown 誤判成巢狀結構。
    """
    if not extra:
        return
    out.append("**其他標籤**")
    out.append("")
    for k, vals in extra.items():
        if len(vals) == 1:
            out.append(f"**{md_escape(k)}**：{md_inline(vals[0])}")
            out.append("")
        else:
            out.append(f"**{md_escape(k)}**")
            out.append("")
            out.append("<ul>")
            for v in vals:
                out.append(f"<li>{md_inline(v)}</li>")
            out.append("</ul>")
            out.append("")
    out.append("")


def render_notes(notes, self_name, out):
    """無標籤散文。略過與自身名稱重複者。"""
    kept = []
    seen_self = False
    for n in notes:
        n = (n or "").strip()
        if not n:
            continue
        if self_name and n == self_name and not seen_self:
            seen_self = True
            continue
        kept.append(n)
    if not kept:
        return
    out.append("**正文**")
    out.append("")
    for n in kept:
        out.append(md_inline(n))
        out.append("")


def render_holder(holder, out, self_name=None):
    """條目 / 等級 / 能力 / 變體 共用的欄位輸出。"""
    render_fields(holder.get("fields_multi"), out)
    pr = fmt_price(holder.get("price"))
    if pr:
        out.append(f"**價格**：{pr}")
        out.append("")
    render_extra(holder.get("fields_extra_multi"), out)
    render_notes(holder.get("notes", []), self_name, out)


def render_entry(entry, out, path="", first=False):
    name = (entry.get("name") or "（未命名）").strip()
    out.append(f"# {md_escape(name)}")
    out.append("")
    if path and first:
        out.append(f"> 來源：`{md_escape(path)}`")
        out.append("")

    render_fields(entry.get("fields_multi"), out)
    pr = fmt_price(entry.get("price"))
    if pr:
        out.append(f"**價格**：{pr}")
        out.append("")

    # 等級
    for lv in entry.get("levels", []):
        lname = lv.get("label") or ""
        head = f"## {lv.get('level', '')} 級"
        if lname:
            head += f"　{md_escape(lname)}"
        out.append(head)
        out.append("")
        render_fields(lv.get("fields_multi"), out)
        lpr = fmt_price(lv.get("price"))
        if lpr:
            out.append(f"**價格**：{lpr}")
            out.append("")
        render_extra(lv.get("fields_extra_multi"), out)
        render_notes(lv.get("notes", []), lname, out)

        for ab in lv.get("tree", []):
            an = (ab.get("name") or "").strip()
            out.append(f"### {md_escape(an)}")
            out.append("")
            render_holder(ab, out, an)

    # 不屬於等級的能力
    for ab in entry.get("abilities", []):
        an = (ab.get("name") or "").strip()
        out.append(f"## {md_escape(an)}")
        out.append("")
        render_holder(ab, out, an)

    # 變體（子項目）
    variants = entry.get("variants", [])
    if variants:
        out.append("## 子項目")
        out.append("")
        for v in variants:
            vn = (v.get("name") or "").strip()
            grp = v.get("group")
            head = f"### {md_escape(vn)}"
            if grp:
                head += f"（{md_escape(grp)}）"
            out.append(head)
            out.append("")
            render_holder(v, out, vn)

    render_extra(entry.get("fields_extra_multi"), out)
    render_notes(entry.get("notes", []), name, out)


def render_page(pg, path):
    out = ["---", ""]
    out.append(f"<!-- {path} -->")
    out.append("")
    entries = pg.get("entries", [])
    for i, e in enumerate(entries):
        render_entry(e, out, path, first=(i == 0))
    return "\n".join(out).rstrip() + "\n"


def nav_order():
    """從 Resource/left.htm 讀出導覽順序（id 遞增即顯示順序）。"""
    p = ROOT / "Resource/left.htm"
    if not p.is_file():
        return []
    txt = p.read_text(encoding="utf-8")
    rows = re.findall(r'd\.add\((\d+),(-?\d+),"([^"]*)","([^"]*)"\);', txt)
    by_id = {int(i): (t, u) for i, _p, t, u in rows}
    kids = {}
    for i, pid, t, u in rows:
        kids.setdefault(int(pid), []).append(int(i))
    order = []

    def walk(nid, depth):
        t, u = by_id.get(nid, ("", ""))
        if u:
            order.append((u, depth))
        for c in kids.get(nid, []):
            walk(c, depth + 1)

    root_id = None
    for i, pid, t, u in rows:
        if int(pid) == -1:
            root_id = int(i)
            break
    if root_id is not None:
        walk(root_id, 0)
    else:
        for i, pid, t, u in rows:
            if u:
                order.append((u, 0))
    return order


def build_index(index_lines, out_root, n_pages, n_entries):
    """產生總目錄：依導航順序、分層縮排。"""
    by_path = {p: names for p, names in index_lines}
    order = nav_order()
    idx = ["# 無限恐怖 TRPG 資源書", "",
           f"共 {n_pages} 頁、{n_entries} 條目。", "",
           "目錄順序與網站導航一致。", ""]

    emitted = set()
    for url, depth in order:
        path = url.rstrip("/")
        if path not in by_path:
            continue
        names = by_path[path]
        idx.append(f"{'  ' * depth}- [{path}]({path}.md)")
        for nm in names:
            idx.append(f"{'  ' * depth}  - {md_escape(nm)}")
        emitted.add(path)

    # 導航裡沒有的頁面（保險）
    missing = [p for p in by_path if p not in emitted]
    if missing:
        idx.append("")
        idx.append("## 未列入導航的頁面")
        idx.append("")
        for p in sorted(missing):
            idx.append(f"- [{p}]({p}.md)")
            for nm in by_path[p]:
                idx.append(f"  - {md_escape(nm)}")

    (out_root / "INDEX.md").write_text("\n".join(idx) + "\n", encoding="utf-8")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--src", default=str(ROOT / "build/extract-all.json"))
    ap.add_argument("--out", default=str(DEFAULT_OUT))
    ap.add_argument("--path", default=None, help="只轉這一頁（相對路徑）")
    ap.add_argument("--section", default=None, help="只轉這一分區")
    ap.add_argument("--no-index", action="store_true", help="不產生總目錄")
    args = ap.parse_args()

    data = json.loads(Path(args.src).read_text(encoding="utf-8"))
    out_root = Path(args.out)

    n_pages = 0
    n_entries = 0
    index_lines = []
    for pg in data:
        path = pg.get("path", "")
        if args.path and path != args.path:
            continue
        if args.section and not path.startswith(args.section):
            continue
        md = render_page(pg, path)
        dest = out_root / (path + ".md")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(md, encoding="utf-8")
        n_pages += 1
        n_entries += len(pg.get("entries", []))
        names = [e.get("name") for e in pg.get("entries", []) if e.get("name")]
        index_lines.append((path, names))

    if not args.no_index:
        build_index(index_lines, out_root, n_pages, n_entries)

    print(f"已輸出 {n_pages} 頁 / {n_entries} 條目 → {out_root}")
    if not args.no_index:
        print(f"總目錄 → {out_root / 'INDEX.md'}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
