# -*- coding: utf-8 -*-
"""抽取器回歸測試（對應 build/extract_v4.py 的 v4 schema）。

每次修改 build/extract_v4.py 後都應該跑這個腳本，
確認已人工核對過的案例沒有退步。

v4 schema 重點：
- 所有欄位都是陣列（fields_multi / fields_extra_multi），沒有 `fields`
- 能量池統一命名（原本的「能量」別名）
- levels[] 帶 level / label / fields_multi / tree[]
- variants[] 攤平在條目頂層，帶 group 欄位
- 續行段落併入前一欄位；無標籤散文進 notes

用法：
    python build/test_extract.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))
import extract_v4 as E  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def all_abilities(page):
    """頁面裡所有能力（含等級技能樹）。"""
    out = []
    for e in page["entries"]:
        out += e.get("abilities", [])
        for lv in e.get("levels", []):
            out += lv.get("tree", [])
    return out


def find_ability(page, name):
    for ab in all_abilities(page):
        if ab.get("name") == name:
            return ab
    return None


def all_variants(page):
    out = []
    for e in page["entries"]:
        out += e.get("variants", [])
    return out


def all_levels(page):
    return [lv for e in page["entries"] for lv in e.get("levels", [])]


def main():
    print("=" * 72)
    print("抽取器回歸測試（v4）")
    print("=" * 72)

    # ---- 寫輪眼：1 條目 / 5 等級 / 15 能力（1-3-6-3-2）/ 複製眼 C+1000
    pg = E.analyze_page(E.RES / "資源/瞳術/寫輪眼" / "index.html")
    levels = all_levels(pg)
    check(len(pg["entries"]) == 1,
          f"寫輪眼：1 個條目（實際 {len(pg['entries'])}）")
    check([lv["level"] for lv in levels] == ["D", "C", "B", "A", "S"],
          f"寫輪眼：等級 D/C/B/A/S（實際 {[lv['level'] for lv in levels]}）")
    dist = [len(lv.get("tree", [])) for lv in levels]
    check(sum(dist) == 15 and dist == [1, 3, 6, 3, 2],
          f"寫輪眼：15 能力，分布 1/3/6/3/2（實際 {dist}）")
    ab = find_ability(pg, "複製眼")
    check(ab is not None and ab.get("price")
          and ab["price"].get("branch") == "C"
          and ab["price"].get("points") == 1000,
          f"寫輪眼：複製眼 C+1000（實際 {ab.get('price') if ab else None}）")

    # ---- 賽亞人：1 條目 / 5 等級 / 等級標籤
    pg = E.analyze_page(
        E.RES / "資源/血統/非人類/特異本質/外星生物/賽亞人" / "index.html")
    levels = all_levels(pg)
    check(len(pg["entries"]) == 1,
          f"賽亞人：1 個條目（實際 {len(pg['entries'])}）")
    labels = [lv.get("label", "") for lv in levels]
    expect = ["賽亞人", "賽亞人戰士", "賽亞人精英戰士",
              "皇族血脈（貝吉塔王）", "超級賽亞人"]
    check(labels == expect, f"賽亞人：等級標籤（實際 {labels}）")

    # ---- 骷髏：等級欄位直接掛在 levels[] 上（不是塞進 tree）
    pg = E.analyze_page(E.RES / "資源/血統/黑暗生物/骷髏" / "index.html")
    levels = all_levels(pg)
    check([lv["level"] for lv in levels] == ["D", "C", "B", "A", "S"],
          f"骷髏：5 等級 D/C/B/A/S（實際 {[lv['level'] for lv in levels]}）")
    check(levels[0].get("label") == "骷髏兵",
          f"骷髏：D 級 label=骷髏兵（實際 {levels[0].get('label')!r}）")
    f0 = levels[0]["fields_multi"]
    check(set(f0) >= {"屬性", "能量池", "特性", "技能樹", "特殊"},
          f"骷髏：D 級欄位含 屬性/能量池/特性/技能樹/特殊（實際 {list(f0)}）")
    check(f0.get("能量池") == ["無"],
          f"骷髏：能量池別名已正規化（實際 {f0.get('能量池')}）")
    check(all(len(v) == 1 for v in f0.values()),
          "骷髏：每個欄位都是陣列且長度 1")
    check("fields" not in levels[0],
          "骷髏：v4 不再使用 `fields` 欄位")
    check(all(isinstance(v, list) for v in f0.values()),
          "骷髏：欄位值一律是陣列")

    # ---- 86 血統：沒有「X 級」前綴的等級（名稱 + 獨立價格段）
    pg = E.analyze_page(E.RES / "未開放/86血統" / "index.html")
    levels = all_levels(pg)
    check([lv["level"] for lv in levels] == ["D", "C", "B", "A"],
          f"86血統：等級 D/C/B/A（實際 {[lv['level'] for lv in levels]}）")
    check([lv.get("label") for lv in levels][:2] == ["新兵", "老兵"],
          f"86血統：D/C 級名稱（實際 {[lv.get('label') for lv in levels]}）")
    check(levels[0].get("price", {}).get("points") == 600,
          f"86血統：D 級價格 600（實際 {levels[0].get('price')}）")
    check(levels[0].get("price", {}).get("branch") == "D",
          f"86血統：D 級價格分支 D（實際 {levels[0].get('price')}）")

    # ---- 港漫強者：4 等級，欄位掛在等級上，能力在 tree
    pg = E.analyze_page(E.RES / "資源/稱號/戰士類/港漫強者" / "index.html")
    levels = all_levels(pg)
    check([lv["level"] for lv in levels] == ["D", "C", "B", "A"],
          f"港漫強者：4 等級 D/C/B/A（實際 {[lv['level'] for lv in levels]}）")
    check(levels[0].get("label") == "初武・萬里長城平地起",
          f"港漫強者：D 級 label（實際 {levels[0].get('label')!r}）")
    check("技能樹" in levels[0]["fields_multi"],
          f"港漫強者：D 級含 技能樹 欄位（實際 {list(levels[0]['fields_multi'])}）")
    check(sum(len(lv.get("tree", [])) for lv in levels) == 33,
          "港漫強者：33 個技能樹能力")

    # ---- 魔杖：子項目變成 variants，跟父項目同一個檔案
    pg = E.analyze_page(E.RES / "資源/物品/魔幻本質/C級武器" / "index.html")
    check(len(pg["entries"]) == 80,
          f"C級武器：80 條目（實際 {len(pg['entries'])}）")
    variants = all_variants(pg)
    check(len(variants) == 15,
          f"C級武器：15 個變體（實際 {len(variants)}）")
    groups = {v.get("group") for v in variants}
    check(groups == {"杖身", "杖芯"},
          f"C級武器：變體分為 杖身/杖芯（實際 {groups}）")
    wand = pg["entries"][0]
    check("魔杖" in (wand.get("name") or ""),
          f"C級武器：第一條為「魔杖」（實際 {wand.get('name')!r}）")

    # ---- 血族：異能是能力不是變體
    pg = E.analyze_page(E.RES / "資源/血統/黑暗生物/血族" / "index.html")
    check(len(all_variants(pg)) == 0,
          f"血族：異能不算變體（實際 {len(all_variants(pg))}）")
    check(len(all_abilities(pg)) == 94,
          f"血族：94 個能力（實際 {len(all_abilities(pg))}）")
    levels = all_levels(pg)
    check([lv["level"] for lv in levels] == ["D", "C", "B", "A"],
          f"血族：等級 D/C/B/A（實際 {[lv['level'] for lv in levels]}）")

    # ---- D級手槍系列：6 條目（有 ==== 分隔線）
    pg = E.analyze_page(
        E.RES / "資源/物品/科技本質/D級系列武器/D級手槍系列" / "index.html")
    check(len(pg["entries"]) == 6,
          f"D級手槍系列：6 條目（實際 {len(pg['entries'])}）")

    # ---- 玲瓏骰：4 條目（無 ==== 分隔線，靠段落規則切）
    pg = E.analyze_page(E.RES / "資源/技藝/特殊武器/玲瓏骰" / "index.html")
    check(len(pg["entries"]) == 4,
          f"玲瓏骰：4 條目（實際 {len(pg['entries'])}）")

    # ---- 科技本質 不得被當成能力名稱
    names = [ab.get("name") for ab in all_abilities(pg)]
    check("科技本質" not in names, f"科技本質 未被當成能力名（實際 {names}）")

    # ---- 通用D級法術：65 條目（有 ==== 分隔線）
    pg = E.analyze_page(
        E.RES / "資源/能量池/魔力/法術列表/通用D級法術" / "index.html")
    check(len(pg["entries"]) == 65,
          f"通用D級法術：65 條目（實際 {len(pg['entries'])}）")

    # ---- 價格解析：帶說明的價格
    p, warn, note = E.parse_price("D+500 強效施法：當你施展…")
    check(p and p["branch"] == "D" and p["points"] == 500,
          f"價格：可從「D+500 強效施法：…」讀出 D+500（實際 {p}）")
    check(note.startswith("強效施法"), f"價格：其餘文字保留於 note（實際 {note!r}）")

    p, warn, note = E.parse_price("C+1500")
    check(p and p["branch"] == "C" and p["points"] == 1500 and not note,
          f"價格：純價格 C+1500（實際 {p}）")

    p, warn, note = E.parse_price("13XP")
    check(p and p["xp"] == 13 and p["branch"] is None,
          f"價格：純 XP 13XP（實際 {p}）")

    p, warn, note = E.parse_price("特殊（總價D+2000）")
    check(p and p["branch"] == "D" and p["points"] == 2000,
          f"價格：總價寫法（實際 {p}）")

    p, warn, note = E.parse_price("價格是原來的 50 倍")
    check(p is None and warn is not None,
          f"價格：無絕對數值時不硬猜（實際 {p}, {warn!r}）")

    # ---- 欄位別名正規化
    check(E.canon_field("能量") == "能量池",
          f"別名：能量 → 能量池（實際 {E.canon_field('能量')!r}）")
    check(E.canon_field("成份") == "成分",
          f"別名：成份 → 成分（實際 {E.canon_field('成份')!r}）")

    # ---- 重複欄位必須保留
    f, e, n = E.collect_fields(["描述：甲", "描述：乙", "價格：D+100"], 0, 3)
    check(f.get("描述") == ["甲", "乙"],
          f"重複欄位：兩個描述都保留（實際 {f}）")
    check(f.get("價格") == ["D+100"], "重複欄位：價格也保留")

    # ---- 續行段落併入前一欄位
    f, e, n = E.collect_fields(["描述：第一段", "第二段沒有標籤"], 0, 2)
    check(f.get("描述") == ["第一段", "第二段沒有標籤"],
          f"續行段落併入前一欄位（實際 {f}）")
    check(n == [], "續行段落不進 notes")

    # ---- 無標籤散文進 notes
    f, e, n = E.collect_fields(["沒有標籤的散文"], 0, 1)
    check(f == {} and n == ["沒有標籤的散文"],
          f"無標籤散文進 notes（實際 fields={f} notes={n}）")

    print()
    print("=" * 72)
    if FAILS:
        print(f"失敗 {len(FAILS)} 項：")
        for f in FAILS:
            print("   - " + f)
        return 1
    print("全部通過")
    return 0


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    sys.exit(main())
