# -*- coding: utf-8 -*-
"""對執行中的本機伺服器做全站驗證。"""
import re
import sys
import urllib.parse
import urllib.request
from pathlib import Path

RES = Path(__file__).resolve().parent.parent / "Resource"
BASE = "http://127.0.0.1:8850/"


def get(path):
    url = BASE + urllib.parse.quote(path)
    with urllib.request.urlopen(url, timeout=8) as r:
        return r.status, r.read()


def main():
    fails = []

    # 1. 入口與資產
    for p in ["index.html", "left.htm", "dtree.js", "dtree.css",
              "img/base.gif", "img/folder.gif", "img/page.gif"]:
        try:
            st, _ = get(p)
            print(f"  {st}  {p}")
            if st != 200:
                fails.append(p)
        except Exception as e:
            print(f"  ERR {p}  {e}")
            fails.append(p)

    # 2. 導航所有連結
    nav = (RES / "left.htm").read_text(encoding="utf-8")
    links = [u for u in re.findall(r'd\.add\(\d+,-?\d+,"(?:[^"]*)","([^"]*)"\)', nav) if u]
    print(f"\n導航連結 {len(links)} 筆，逐一驗證…")
    bad = 0
    for u in links:
        try:
            st, _ = get(u)
            if st != 200:
                bad += 1
                fails.append(u)
        except Exception:
            bad += 1
            fails.append(u)
    print(f"  失效 {bad}")

    # 3. 抽驗頁面內容非空
    samples = ["規則/ST幫助/巨龍/", "資源/血統/人類/特異本質/禍神血脈/",
               "資源/物品/科技本質/D級系列武器/D級手槍系列/", "未開放/武經/血神經/"]
    print("\n抽驗內容：")
    for s in samples:
        try:
            st, body = get(s)
            text = re.sub(r'<[^>]+>', '', body.decode("utf-8", "replace")).strip()
            print(f"  {st}  {s}  內文 {len(text)} 字")
            if len(text) < 20:
                fails.append(s)
        except Exception as e:
            print(f"  ERR {s}  {e}")
            fails.append(s)

    print("\n" + ("✅ 全部通過" if not fails else f"⚠️ {len(fails)} 項失敗"))
    for f in fails[:15]:
        print("   -", f)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
