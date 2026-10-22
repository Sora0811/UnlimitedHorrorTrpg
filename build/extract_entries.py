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


# 價格：取「開頭的價格 token」，其餘文字留在備註欄。
# 舊版用 fullmatch，導致 11% 的價格解析失敗（2026-10-22 實測）。
# 實務上原文常見「D+500 強效施法：…」「C+1000 消耗：見下」這類寫法，
# 價格只是欄位開頭的一小段，後面是別的欄位或說明。
PRICE_LEAD_RE = re.compile(
    r"^\s*(?:"
    r"(?P<br1>[DCBASdcbas]{1,3})\s*\+\s*(?P<pts1>\d+)\s*\+\s*(?P<xp1>\d+)\s*[xX]\s*[pP]"
    r"|(?P<xp0>\d+)\s*[xX]\s*[pP]"
    r"|(?P<br2>[DCBASdcbas]{1,3})\s*\+\s*(?P<pts2>\d+)"
    r"|(?P<pts3>\d+)"
    r"|(?P<br3>[DCBASdcbas]{1,3})\s*\+"
    r")"
)

# 「總價」寫法：「特殊(總價D+2000)」「水晶劍的卷軸總價DD+500」
PRICE_TOTAL_RE = re.compile(
    r"總價\s*[：:]?\s*(?P<br>[DCBASdcbas]{1,3})\s*\+?\s*(?P<pts>\d+)"
)


def parse_price(raw: str):
    """價格 → ({branch, points, xp}, warning, note)

    只取開頭的價格 token；其餘文字原樣放進 note，避免資料蒸發。
    回傳 (price, warning, note)：
      price 為 None 代表完全無法從開頭讀出價格
      note  為價格之後的說明文字（可能為空字串）
    """
    s = norm(raw)
    if not s:
        return None, "價格為空", ""

    m = PRICE_LEAD_RE.match(s)
    if m:
        br = (m.group("br1") or m.group("br2") or m.group("br3") or "").upper() or None
        pts = m.group("pts1") or m.group("pts2") or m.group("pts3")
        xp = m.group("xp1") or m.group("xp0")
        note = s[m.end():].strip()
        # 只讀到支線、沒有分數
        warn = None
        if pts is None and xp is None:
            warn = "只有支線，缺分數"
        return {
            "branch": br,
            "points": int(pts) if pts else None,
            "xp": int(xp) if xp else None,
        }, warn, note

    # 「總價」寫法
    mt = PRICE_TOTAL_RE.search(s)
    if mt:
        return {
            "branch": mt.group("br").upper(),
            "points": int(mt.group("pts")),
            "xp": None,
        }, None, s.strip()

    return None, f"無法解析價格: {raw!r}", s.strip()


def split_fields(segment: str):
    """→ ({欄位: 值}, [warning])

    同一個欄位可能出現多次（例如一個條目有多段「描述」、多個「價格」）。
    舊版遇到重複就直接丟棄，導致 85% 的欄位資料蒸發（2026-10-22 實測）。
    現在保留第一個值當主值，其餘收進「_multi」附表，不再遺失。

    回傳 (fields, warnings, multi)：
      fields[name]          → 第一個出現的值（字串，向後相容）
      multi[name]          → 所有出現的值（list，含第一個）
    """
    fields, warnings, multi = {}, [], {}
    marks = [(m.start(), m.group(1), m.end()) for m in FIELD_RE.finditer(segment)]
    for i, (pos, name, vstart) in enumerate(marks):
        end = marks[i + 1][0] if i + 1 < len(marks) else len(segment)
        value = segment[vstart:end].strip(" ：:")
        if not value:
            continue
        multi.setdefault(name, []).append(value)
        if name in fields:
            # 不丟棄：記錄在 multi 裡，並降級為提示而非警告
            warnings.append(f"欄位重複：{name}（已保留 {len(multi[name])} 個值）")
            continue
        fields[name] = value
    return fields, warnings, multi


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
        "fields_multi": {},
        "levels": [],
        "price": None,
        "warnings": [],
    }

    marks = find_marks(block)
    if not marks:
        f, w, m = split_fields(block)
        entry["fields"] = f
        entry["fields_multi"] = m
        entry["warnings"] += w
        p, pw, note = parse_price(f.get("價格", "")) if "價格" in f else (None, None, "")
        entry["price"] = p
        if pw:
            entry["warnings"].append(pw)
        if note:
            entry["price_note"] = note
        return entry

    # 第一個邊界前 = 本體
    if marks[0]["pos"] > 5:
        f, w, m = split_fields(block[: marks[0]["pos"]])
        entry["fields"] = f
        entry["fields_multi"] = m
        entry["warnings"] += w
        if "價格" in f:
            p, pw, note = parse_price(f["價格"])
            entry["price"] = p
            if pw:
                entry["warnings"].append(pw)
            if note:
                entry["price_note"] = note

    current_level = None
    current_tree_level = None   # 技能樹區塊所屬的等級（依字母配對）

    def level_for(letter):
        """找出該等級字母對應的 level 物件；找不到就新建。"""
        for lv in entry["levels"]:
            if lv["level"] == letter:
                return lv
        lv = {"level": letter, "label": "", "fields": {}, "fields_multi": {},
              "price": None,
              "tree": [], "warnings": ["技能樹區塊無對應等級，已自動建立"]}
        entry["levels"].append(lv)
        return lv

    for i, mk in enumerate(marks):
        end = marks[i + 1]["pos"] if i + 1 < len(marks) else len(block)
        seg = block[mk["pos"]:end]
        f, w, mm = split_fields(seg)

        if mk["kind"] == "level":
            current_level = {
                "level": mk["level"],
                "label": "",
                "fields": f,
                "fields_multi": mm,
                "price": None,
                "tree": [],
                "warnings": list(w),
            }
            # 標題後面若有名稱（D級：賽亞人）
            m = re.match(r"[DCBAS]{1,3}\s*級\s*[：:]\s*(.{1,30}?)(?=\s|$)", seg)
            if m:
                current_level["label"] = m.group(1).strip()
            if "價格" in f:
                p, pw, note = parse_price(f["價格"])
                current_level["price"] = p
                if pw:
                    current_level["warnings"].append(pw)
                if note:
                    current_level["price_note"] = note
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
                "fields_multi": mm,
                "price": None,
                "warnings": list(w),
            }
            if "價格" in f:
                p, pw, note = parse_price(f["價格"])
                ability["price"] = p
                if pw:
                    ability["warnings"].append(pw)
                if note:
                    ability["price_note"] = note
            else:
                ability["warnings"].append("能力缺價格")

            # 歸屬：優先用技能樹區塊所屬等級，其次才是當前等級
            target = current_tree_level or current_level
            if target is not None:
                target["tree"].append(ability)
            else:
                entry.setdefault("abilities", []).append(ability)

    return entry


# ---------------------------------------------------------------- 段落切分
#
# 條目邊界的判斷依據（2026-10-22 實測確立）
# --------------------------------------------
# 原始 HTML 是 <P> 段落序列。條目與條目之間的結構訊號有兩種：
#
#   1. 「====」分隔線 —— 166/1147 頁有，是最可靠的邊界
#   2. 段落邊界 —— 其餘頁靠「名稱段 + 欄位組」隱含切分
#
# 段落規則：
#   候選點 = 非欄位開頭的段落，且下一個非空段落是欄位開頭
#   條目起點 = 候選點，且下一欄位屬於 ENTRY_FIELDS（本質/名稱/稱號/血統）
#   等級起點 = 段落本身是等級標題，或段內含「技能樹：開啓X級」→ 不切新條目
#
# 為什麼要要求「下一欄位是本質」：資源條目一定帶本質（物品、血統、瞳術…），
# 而子項目（魔杖的「樺木」杖身、喪屍的「力量型」分支）只帶價格，不帶本質。

PARA_RE = re.compile(r"<[Pp]\b[^>]*>(.*?)</[Pp]>", re.S)
LEVEL_TITLE_RE = re.compile(r"^\s*([DCBAS]{1,3})\s*級")
TREE_OPEN_RE = re.compile(r"技能樹\s*[：:]\s*開啓\s*([DCBAS]{1,3})\s*級")

# 資源條目的主體欄位——只有帶這些欄位的段落才是「條目起點」
ENTRY_FIELDS = {"本質", "名稱", "稱號", "血統"}

# 條目名稱段的長度上限。超過這個長度多半是描述文字，不是名稱。
# 實測最長的真實名稱是「復仇火銃：失敗保護者的最後懺悔」（17 字）、
# 「名器·恆嶽松雲」（8 字），取 30 留緩衝。
NAME_MAX_LEN = 30


def get_paragraphs(html: str):
    """取出 <P> 段落文字序列，保留段落邊界。"""
    out = []
    for seg in PARA_RE.findall(html):
        t = TAG_RE.sub(" ", seg).replace("&nbsp;", " ")
        out.append(re.sub(r"\s+", " ", t).strip())
    return out


def para_field(t: str):
    """段落開頭的欄位標籤；不是欄位則回 None。"""
    m = re.match(r"\s*([^\s：:]{1,12})\s*[：:]", t)
    if m and m.group(1) in FIELD_NAMES:
        return m.group(1)
    return None


def next_nonempty(paras, i):
    """(索引, 文字)：i 之後第一個非空段落。"""
    for j in range(i + 1, len(paras)):
        if paras[j]:
            return j, paras[j]
    return None, ""


def is_separator(t: str) -> bool:
    """是否為 ==== 分隔線。"""
    return bool(SEP_RE.fullmatch(t.strip()))


def find_entry_starts(paras):
    """段落區塊內的條目起點索引。"""
    cands = []
    for i, t in enumerate(paras):
        if not t or para_field(t) is not None:
            continue
        j, nxt = next_nonempty(paras, i)
        if j is None or para_field(nxt) is None:
            continue
        cands.append(i)

    starts = []
    for k, i in enumerate(cands):
        end = cands[k + 1] if k + 1 < len(cands) else len(paras)
        seg = " ".join(paras[i:end])
        # 等級標題 / 技能樹開啓 → 是等級，不是新條目
        if LEVEL_TITLE_RE.match(paras[i]):
            continue
        if TREE_OPEN_RE.search(seg):
            continue
        # 名稱段不該以標點開頭（那是描述文字被誤判）
        if paras[i][0] in "：:，,。、；;（「『\"'":
            continue
        # 名稱段應該短。描述文字後面剛好接「本質：」時會被誤判成條目起點
        # （例如「劍反：當你受到近戰攻擊時…本质：」），用長度上限排除。
        if len(paras[i]) > NAME_MAX_LEN:
            continue
        j, nxt = next_nonempty(paras, i)
        if para_field(nxt) in ENTRY_FIELDS:
            starts.append(i)
    return starts


def split_page_blocks(html: str):
    """整頁切成條目區塊（list of str）。

    層 1：==== 分隔線切塊
    層 2：塊內用段落規則再切
    """
    paras = get_paragraphs(html)

    raw_blocks, cur = [], []
    for t in paras:
        if is_separator(t):
            raw_blocks.append(cur)
            cur = []
        else:
            cur.append(t)
    raw_blocks.append(cur)

    out = []
    for blk in raw_blocks:
        blk = [t for t in blk if t]
        if not blk:
            continue
        starts = find_entry_starts(blk)
        if starts:
            for k, i in enumerate(starts):
                end = starts[k + 1] if k + 1 < len(starts) else len(blk)
                out.append(" ".join(blk[i:end]))
        else:
            out.append(" ".join(blk))
    return out


def analyze_page(path: Path):
    html = path.read_text(encoding="utf-8", errors="replace")
    text = plain(html)
    rel = path.parent.relative_to(RES).as_posix()
    page = {"path": rel, "chars": len(text), "entries": [], "warnings": []}

    for chunk in split_page_blocks(html):
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
