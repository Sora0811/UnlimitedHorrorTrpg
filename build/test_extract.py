# -*- coding: utf-8 -*-
"""抽取器回歸測試。

每次修改 build/extract_entries.py 後都應該跑這個腳本，
確認已人工核對過的案例沒有退步。

用法：
    python build/test_extract.py
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "build"))
import extract_entries as E  # noqa: E402

FAILS = []


def check(cond, msg):
    print(("  OK   " if cond else "  FAIL ") + msg)
    if not cond:
        FAILS.append(msg)


def find_ability(page, name):
    """在頁面（含等級技能樹）中找能力。"""
    for e in page["entries"]:
        for lv in e.get("levels", []):
            for ab in lv.get("tree", []):
                if ab.get("name") == name:
                    return ab
        for ab in e.get("abilities", []):
            if ab.get("name") == name:
                return ab
    return None


def main():
    print("=" * 72)
    print("抽取器回歸測試")
    print("=" * 72)

    # ---- 寫輪眼：1 條目 / 5 等級 / 15 能力（1-3-6-3-2）/ 複製眼 C+1000
    pg = E.analyze_page(E.RES / "資源/瞳術/寫輪眼" / "index.html")
    levels = [lv for e in pg["entries"] for lv in e.get("levels", [])]
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
    levels = [lv for e in pg["entries"] for lv in e.get("levels", [])]
    check(len(pg["entries"]) == 1,
          f"賽亞人：1 個條目（實際 {len(pg['entries'])}）")
    labels = [lv.get("label", "") for lv in levels]
    expect = ["賽亞人", "賽亞人戰士", "賽亞人精英戰士",
              "皇族血脈（貝吉塔王）", "超級賽亞人"]
    check(labels == expect, f"賽亞人：等級標籤（實際 {labels}）")

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
    names = []
    for e in pg["entries"]:
        names += [ab.get("name") for ab in e.get("abilities", [])]
        names += [ab.get("name") for lv in e.get("levels", [])
                  for ab in lv.get("tree", [])]
    check("科技本質" not in names, f"科技本質 未被當成能力名（實際 {names}）")

    # ---- 魔杖的杖身子項目不得被切成獨立條目
    pg = E.analyze_page(E.RES / "資源/物品/魔幻本質/C級武器" / "index.html")
    first = pg["entries"][0]
    check(len(pg["entries"]) == 80,
          f"C級武器：80 條目（實際 {len(pg['entries'])}）")
    check("魔杖" in (first.get("name") or ""),
          f"C級武器：第一條為「魔杖」（實際 {first.get('name')!r}）")

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

    # ---- 重複欄位必須保留
    f, w, m = E.split_fields("描述：甲 描述：乙 價格：D+100")
    check(f.get("描述") == "甲" and m.get("描述") == ["甲", "乙"],
          f"重複欄位：兩個描述都保留（實際 {m}）")
    check(len(m.get("描述", [])) == 2, "重複欄位：數量正確")

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
