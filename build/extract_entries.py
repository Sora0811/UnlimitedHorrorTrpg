# -*- coding: utf-8 -*-
"""資源條目抽取器 —— v3（小樣本驗證）。

資料模型
--------
一條目（entry）
├─ name          條目名稱
├─ fields        本體欄位（本質、描述、特殊…）
└─ levels[]      各等級
   ├─ level      D / C / B / A / S / AA…
   ├─ label      「D級：賽亞人」後面的名稱
   ├─ fields     該等級的欄位（屬性、能量池、技能樹、特性…）
   ├─ price      該等級的價格（可為 None，代表套預設值）
   └─ tree[]     該等級開啓的技能樹能力
      ├─ name
      ├─ fields  （價格、動作、能耗、射程、目標、範圍、持續、描述、效果…）
      └─ price

分段規則
--------
1. 先依 ==== 分隔線切成大塊（清單頁）
2. 每塊內部再用「段落邊界」細分：
   - 等級標題：段首的「D級」，且後面接欄位或冒號
   - 技能樹標題：段首的「D級OO技能樹：」，必須有冒號
   - 能力起點：「名稱 價格：」
3. 技能樹能力歸屬到「最近的前一個等級」

設計原則
--------
- 只讀不寫
- 不硬猜；無法解析的標記 null + warning
- branch 保留原始字串（"DD"、"AA"）
"""
import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "Resource"

STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
SEP_RE = re.compile(r"={10,}")

FIELD_NAMES = [
    "名稱", "本質", "價格", "前提", "等級", "階級", "屬性", "能量池", "能量",
    "技能樹", "特性", "描述", "備註", "分類", "體積", "標籤",
    "武器基本屬性", "武器特殊屬性", "能耗", "動作", "射程", "目標", "範圍",
    "持續", "效果", "威力值", "增幅", "研發", "升級", "從屬", "關鍵字", "專業", "特殊",
]
FIELD_RE = re.compile(
    r"(" + "|".join(sorted(FIELD_NAMES, key=len, reverse=True)) + r")\s*[：:]"
)

# 技能樹標題：必須在段首，形如「D級寫輪眼技能樹：」
# 名稱至少 1 字——否則「C級 技能樹：開啓C級…」這種等級標題會被誤判為技能樹
TREE_RE = re.compile(r"(?:^|(?<=[。；\s]))([DCBAS]{1,3})\s*級\s*([^\s：:]{1,20}?)技能樹\s*[：:]")
# 等級標題：段首的「D級」
LEVEL_RE = re.compile(r"(?:^|(?<=[。；\s]))([DCBAS]{1,3})\s*級")
# 能力起點：名稱 + 價格：
ABILITY_RE = re.compile(r"([^\s：:，、。；=]{2,20})\s*價格\s*[：:]")

FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＳＥＸＰ＋（）",
    "0123456789ABCDSEXP+()",
)


def plain(html: str) -> str:
    t = STYLE_RE.sub(" ", html)
    t = TAG_RE.sub(" ", t)
    t = t.replace("&nbsp;", " ")
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", t).strip()


def norm(s: str) -> str:
    return s.translate(FULLWIDTH).strip()


def parse_price(raw: str):
    """價格 → {branch, points, xp}。三種組合：支線+分數 / 純XP / 三者並存。"""
    s = norm(raw).replace(" ", "")
    if not s:
        return None, "價格為空"

    m = re.fullmatch(r"(\d+)[xX][pP]", s)
    if m:
        return {"branch": None, "points": None, "xp": int(m.group(1))}, None

    m = re.fullmatch(r"([DCBAS]{1,3})?\+?(\d+)?(?:\+(\d+)[xX][pP])?", s)
    if m and (m.group(1) or m.group(2)):
        br, pts, xp = m.group(1), m.group(2), m.group(3)
        return {
            "branch": br,
            "points": int(pts) if pts else None,
            "xp": int(xp) if xp else None,
        }, None

    m = re.fullmatch(r"([DCBAS]{1,3})\+?", s)
    if m:
        return {"branch": m.group(1), "points": None, "xp": None}, "只有支線，缺分數"

    return None, f"無法解析價格: {raw!r}"


def split_fields(segment: str):
    """→ ({欄位: 值}, [warning])"""
    fields, warnings = {}, []
    marks = [(m.start(), m.group(1), m.end()) for m in FIELD_RE.finditer(segment)]
    for i, (pos, name, vstart) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(segment)
        value = segment[vstart:end].strip(" ：:")
        if name in fields:
            warnings.append(f"欄位重複：{name}")
            continue
        fields[name] = value
    return fields, warnings


def find_marks(text: str):
    """找出段落邊界，回傳排序後的 [{pos, kind, level, name}]。"""
    marks = []

    for m in TREE_RE.finditer(text):
        marks.append({"pos": m.start(), "kind": "tree",
                      "level": m.group(1), "name": m.group(2).strip()})
    for m in ABILITY_RE.finditer(text):
        if m.group(1) in FIELD_NAMES:
            continue
        # 排除「欄位：值 價格：」這種情形——能力名稱前面不該是冒號
        if m.start() > 0 and text[m.start() - 1] in "：:":
            continue
        marks.append({"pos": m.start(), "kind": "ability",
                      "level": None, "name": m.group(1)})
    for m in LEVEL_RE.finditer(text):
        pos = m.start()
        if any(abs(pos - k["pos"]) < 3 for k in marks):
            continue
        # 等級標題後面必須接：欄位標籤、冒號、或數字/名稱
        after = text[m.end():m.end() + 10]
        if not after:
            continue
        after_strip = after.lstrip(" ：:")
        ok = (
            any(after_strip.startswith(f) for f in FIELD_NAMES)
            or after[:1] in "：:"
        )
        if not ok:
            continue
        marks.append({"pos": pos, "kind": "level",
                      "level": m.group(1), "name": None})

    marks.sort(key=lambda x: x["pos"])
    prio = {"tree": 0, "ability": 1, "level": 2}
    dedup = []
    for m in marks:
        if dedup and m["pos"] - dedup[-1]["pos"] < 3:
            if prio[m["kind"]] < prio[dedup[-1]["kind"]]:
                dedup[-1] = m
            continue
        dedup.append(m)
    return dedup


def build_entry(block: str, name_hint: str = None):
    """把一大塊文字建成 entry（含 levels 與 tree）。"""
    entry = {
        "name": name_hint,
        "fields": {},
        "levels": [],
        "price": None,
        "warnings": [],
    }

    marks = find_marks(block)
    if not marks:
        f, w = split_fields(block)
        entry["fields"] = f
        entry["warnings"] += w
        p, pw = parse_price(f.get("價格", "")) if "價格" in f else (None, None)
        entry["price"] = p
        if pw:
            entry["warnings"].append(pw)
        return entry

    # 第一個邊界前 = 本體
    if marks[0]["pos"] > 5:
        f, w = split_fields(block[: marks[0]["pos"]])
        entry["fields"] = f
        entry["warnings"] += w
        if "價格" in f:
            p, pw = parse_price(f["價格"])
            entry["price"] = p
            if pw:
                entry["warnings"].append(pw)

    current_level = None
    current_tree_level = None   # 技能樹區塊所屬的等級（依字母配對）

    def level_for(letter):
        """找出該等級字母對應的 level 物件；找不到就新建。"""
        for lv in entry["levels"]:
            if lv["level"] == letter:
                return lv
        lv = {"level": letter, "label": "", "fields": {}, "price": None,
              "tree": [], "warnings": ["技能樹區塊無對應等級，已自動建立"]}
        entry["levels"].append(lv)
        return lv

    for i, mk in enumerate(marks):
        end = marks[i + 1]["pos"] if i + 1 < len(marks) else len(block)
        seg = block[mk["pos"]:end]
        f, w = split_fields(seg)

        if mk["kind"] == "level":
            current_level = {
                "level": mk["level"],
                "label": "",
                "fields": f,
                "price": None,
                "tree": [],
                "warnings": list(w),
            }
            # 標題後面若有名稱（D級：賽亞人）
            m = re.match(r"[DCBAS]{1,3}\s*級\s*[：:]\s*(.{1,30}?)(?=\s|$)", seg)
            if m:
                current_level["label"] = m.group(1).strip()
            if "價格" in f:
                p, pw = parse_price(f["價格"])
                current_level["price"] = p
                if pw:
                    current_level["warnings"].append(pw)
            entry["levels"].append(current_level)
            current_tree_level = None

        elif mk["kind"] == "tree":
            # 技能樹區塊：依「等級字母」配對到對應等級，而非最近出現的等級
            current_tree_level = level_for(mk["level"])
            current_tree_level["tree_name"] = f"{mk['level']}級{mk['name']}技能樹"

        elif mk["kind"] == "ability":
            ability = {
                "name": mk["name"],
                "fields": f,
                "price": None,
                "warnings": list(w),
            }
            if "價格" in f:
                p, pw = parse_price(f["價格"])
                ability["price"] = p
                if pw:
                    ability["warnings"].append(pw)
            else:
                ability["warnings"].append("能力缺價格")

            # 歸屬：優先用技能樹區塊所屬等級，其次才是當前等級
            target = current_tree_level or current_level
            if target is not None:
                target["tree"].append(ability)
            else:
                entry.setdefault("abilities", []).append(ability)

    return entry


def analyze_page(path: Path):
    text = plain(path.read_text(encoding="utf-8", errors="replace"))
    rel = path.parent.relative_to(RES).as_posix()
    page = {"path": rel, "chars": len(text), "entries": [], "warnings": []}

    chunks = []
    cursor = 0
    for m in SEP_RE.finditer(text):
        chunks.append(text[cursor:m.start()])
        cursor = m.end()
    chunks.append(text[cursor:])

    for chunk in chunks:
        chunk = chunk.strip()
        if len(chunk) < 20:
            continue
        # 標題 = 第一個欄位標籤之前
        first = FIELD_RE.search(chunk)
        hint = chunk[: first.start()].strip()[:40] if first else chunk[:40]
        page["entries"].append(build_entry(chunk, hint))

    return page


def summarize(page):
    print("=" * 76)
    print(f"{page['path']}   ({page['chars']} 字, {len(page['entries'])} 條目)")
    print("=" * 76)
    for e in page["entries"]:
        print(f"\n▸ 條目：{e['name']!r}")
        for k, v in e["fields"].items():
            print(f"    {k}: {v[:55]}")
        if e.get("price"):
            print(f"    [價格] {e['price']}")
        for lv in e.get("levels", []):
            print(f"  ├─ 等級 {lv['level']}  {lv.get('label','')}")
            for k, v in lv["fields"].items():
                print(f"  │    {k}: {v[:50]}")
            if lv.get("price"):
                print(f"  │    [價格] {lv['price']}")
            if lv.get("tree_name"):
                print(f"  │    ▸ {lv['tree_name']}")
            for ab in lv.get("tree", []):
                pr = ab["price"]
                if pr:
                    tag = (pr.get("branch") or "")
                    tag += f"+{pr['points']}" if pr.get("points") else ""
                    tag += f"{pr['xp']}XP" if pr.get("xp") else ""
                else:
                    tag = "⚠無價格"
                print(f"  │      • {ab['name']:<8} {tag}")
        for ab in e.get("abilities", []):
            print(f"  ├─ 能力 {ab['name']}  {ab['price']}")
        for w in e["warnings"]:
            print(f"    ⚠ {w}")


def main():
    samples = [
        "資源/瞳術/寫輪眼/index.html",
        "資源/物品/科技本質/D級系列武器/D級手槍系列/index.html",
        "資源/血統/非人類/特異本質/外星生物/賽亞人/index.html",
    ]
    out = []
    for s in samples:
        p = RES / s
        if not p.exists():
            continue
        page = analyze_page(p)
        out.append(page)
        summarize(page)
        print()

    dst = ROOT / "build" / "extract-sample.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已寫入 {dst.relative_to(ROOT)}")

    # 統計
    print("\n=== 統計 ===")
    for page in out:
        n_ab = sum(len(lv.get("tree", [])) for e in page["entries"] for lv in e.get("levels", []))
        n_ab += sum(len(e.get("abilities", [])) for e in page["entries"])
        n_lv = sum(len(e.get("levels", [])) for e in page["entries"])
        print(f"  {page['path']}: {n_lv} 等級, {n_ab} 能力")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
