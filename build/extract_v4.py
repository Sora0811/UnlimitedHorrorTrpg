# -*- coding: utf-8 -*-
"""資源條目抽取器 —— v4（段落層級解析）。

v3 的問題（2026-10-22 實測）
--------------------------------
1. 等級標題的「後面必須是欄位或冒號」檢查太嚴
   → 「D 級骷髏兵」被擋掉，等級無法拆出（寫輪眼能拆是因為標題是光禿「D級」）
2. 等級標題不一定有「級」字
   → 「B：中武・高手」「A：高武・破碎虛空」完全沒被偵測，等級直接遺失
3. 欄位解析在「文字流」上做，段落邊界資訊全丟
   → 續行說明段落被誤併進上一個欄位（「魔杖」的「本質」欄位裡混進說明文字）
4. 條目切分會丟資料
   → 玲瓏骰頁開頭 5 段（條目描述）、C級武器 3 段，完全沒進任何條目
5. 欄位標籤是寫死的 33 個白名單，只覆蓋 66.5% 的標記段落
   → 真欄位「成分」「消耗」「持續時間」「防禦」「攻擊」…全被當成正文
6. 同名欄位只留第一個 → 85% 欄位資料蒸發

v4 的做法
--------------------------------
- 一切以「段落」為單位解析，保留原始邊界
- 欄位值可以跨多段：欄位之後的續行段落併進該欄位
- 等級標題支援五種寫法（實測全站 1171 頁）：
    「D級」「D 級：名稱」「D 級 名稱」「D 級名稱」「B：名稱」
- 欄位標籤依「跨資源使用範圍」分層（見 build/field_schema.json）：
    core     用於 ≥30 頁 → schema 欄位
    extended 用於 5–29 頁 → schema 欄位
    <5 頁者 → 單一資源專屬，不升級為 schema 欄位，
              改存進 fields_extra_multi（保留原標籤，不污染 schema）
- 輸出只留 multi 陣列（fields_multi / fields_extra_multi），砍掉單值欄位
- 子項目（魔杖的杖身/杖芯）拆成頂層 variants[]，格式與條目相同

設計原則
--------------------------------
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
PARA_RE = re.compile(r"<[Pp]\b[^>]*>(.*?)</[Pp]>", re.S)

FULLWIDTH = str.maketrans(
    "０１２３４５６７８９ＡＢＣＤＳＥＸＰ＋（）",
    "0123456789ABCDSEXP+()",
)

# ---------------------------------------------------------------- 欄位詞典

# 內建核心欄位（保底；field_schema.json 存在時以它為準）
BUILTIN_FIELDS = [
    "名稱", "本質", "價格", "前提", "等級", "階級", "屬性", "能量池",
    "技能樹", "特性", "描述", "備註", "分類", "體積", "標籤",
    "武器基本屬性", "武器特殊屬性", "能耗", "動作", "射程", "目標", "範圍",
    "持續", "效果", "威力值", "增幅", "研發", "升級", "從屬", "關鍵字",
    "專業", "特殊", "消耗", "成分", "持續時間", "防禦", "攻擊",
    "盔甲基本屬性", "盔甲特殊屬性", "被動", "專長", "進化", "認主",
    "加值轉換", "速度", "選開", "技能", "總描述", "例", "注",
]

# 別名 → 正規名（統一用詞）
FIELD_ALIASES = {
    "能量": "能量池",
    "成份": "成分",
    "耗能": "能耗",
    "持續時間": "持續",
    "關鍵詞": "關鍵字",
    "威力": "威力值",
    "武器基礎屬性": "武器基本屬性",
    "先決條件": "先決",
    "施法時間": "施展時間",
}

# 外觀像標籤但其實是等級標題／內容量子，不當欄位
NOT_FIELDS = {
    "D級", "C級", "B級", "A級", "S級", "D", "C", "B", "A", "S",
    "DD級", "CC級", "BB級", "AA級", "SS級",
    "1級", "2級", "3級", "4級", "5級",
    "1", "2", "3", "4", "5", "PS",
}

_SCHEMA = None


def load_schema():
    """載入欄位詞典（可版本控制，讓維護者能調整欄位範圍）。"""
    global _SCHEMA
    if _SCHEMA is not None:
        return _SCHEMA
    path = ROOT / "build" / "field_schema.json"
    fields = set(BUILTIN_FIELDS)
    aliases = dict(FIELD_ALIASES)
    level_fields = set(LEVEL_FIELDS)
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            fields |= set(data.get("core", []))
            fields |= set(data.get("extended", []))
            aliases.update(data.get("aliases", {}))
            lf = data.get("level_fields") or {}
            if isinstance(lf, dict):
                level_fields |= set(lf.get("items", []))
            elif isinstance(lf, list):
                level_fields |= set(lf)
        except (OSError, ValueError):
            pass
    _SCHEMA = {"fields": fields, "aliases": aliases, "not_fields": NOT_FIELDS,
               "level_fields": level_fields}
    return _SCHEMA


def is_field_label(label: str) -> bool:
    """這個標籤是不是 schema 欄位？"""
    s = load_schema()
    if label in s["not_fields"]:
        return False
    return s["aliases"].get(label, label) in s["fields"]


def canon_field(label: str) -> str:
    return load_schema()["aliases"].get(label, label)


# ---------------------------------------------------------------- 價格

PRICE_LEAD_RE = re.compile(
    r"^\s*(?:"
    r"(?P<br1>[DCBASdcbas]{1,3})\s*\+\s*(?P<pts1>\d+)\s*\+\s*(?P<xp1>\d+)\s*[xX]\s*[pP]"
    r"|(?P<xp0>\d+)\s*[xX]\s*[pP]"
    r"|(?P<br2>[DCBASdcbas]{1,3})\s*\+\s*(?P<pts2>\d+)"
    r"|(?P<pts3>\d+)"
    r"|(?P<br3>[DCBASdcbas]{1,3})\s*\+"
    r")"
)

PRICE_TOTAL_RE = re.compile(
    r"總價\s*[：:]?\s*(?P<br>[DCBASdcbas]{1,3})\s*\+?\s*(?P<pts>\d+)"
)


def parse_price(raw: str):
    """價格 → ({branch, points, xp}, warning, note)

    只取開頭的價格 token；其餘文字原樣放進 note，避免資料蒸發。
    """
    s = raw.translate(FULLWIDTH).strip()
    if not s:
        return None, "價格為空", ""

    m = PRICE_LEAD_RE.match(s)
    if m:
        br = (m.group("br1") or m.group("br2") or m.group("br3") or "").upper() or None
        pts = m.group("pts1") or m.group("pts2") or m.group("pts3")
        xp = m.group("xp1") or m.group("xp0")
        note = s[m.end():].strip()
        warn = None
        if pts is None and xp is None:
            warn = "只有支線，缺分數"
        return {
            "branch": br,
            "points": int(pts) if pts else None,
            "xp": int(xp) if xp else None,
        }, warn, note

    mt = PRICE_TOTAL_RE.search(s)
    if mt:
        return {
            "branch": mt.group("br").upper(),
            "points": int(mt.group("pts")),
            "xp": None,
        }, None, s.strip()

    return None, f"無法解析價格: {raw!r}", s.strip()


# ---------------------------------------------------------------- 段落解析

# 段落開頭的「標籤：」——標籤限制在 10 字以內、不含標點
LABEL_RE = re.compile(
    r"^\s*([^\s：:，,。、；;（）()【】\[\]「」『』…—\-]{1,10}?)\s*[：:]")

# 等級標題的五種寫法
LEVEL_PATTERNS = [
    re.compile(r"^\s*([DCBAS]{1,3})\s*級\s*[：:]\s*(.*)$"),   # X級：名稱
    re.compile(r"^\s*([DCBAS]{1,3})\s*級\s+(.*)$"),           # X級 名稱
    re.compile(r"^\s*([DCBAS]{1,3})\s*[：:]\s*(.*)$"),         # X：名稱
]
LEVEL_ONLY_RE = re.compile(r"^\s*([DCBAS]{1,3})\s*級\s*$")
LEVEL_TIGHT_RE = re.compile(r"^\s*([DCBAS]{1,3})\s*級\s*([^\s：:]{1,20})\s*$")

# 技能樹標題：「D級寫輪眼技能樹：」「B級港漫武者技能樹」
TREE_RE = re.compile(
    r"^\s*([DCBAS]{1,3})\s*級\s*([^\s：:]{0,20}?)\s*技能樹\s*[：:]?\s*$")

# 「名稱 價格：」同一段
ABILITY_RE = re.compile(r"^\s*([^\s：:，、。；=]{2,20})\s*價格\s*[：:]")

# 名稱段的長度上限
NAME_MAX_LEN = 30
ABILITY_NAME_MAX = 20
LEVEL_NAME_MAX = 16

# 「詞 數字」的數值堆疊（「風度 6 操控 6 沉着 11」）
NUM_PAIR_RE = re.compile(r"[一-鿿]\s*[+\-]?\d+")

# 描述「等級本身」的欄位：出現這些欄位，代表前面的名稱段落是等級而非能力
LEVEL_FIELDS = {"屬性", "能量池", "技能樹", "特性", "特殊"}

# 施放型欄位：能力（法術/招式/異能/專長）會有這些，物品子項目不會
CAPABILITY_FIELDS = {
    "動作", "效果", "能耗", "升級", "升階", "專業", "威力值", "消耗",
    "加成", "增幅", "主動", "被動", "主動效果", "被動效果", "目標", "範圍",
    "持續", "射程", "前提", "先決", "前置", "距離", "專長", "技能",
}

# 資源條目的主體欄位——只有帶這些欄位的段落才是「條目起點」
ENTRY_FIELDS = {"本質", "名稱", "稱號", "血統"}

# 群組標題含這些字 → 底下是「能力列表」而非「物品子項目」
CAPABILITY_GROUP_KW = (
    "技能樹", "異能", "法術", "專長", "秘技", "招式", "戰技", "秘術",
    "真言", "奧義", "靈術", "符咒", "神通", "武技", "能力",
)


def plain(html: str) -> str:
    t = STYLE_RE.sub(" ", html)
    t = TAG_RE.sub(" ", t)
    t = t.replace("&nbsp;", " ")
    t = t.replace("&amp;", "&").replace("&lt;", "<").replace("&gt;", ">")
    return re.sub(r"\s+", " ", t).strip()


def get_paragraphs(html: str):
    """取出 <P> 段落文字序列，保留段落邊界。

    少數頁面（CHM 壓縮後）會把整個條目塞進單一 <P>，
    內部用半形空格分隔「等級標題：」與「欄位標籤：」。
    這裡把這類段落再切開，切點規則見 _split_para()。
    """
    out = []
    for seg in PARA_RE.findall(html):
        t = TAG_RE.sub(" ", seg).replace("&nbsp;", " ")
        t = re.sub(r"\s+", " ", t).strip()
        if not t:
            continue
        out.extend(_split_para(t))
    return out


_SPLIT_RE = None


def _split_re():
    """段內切點的正則（延遲建立，因為需要 schema 詞典）。"""
    global _SPLIT_RE
    if _SPLIT_RE is None:
        fields = sorted((f for f in load_schema()["fields"] if len(f) >= 2),
                        key=len, reverse=True)
        alt = "|".join(fields)
        # 空格 + ( 等級標題「X 級：」 | schema 欄位「標籤：」)
        _SPLIT_RE = re.compile(
            r"(?<=\s)(?=(?:[DCBAS]{1,3}\s*級\s*[：:]|(?:" + alt + r")\s*[：:]))")
    return _SPLIT_RE


def _split_para(t: str):
    """把段內混排的標籤切成子段落。沒有切點就原樣回傳。"""
    cuts = [m.start() for m in _split_re().finditer(t)]
    if len(cuts) < 2:
        return [t]
    parts = []
    prev = 0
    for i in cuts:
        chunk = t[prev:i].strip()
        if chunk:
            parts.append(chunk)
        prev = i
    chunk = t[prev:].strip()
    if chunk:
        parts.append(chunk)
    return parts


def any_label(t: str):
    """段落開頭的任意「標籤：」。回傳 (標籤, 值起始位置)。"""
    m = LABEL_RE.match(t)
    if not m:
        return None
    return m.group(1), m.end()


def para_label(t: str):
    """段落開頭的 schema 欄位標籤；不是 schema 欄位則回 None。"""
    info = any_label(t)
    if not info:
        return None
    lab, vstart = info
    if not is_field_label(lab):
        return None
    return lab, vstart


def looks_like_label(name: str) -> bool:
    """這段文字像「名稱」而不像「句子」？"""
    if not name:
        return False
    if len(name) > LEVEL_NAME_MAX:
        return False
    if name[0] in "：:，,。、；;（「『\"'【":
        return False
    if any(ch in name for ch in "。？！.?!，,；;"):
        return False
    return True


def level_heading(t: str):
    """段落是等級標題？→ (字母, 名稱) 或 None。

    排除正文裡的「A ：強力攻擊插件。使用這把手槍…」這類冒號句
    ——名稱太長、且含句號，就不是等級名。
    """
    if is_tree_heading(t):
        return None
    for pat in LEVEL_PATTERNS:
        m = pat.match(t)
        if m:
            name = m.group(2).strip()
            # 冒號後可以是空的（「D 級：」），此時沒有等級名
            if name and not looks_like_label(name):
                return None
            return m.group(1), name
    m = LEVEL_ONLY_RE.match(t)
    if m:
        return m.group(1), ""
    m = LEVEL_TIGHT_RE.match(t)
    if m:
        name = m.group(2).strip()
        if not looks_like_label(name):
            return None
        return m.group(1), name
    return None


def is_separator(t: str) -> bool:
    return bool(SEP_RE.fullmatch(t.strip()))


def is_tree_heading(t: str) -> bool:
    return bool(TREE_RE.match(t))


def is_name_para(t: str) -> bool:
    """短、無欄位標籤、不是標題 → 可能是條目/變體/能力的名稱。

    名稱不含句讀：實測 2741 個「含句讀的短段落」幾乎全是散文
    （「戰場上的怨靈呼喚着新兵，期待着他的加入」），不是名稱。
    能力名含的是「-」「・」而非「，」。
    """
    if not t or para_label(t) or level_heading(t) or is_tree_heading(t):
        return False
    if any_label(t):
        return False
    if len(t) > ABILITY_NAME_MAX:
        return False
    if t[0] in "：:，,。、；;（「『\"'【":
        return False
    # 名稱不含句讀（散文特徵）
    if any(ch in t for ch in "，。；！？!?,,"):
        return False
    # 數值堆疊的段落不是名稱（「風度 6 操控 6 沉着 11」「感受 5」）
    if NUM_PAIR_RE.search(t):
        return False
    # 整段就是一個數字或「詞+數字」→ 不是名稱
    if re.fullmatch(r"[^\s]+\s*[+\-]?\d+", t):
        return False
    return True


def is_group_header(t: str) -> bool:
    """段落是不是「群組標題」——整段只有「xxx：」，後面沒內容。

    實測（魔杖的杖身/杖芯）：
      [16] 杖身：        ← 群組標題
      [17] 樺木           ← 變體名稱
      [18] 價格：D+500   ← 變體的價格

    判準：
      - 整段是「短標籤：」且冒號後無內容
      - 標籤不在 schema 詞典（schema 欄位的空值仍算欄位）
      - 標籤不是等級代號
      - 不是技能樹標題（「D級港漫強者技能樹：」長得像群組標題，但不是）
    """
    if is_tree_heading(t):
        return False
    info = any_label(t)
    if not info:
        return False
    lab, vstart = info
    if t[vstart:].strip():
        return False                       # 有內容 → 是欄位，不是群組標題
    if lab in load_schema()["not_fields"]:
        return False                       # 等級代號
    if is_field_label(lab):
        return False                       # schema 欄位的空值 → 仍當欄位
    if len(lab) > 12:
        return False
    return True


# ---------------------------------------------------------------- 容器


def make_holder(name=None):
    """條目／等級／能力／變體 共用的容器。"""
    return {
        "name": name,
        "fields_multi": {},
        "fields_extra_multi": {},
        "notes": [],
        "levels": [],
        "abilities": [],
        "variants": [],
        "price": None,
        "warnings": [],
    }


def apply_price(holder, fields_multi):
    """從欄位裡抓價格，解析後寫進 holder。"""
    vals = fields_multi.get("價格")
    if not vals:
        return
    p, warn, note = parse_price(vals[0])
    holder["price"] = p
    if warn:
        holder["warnings"].append(warn)
    if note:
        holder["price_note"] = note


def merge_fields(holder, fields_multi, fields_extra_multi):
    """把欄位併進容器。"""
    for k, vals in fields_multi.items():
        holder["fields_multi"].setdefault(k, []).extend(vals)
    for k, vals in fields_extra_multi.items():
        holder["fields_extra_multi"].setdefault(k, []).extend(vals)


# 獨立的價格段落（「D+600」整段就是價格）
STANDALONE_PRICE_RE = re.compile(r"^\s*([DCBAS]{1,3})\s*\+\s*(\d+)\s*$", re.I)


def _guess_level(paras, k):
    """等級區塊沒有「X 級」前綴時，從區塊內的獨立價格段推斷等級代號。

    86 血統的排版：「新兵」/「D+600」/「屬性：…」→ 等級 D。
    """
    m = _find_standalone_price(paras, k, min(len(paras), k + 8))
    if m is None:
        return None
    return STANDALONE_PRICE_RE.match(paras[m]).group(1).upper()


def _find_standalone_price(paras, start, end):
    """區塊裡第一個獨立價格段（「D+600」整段就是價格）的索引，沒有則 None。"""
    for j in range(start, min(end, len(paras))):
        t = paras[j]
        if t and STANDALONE_PRICE_RE.match(t):
            return j
    return None


def _has_standalone_price(paras, start, end):
    return _find_standalone_price(paras, start, end) is not None


# ---------------------------------------------------------------- 欄位收集


def collect_fields(paras, start, end):
    """收集 [start, end) 段落的欄位 → (fields_multi, fields_extra_multi, notes)

    - 「schema 標籤：值」開頭的段落 → schema 欄位
    - 「非 schema 標籤：值」開頭的段落 → extra 欄位（保留原標籤，不升級）
    - 續行段落（前一段有欄位）→ 併進該欄位
    - 其餘段落（沒有任何前文欄位可接）→ notes（散敘文字，不能丟）
    """
    fields = {}
    extra = {}
    notes = []
    current = None            # 目前收集中的欄位（schema）
    current_extra = None      # 目前收集中的欄位（extra）
    for t in paras[start:end]:
        if not t:
            continue
        info = any_label(t)
        if info:
            lab, vstart = info
            value = t[vstart:].strip()
            if is_field_label(lab):
                name = canon_field(lab)
                current, current_extra = name, None
                fields.setdefault(name, [])
                if value:
                    fields[name].append(value)
            else:
                current, current_extra = None, lab
                extra.setdefault(lab, [])
                if value:
                    extra[lab].append(value)
        else:
            # 續行段落 → 併進前一個欄位
            if current is not None:
                fields[current].append(t)
            elif current_extra is not None:
                extra[current_extra].append(t)
            else:
                notes.append(t)
    return fields, extra, notes


# ---------------------------------------------------------------- 條目切分


def next_nonempty(paras, i):
    for j in range(i + 1, len(paras)):
        if paras[j]:
            return j, paras[j]
    return None, ""


def find_entry_starts(paras):
    """段落區塊內的條目起點索引。

    候選點 = 非欄位開頭、也不是等級標題的段落，且下一個非空段落是欄位開頭
    條目起點 = 候選點，且下一欄位 ∈ {本質, 名稱, 稱號, 血統}
    """
    cands = []
    for i, t in enumerate(paras):
        if not t or para_label(t) is not None or level_heading(t):
            continue
        if is_tree_heading(t):
            continue
        j, nxt = next_nonempty(paras, i)
        if j is None or para_label(nxt) is None:
            continue
        cands.append(i)

    starts = []
    for i in cands:
        # 名稱段不該以標點開頭（那是描述文字被誤判）
        if paras[i][0] in "：:，,。、；;（「『\"'":
            continue
        # 名稱段應該短。描述文字後面剛好接「本質：」時會被誤判成條目起點
        # （例如「劍反：當你受到近戰攻擊時…本质：」），用長度上限排除。
        if len(paras[i]) > NAME_MAX_LEN:
            continue
        # 前一個非空段落是群組標題（例如「杖身：」）→ 這是變體名稱，不是條目起點
        prev = None
        for k in range(i - 1, -1, -1):
            if paras[k]:
                prev = paras[k]
                break
        if prev is not None and is_group_header(prev):
            continue
        j, nxt = next_nonempty(paras, i)
        if para_label(nxt) and canon_field(para_label(nxt)[0]) in ENTRY_FIELDS:
            starts.append(i)
    return starts


def split_page_blocks(html: str):
    """整頁切成條目區塊（list of list[str]，每個內部是段落序列）。

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
            # 段落 0 到第一個起點之前是「頁首說明」，也要保留（不能丟資料）
            head = blk[:starts[0]]
            for k, i in enumerate(starts):
                end = starts[k + 1] if k + 1 < len(starts) else len(blk)
                seg = blk[i:end]
                if head and k == 0:
                    seg = head + seg
                    head = []
                out.append(seg)
            if head:
                out.append(head)
        else:
            out.append(blk)
    return out


# ---------------------------------------------------------------- 條目建構


def build_from_paras(paras, name_hint):
    """由段落序列建立一個條目（含 levels / tree / variants）。

    結構辨識：
      - 等級標題     → 開一個 level
      - 技能樹標題   → 標記目前 tree 區塊所屬的等級
      - 群組標題     → 空值標籤（如「杖身：」），其下的項目是 variants
      - 名稱段       → 能力（在技能樹下）或變體（在群組標題下）
      - 其他         → 欄位（續行段落併進前一欄位）

    實作：先預掃描出每個「名稱段」的區塊邊界，再單趟走過段落序列。
    這樣才能區分「名稱段 + 後續欄位」和「散落的說明段落」。
    """
    entry = make_holder(name_hint)

    n = len(paras)

    # ---------------------------------------------------- 預掃描：區塊邊界
    #
    # 區塊終止於：等級標題 / 技能樹標題 / 群組標題 / 下一個名稱段。
    # 名稱段 = 短名稱段，且其後（到下一個名稱段為止）含有 schema 欄位。
    breaks = {}          # breaks[k] = True 表示第 k 段是終止點
    for k in range(n):
        t = paras[k]
        if not t:
            continue
        if level_heading(t) or is_tree_heading(t) or is_group_header(t):
            breaks[k] = True

    name_idx = set()
    entry_name = (name_hint or "").strip()
    for k in range(n):
        t = paras[k]
        if not t or breaks.get(k):
            continue
        # 區塊的第一段是條目名稱本身，不是能力/變體
        if k == 0 and entry_name and t.strip() == entry_name:
            continue
        if not is_name_para(t):
            continue
        # 其後的區塊裡有沒有 schema 欄位？
        end = k + 1
        has = False
        while end < n:
            if breaks.get(end):
                break
            if paras[end] and is_name_para(paras[end]):
                break
            if any_label(paras[end]):
                has = True
                break
            end += 1
        if has:
            name_idx.add(k)
            breaks[k] = True       # 名稱段本身是區塊終止點

    def block_end(start):
        """名稱段 start 的區塊結束位置（不含）。"""
        j = start + 1
        while j < n:
            if breaks.get(j):
                return j
            j += 1
        return n

    def item_name_at(k):
        """第 k 段是名稱段時回傳名稱，否則 None。"""
        if k not in name_idx:
            return None
        t = paras[k]
        m = ABILITY_RE.match(t)
        if m:
            return m.group(1).strip()
        return t.strip()

    # ---------------------------------------------------- 等級區塊
    #
    # 兩種等級的排版：
    #   (A) 「D 級骷髏兵」→ 等級標題，下一個名稱段是副標，
    #       欄位（屬性/能量池/技能樹/特性/特殊）屬於等級本身
    #   (B) 「新兵」→ 名稱段 + 獨立價格段 + 等級欄位，沒有「X 級」前綴
    #
    # 判準：名稱段的區塊裡出現「等級欄位」，那它就是等級而不是能力。
    # 技能樹內的能力不會有這些欄位，所以不會誤判。
    #
    # 「特殊」「增幅」這類欄位能力也會有，所以要求：
    #   (1) 至少命中 2 個等級欄位，且
    #   (2) 其中至少一個是「屬性 / 能量池 / 技能樹」——能力不會有這三個。
    level_fields = load_schema()["level_fields"]
    strong_level_fields = level_fields & {"屬性", "能量池", "技能樹"}
    level_blocks = {}          # 名稱段索引 → 等級名
    _level_price_para = {}     # 名稱段索引 → 獨立價格段的索引
    for k in sorted(name_idx):
        f, _, _ = collect_fields(paras, k + 1, block_end(k))
        hit = level_fields & set(f)
        if len(hit) < 2 or not (strong_level_fields & hit):
            continue
        # 區塊裡有獨立的價格段（「D+600」）→ 這是等級的價位，
        # 而且區塊內只有「第一個」名稱段是等級名，後續的是散文。
        #   「新兵」/散文/散文/「D+600」/「屬性：…」→ 等級名 = 新兵
        if _has_standalone_price(paras, k, block_end(k)):
            level_blocks[k] = paras[k].strip()
            _level_price_para[k] = _find_standalone_price(paras, k, block_end(k))
        else:
            level_blocks[k] = paras[k].strip()

    # ---------------------------------------------------- 群組 → 變體對應
    #
    # 群組標題（「杖身：」）管住其後連續的一串名稱段。
    # 說明性群組（「深海僕從屬性如下：」）底下是散文、沒有價格，
    # 不會被當成變體群組。
    #
    # 但「血族異能說明：」底下是異能（能力），不是變體。
    # 區分靠欄位種類：能力有施放型欄位（動作/效果/能耗/射程/目標…），
    # 變體（物品子項目）只有「價格」加物品型欄位（分類/本質/體積/屬性…）。
    group_map = {}
    for gk in range(n):
        t = paras[gk]
        if not t or not is_group_header(t):
            continue
        gname = t.rstrip("：:").strip()
        is_cap_group = any(k in gname for k in CAPABILITY_GROUP_KW)
        j = gk + 1
        while j < n:
            if not paras[j]:
                j += 1
                continue
            if level_heading(paras[j]) or is_tree_heading(paras[j]) \
                    or is_group_header(paras[j]):
                break
            if j in name_idx:
                if j in level_blocks:
                    break
                f, _, _ = collect_fields(paras, j + 1, block_end(j))
                if "價格" in f and not is_cap_group \
                        and not (CAPABILITY_FIELDS & set(f)):
                    group_map[j] = gname
                else:
                    break
            j += 1

    def group_of(k):
        """名稱段 k 屬於哪個群組？回傳群組名或 None。

        群組標題管住「其後連續的一串名稱段」——
        魔杖的「杖身：」底下有 8 個變體（樺木、紫杉木、橡木…）。

        但說明性群組（「深海僕從屬性如下：」）底下是散文，不是變體，
        所以要求：該名稱段的區塊裡必須有價格欄位，才是變體。
        群組標題也只管到「下一個等級/技能樹/群組標題」為止。
        """
        return group_map.get(k)

    # ---------------------------------------------------- 單趟處理
    i = 0
    current_level = None
    current_tree = None

    while i < n:
        t = paras[i]
        if not t:
            i += 1
            continue

        # --- 技能樹標題
        if is_tree_heading(t):
            current_tree = TREE_RE.match(t).group(1)
            i += 1
            continue

        # --- 等級標題
        lh = level_heading(t)
        if lh:
            letter, label = lh
            lv = make_holder()
            lv["level"] = letter
            lv["label"] = label
            entry["levels"].append(lv)
            current_level = lv
            current_tree = None
            i += 1
            continue

        # --- 群組標題（整段只有「xxx：」）→ 只記錄，實際歸屬由 group_of 判斷
        if is_group_header(t):
            i += 1
            continue

        # --- 名稱段 → 等級 / 變體（群組標題下）/ 能力（技能樹下）
        item_name = item_name_at(i)
        if item_name:
            j = block_end(i)
            fields, extra, notes = collect_fields(paras, i + 1, j)

            # (1) 等級區塊：欄位是「等級自己的」→ 掛到等級上
            if i in level_blocks:
                # 若前面已經有等級標題開的 level，就合併進去（副標情形）
                if current_level is not None and current_tree is None \
                        and not current_level["fields_multi"] \
                        and not current_level["levels"] \
                        and current_level.get("tree") is None:
                    lv = current_level
                    if lv.get("label") and lv["label"] != level_blocks[i]:
                        lv["subtitle"] = level_blocks[i]
                else:
                    lv = make_holder()
                    lv["level"] = current_tree or _guess_level(paras, i) or ""
                    lv["label"] = level_blocks[i]
                    entry["levels"].append(lv)
                    current_level = lv
                merge_fields(lv, fields, extra)
                lv["notes"].extend(notes)
                apply_price(lv, lv["fields_multi"])
                # 獨立價格段（「D+600」）→ 等級價位
                pk = _level_price_para.get(i)
                if pk is not None:
                    mm = STANDALONE_PRICE_RE.match(paras[pk])
                    if mm:
                        lv["price"] = {"branch": mm.group(1).upper(),
                                       "points": int(mm.group(2)), "xp": None}
                current_tree = None
                i = j
                continue

            grp = group_of(i)
            if grp is not None:
                var = make_holder(item_name)
                var["group"] = grp
                merge_fields(var, fields, extra)
                var["notes"].extend(notes)
                apply_price(var, var["fields_multi"])
                entry["variants"].append(var)
            else:
                ability = make_holder(item_name)
                merge_fields(ability, fields, extra)
                ability["notes"].extend(notes)
                apply_price(ability, ability["fields_multi"])
                target = None
                for lv in entry["levels"]:
                    if lv["level"] == current_tree:
                        target = lv
                        break
                if target is None:
                    target = current_level
                if target is not None:
                    target.setdefault("tree", []).append(ability)
                else:
                    entry["abilities"].append(ability)
            i = j
            continue

        # --- 欄位
        info = any_label(t)
        if info:
            lab, vstart = info
            value = t[vstart:].strip()
            j = i + 1
            while j < n:
                if not paras[j]:
                    j += 1
                    continue
                if breaks.get(j):
                    break
                j += 1
            fields, extra, notes = collect_fields(paras, i, j)
            target = current_level if current_level is not None else entry
            merge_fields(target, fields, extra)
            target["notes"].extend(notes)
            apply_price(target, target["fields_multi"])
            i = j
            continue

        # --- 沒有任何欄位可接的散敘段落 → notes（不能丟資料）
        current_level["notes"].append(t) if current_level is not None \
            else entry["notes"].append(t)
        i += 1

    return entry


def analyze_page(path: Path):
    html = path.read_text(encoding="utf-8", errors="replace")
    text = plain(html)
    rel = path.parent.relative_to(RES).as_posix()
    page = {"path": rel, "chars": len(text), "entries": [], "warnings": []}

    for seg in split_page_blocks(html):
        seg = [x for x in seg if x.strip()]
        if not seg:
            continue
        joined = " ".join(seg)
        if len(joined) < 20:
            continue
        # 名稱 = 段落序列裡第一個非欄位、非等級標題的段落
        hint = ""
        for t in seg:
            if para_label(t) or level_heading(t) or is_tree_heading(t):
                continue
            if is_group_header(t):
                continue
            if len(t) <= NAME_MAX_LEN and t[0] not in "：:，,。、；;（「『\"'":
                hint = t
                break
        if not hint:
            hint = seg[0][:40]
        page["entries"].append(build_from_paras(seg, hint))

    return page


def summarize(page):
    print("=" * 76)
    print(f"{page['path']}   ({page['chars']} 字, {len(page['entries'])} 條目)")
    print("=" * 76)
    for e in page["entries"]:
        print(f"\n▸ 條目：{e['name']!r}")
        for k, vals in e["fields_multi"].items():
            for v in vals:
                print(f"    {k}: {v[:55]}")
        for k, vals in e["fields_extra_multi"].items():
            for v in vals:
                print(f"    (extra){k}: {v[:50]}")
        if e.get("price"):
            print(f"    [價格] {e['price']}")
        for var in e.get("variants", []):
            print(f"  ├─ 變體 {var['name']}  ({var.get('group')})")
        for lv in e.get("levels", []):
            print(f"  ├─ 等級 {lv['level']}  {lv.get('label','')}")
            for k, vals in lv["fields_multi"].items():
                for v in vals:
                    print(f"  │    {k}: {v[:50]}")
            for ab in lv.get("tree", []):
                print(f"  │    ▸ {ab['name']}  {ab.get('price')}")


def main():
    samples = [
        "資源/瞳術/寫輪眼/index.html",
        "資源/血統/黑暗生物/骷髏/index.html",
        "資源/物品/科技本質/D級系列武器/D級手槍系列/index.html",
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

    dst = ROOT / "build" / "extract-v4-sample.json"
    dst.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"已寫入 {dst.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
