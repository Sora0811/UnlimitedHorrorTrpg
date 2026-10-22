# -*- coding: utf-8 -*-
"""掃描資源頁，分類並統計結構特徵。

目的：在寫 schema 或解析器之前，先把「1020 頁到底長什麼樣」量清楚。
只讀不寫，輸出報告。

分類依據（依序判斷）：
  MULTI     含 ==...== 分隔線，或含 3 個以上「價格：」→ 一頁多條目
  SINGLE    單一條目（有 本質 或 價格，且只有一組欄位）
  PROSE     無任何欄位標籤 → 說明文
  HYBRID    有欄位但混雜大量散文
"""
import json
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "Resource"

STYLE_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.S | re.I)
TAG_RE = re.compile(r"<[^>]+>")
SEP_RE = re.compile(r"={10,}")

# 欄位標籤（含實際出現的變體）
FIELDS = ["名稱", "本質", "價格", "前提", "等級", "階級", "屬性", "能量池", "能量",
          "技能樹", "特性", "描述", "備註", "分類", "體積", "標籤",
          "武器基本屬性", "武器特殊屬性", "能耗", "動作", "射程", "威力值"]


def plain(html: str) -> str:
    t = STYLE_RE.sub(" ", html)
    t = TAG_RE.sub(" ", t)
    t = t.replace("&nbsp;", " ").replace("&amp;", "&")
    return re.sub(r"\s+", " ", t).strip()


def analyze(path: Path):
    html = path.read_text(encoding="utf-8", errors="replace")
    text = plain(html)

    seps = len(SEP_RE.findall(html)) + len(SEP_RE.findall(text))

    counts = {}
    for f in FIELDS:
        n = len(re.findall(f + r"[：:]", text))
        if n:
            counts[f] = n

    price_n = counts.get("價格", 0)
    essence_n = counts.get("本質", 0)
    total_fields = sum(counts.values())

    if seps >= 1 or price_n >= 3:
        kind = "MULTI"
    elif total_fields == 0:
        kind = "PROSE"
    elif price_n >= 1 or essence_n >= 1:
        kind = "SINGLE"
    else:
        kind = "HYBRID"

    return {
        "path": path.parent.relative_to(RES).as_posix(),
        "kind": kind,
        "chars": len(text),
        "seps": seps,
        "price": price_n,
        "essence": essence_n,
        "fields": counts,
        "field_total": total_fields,
    }


def main():
    pages = sorted(RES.rglob("index.html"))
    rows = []
    for p in pages:
        if "_chm源檔" in p.parts:
            continue
        try:
            rows.append(analyze(p))
        except OSError:
            pass

    kinds = Counter(r["kind"] for r in rows)
    print(f"=== 掃描 {len(rows)} 頁 ===\n")
    for k, v in kinds.most_common():
        print(f"  {k:<8} {v:>5} 頁")

    print("\n=== 各分類的字數分佈 ===")
    for k in kinds:
        sizes = sorted(r["chars"] for r in rows if r["kind"] == k)
        if sizes:
            print(f"  {k:<8} 中位數 {sizes[len(sizes)//2]:>6}  最大 {sizes[-1]:>6}")

    print("\n=== MULTI 頁的條目數估算（價格欄位數）===")
    multi = [r for r in rows if r["kind"] == "MULTI"]
    price_counts = Counter(r["price"] for r in multi)
    for n, c in sorted(price_counts.items())[:12]:
        print(f"  {n:>2} 個價格 → {c:>3} 頁")
    total_entries = sum(max(r["price"], r["seps"] + 1, 2) for r in multi)
    print(f"  估計可拆出約 {total_entries} 個條目")

    # 欄位覆蓋率
    print("\n=== 各分類的欄位出現率 ===")
    for k in ["SINGLE", "MULTI", "PROSE", "HYBRID"]:
        subset = [r for r in rows if r["kind"] == k]
        if not subset:
            continue
        print(f"\n  [{k}] {len(subset)} 頁")
        cov = Counter()
        for r in subset:
            for f in r["fields"]:
                cov[f] += 1
        for f, c in cov.most_common(10):
            print(f"    {f:<10} {c:>4} ({c*100//len(subset):>2}%)")

    out = ROOT / "build" / "resource-classify.json"
    out.write_text(json.dumps(rows, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\n明細已寫入 {out.relative_to(ROOT)}")


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
