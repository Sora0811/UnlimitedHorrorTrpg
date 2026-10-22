# -*- coding: utf-8 -*-
"""資源抽取器 —— 分段批次執行器。

用途
----
把 `extract_v4.py` 的全站遍歷拆成許多小段，每段獨立落盤。
任何一段中斷或失敗，都只影響那一段；重跑時已完成的段會被跳過。

為什麼要分段
------------
單頁抽取約 2.4ms，全站 1148 頁約 3 秒。真正的風險不是「跑太久被逾時砍斷」，
而是「中斷後整份輸出要重來」。分段 + 續跑把重做成本壓到最小：
最壞情況只需要重跑一段。

設計原則（沿用 extract_v4.py 的「只讀不寫、不硬猜」）
----------------------------------------------------------
- 只讀 Resource/，不修改任何來源檔
- 每段輸出寫成獨立檔案，再合併；來源資料不被覆寫
- 單頁解析失敗 → 記錄到 manifest 的 errors，不讓整段失敗
- 段檔以「寫入暫存檔再 rename」方式產生，避免中斷留下半截 JSON
- manifest 記錄每段狀態，支援 --resume

用法
----
    python build/extract_batch.py plan                    # 只產生計畫，不抽取
    python build/extract_batch.py run                     # 執行（自動續跑已完成的段）
    python build/extract_batch.py run --segments 1-10      # 只跑指定段
    python build/extract_batch.py run --force             # 忽略既有結果，全部重跑
    python build/extract_batch.py merge                   # 合併所有段檔 → extract-all.json
    python build/extract_batch.py status                  # 顯示各段狀態

選項
----
    --segments N-M     只處理第 N 到 M 段（1-based，含兩端）
    --segment-size N   計畫時每段頁數上限（預設 20）
    --out DIR          段檔輸出目錄（預設 build/extract-parts）
    --force            忽略既有段檔，全部重跑
"""
import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RES = ROOT / "Resource"
BUILD = ROOT / "build"
sys.path.insert(0, str(BUILD))

import extract_v4 as E  # noqa: E402

sys.stdout.reconfigure(encoding="utf-8")

DEFAULT_OUT = BUILD / "extract-parts"
MANIFEST = "manifest.json"
PART_PREFIX = "part-"
PART_SUFFIX = ".json"


# ---------------------------------------------------------------- 路徑工具

def page_rel(p: Path) -> str:
    """index.html 的父資料夾相對於 Resource/ 的路徑（'.' 表示入口網站）。"""
    return p.parent.relative_to(RES).as_posix()


def is_entry_page(rel: str) -> bool:
    """入口網站 Resource/index.html 不是條目。"""
    return rel != "."


def collect_pages() -> list:
    """所有待抽取的條目頁 index.html。

    排除兩類：
      1. _chm源檔/ 底下的備份（含 24 個空佔位頁備份）
      2. Resource/index.html —— 入口網站本身，不是條目
         （這就是 resource-classify.json 掃到 1148 頁、導航卻只有 1147 條的原因）
    """
    pages = []
    for p in RES.rglob("index.html"):
        if "_chm源檔" in p.parts:
            continue
        if not is_entry_page(page_rel(p)):
            continue
        pages.append(p)
    pages.sort(key=lambda x: page_rel(x))
    return pages


# ---------------------------------------------------------------- 計畫

def make_plan(pages: list, segment_size: int) -> list:
    """把頁面切成多段。

    依檔案大小降序做 round-robin 分配，而不是依路徑順序連續切塊。
    理由：連續切塊會讓「大型分類」（如 資源/技藝/其他能力）整段落在一起，
    段落耗時嚴重不均；round-robin 能把大頁攤平到每一段。
    """
    sized = sorted(pages, key=lambda x: (-x.stat().st_size, page_rel(x)))
    buckets = [[] for _ in range(max(1, len(sized) // segment_size))]
    for i, p in enumerate(sized):
        buckets[i % len(buckets)].append(p)

    plan = []
    for i, bucket in enumerate(buckets, start=1):
        items = sorted(bucket, key=lambda x: page_rel(x))
        plan.append({
            "segment": i,
            "count": len(items),
            "bytes": sum(p.stat().st_size for p in items),
            "pages": [page_rel(p) for p in items],
        })
    return plan


def part_path(out_dir: Path, seg: int) -> Path:
    return out_dir / f"{PART_PREFIX}{seg:03d}{PART_SUFFIX}"


def load_manifest(out_dir: Path) -> dict:
    mf = out_dir / MANIFEST
    if not mf.exists():
        return {"version": 1, "segments": {}, "created": None}
    try:
        return json.loads(mf.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        # manifest 壞掉就當作沒有，從頭再來（段檔本身才是真實進度）
        return {"version": 1, "segments": {}, "created": None}


def save_manifest(out_dir: Path, mf: dict) -> None:
    """manifest 也用原子寫入，避免中斷留下半截檔。"""
    tmp = out_dir / (MANIFEST + ".tmp")
    tmp.write_text(json.dumps(mf, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, out_dir / MANIFEST)


# ---------------------------------------------------------------- 執行

def run_segment(seg: dict, out_dir: Path, mf: dict) -> dict:
    """執行單一段。回傳該段的狀態摘要。"""
    seg_no = seg["segment"]
    dst = part_path(out_dir, seg_no)
    pages = [RES / rel / "index.html" for rel in seg["pages"]]

    rec = mf["segments"].setdefault(str(seg_no), {})
    rec["segment"] = seg_no
    rec["count"] = seg["count"]
    rec["pages"] = seg["pages"]
    rec["started"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rec["status"] = "running"
    rec["errors"] = []
    rec["ok"] = 0
    rec["missing"] = 0
    save_manifest(out_dir, mf)

    out_pages, errors = [], []
    t0 = time.perf_counter()
    for rel in seg["pages"]:
        src = RES / rel / "index.html"
        if not src.exists():
            rec["missing"] += 1
            errors.append({"path": rel, "error": "檔案不存在"})
            continue
        try:
            page = E.analyze_page(src)
        except Exception as exc:  # 單頁失敗不讓整段失敗
            rec["missing"] += 1
            errors.append({"path": rel, "error": f"{type(exc).__name__}: {exc}"})
            continue
        # 損壞的 HTML 會被 errors="replace" 吞掉，變成近空白頁而不報錯。
        # 這種頁抽不出任何東西，屬於可疑結果，要標記出來而不是靜默產出空條目。
        if page["chars"] < 20:
            rec["suspect"] = rec.get("suspect", 0) + 1
            errors.append({
                "path": rel,
                "error": f"可疑：抽出後僅 {page['chars']} 字元，疑似檔案損壞或空白頁",
            })
        out_pages.append(page)
        rec["ok"] += 1

    elapsed = time.perf_counter() - t0
    rec["elapsed_ms"] = int(elapsed * 1000)
    rec["finished"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    rec["status"] = "done" if not errors else "done_with_errors"
    rec["errors"] = errors
    rec["output"] = dst.name

    # 原子寫入：先寫暫存，再 rename
    tmp = dst.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(out_pages, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, dst)

    mf["updated"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    save_manifest(out_dir, mf)
    return rec


def merge(out_dir: Path, mf: dict) -> dict:
    """把所有已完成的段檔合併成單一 JSON。"""
    merged, done, missing = [], 0, []
    for key in sorted(mf["segments"], key=lambda x: int(x)):
        rec = mf["segments"][key]
        if rec.get("status", "").startswith("done"):
            f = part_path(out_dir, int(key))
            if f.exists():
                try:
                    merged.extend(json.loads(f.read_text(encoding="utf-8")))
                    done += 1
                    continue
                except (OSError, json.JSONDecodeError) as exc:
                    missing.append({"segment": int(key), "error": f"讀取失敗: {exc}"})
                    continue
            missing.append({"segment": int(key), "error": "段檔不存在"})
        else:
            missing.append({"segment": int(key), "error": f"狀態={rec.get('status')}"})
    return {"pages": merged, "segments_merged": done, "issues": missing}


# ---------------------------------------------------------------- 子命令

def cmd_plan(args):
    pages = collect_pages()
    plan = make_plan(pages, args.segment_size)
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    mf = load_manifest(out_dir)
    mf["version"] = 1
    mf["created"] = time.strftime("%Y-%m-%dT%H:%M:%S")
    mf["segment_size"] = args.segment_size
    mf["total_pages"] = len(pages)
    mf["segments"] = {}
    for seg in plan:
        mf["segments"][str(seg["segment"])] = {
            "segment": seg["segment"], "count": seg["count"],
            "bytes": seg["bytes"], "pages": seg["pages"],
            "status": "pending", "errors": [],
        }
    save_manifest(out_dir, mf)

    sizes = [s["bytes"] for s in plan]
    print(f"計畫完成：{len(pages)} 頁 → {len(plan)} 段")
    print(f"  每段頁數: min {min(s['count'] for s in plan)} / max {max(s['count'] for s in plan)}"
          f" / avg {sum(s['count'] for s in plan)/len(plan):.1f}")
    print(f"  每段大小: min {min(sizes)/1024:.0f} KB / max {max(sizes)/1024:.0f} KB"
          f" / avg {sum(sizes)/len(sizes)/1024:.0f} KB")
    print(f"  manifest → {out_dir / MANIFEST}")
    return 0


def cmd_run(args):
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)
    mf = load_manifest(out_dir)
    if not mf.get("segments"):
        print("尚無計畫，先執行：python build/extract_batch.py plan")
        return 1

    sel = None
    if args.segments:
        m = re.fullmatch(r"\s*(\d+)\s*-\s*(\d+)\s*", args.segments)
        if m:
            sel = range(int(m.group(1)), int(m.group(2)) + 1)
        elif re.fullmatch(r"\s*\d+\s*", args.segments):
            sel = [int(args.segments)]
        else:
            print(f"無法解析 --segments: {args.segments!r}")
            return 1

    todo = []
    for key in sorted(mf["segments"], key=lambda x: int(x)):
        seg_no = int(key)
        if sel is not None and seg_no not in sel:
            continue
        rec = mf["segments"][key]
        f = part_path(out_dir, seg_no)
        if not args.force and rec.get("status", "").startswith("done") and f.exists():
            continue
        todo.append({"segment": seg_no, "count": rec["count"], "bytes": rec["bytes"],
                     "pages": rec["pages"]})

    if not todo:
        print("沒有需要執行的段（既有結果皆已完成）。要重跑請加 --force")
        return 0

    print(f"執行 {len(todo)} 段 → {out_dir}")
    t0 = time.perf_counter()
    failed = []
    for i, seg in enumerate(todo, start=1):
        try:
            rec = run_segment(seg, out_dir, mf)
        except Exception as exc:
            failed.append({"segment": seg["segment"], "error": f"{type(exc).__name__}: {exc}"})
            mf["segments"][str(seg["segment"])]["status"] = "failed"
            mf["segments"][str(seg["segment"])]["errors"] = [
                {"path": "*", "error": f"{type(exc).__name__}: {exc}"}]
            save_manifest(out_dir, mf)
            print(f"  [{i}/{len(todo)}] 段 {seg['segment']:>3} ✗ 失敗: {exc}")
            continue
        mark = "✓" if rec["status"] == "done" else "⚠"
        err_note = f"  ({len(rec['errors'])} 個錯誤)" if rec["errors"] else ""
        print(f"  [{i}/{len(todo)}] 段 {seg['segment']:>3} {mark} "
              f"{rec['ok']}/{rec['count']} 頁  {rec['elapsed_ms']}ms{err_note}")

    print(f"\n耗時 {time.perf_counter()-t0:.2f}s")
    if failed:
        print(f"失敗段: {[f['segment'] for f in failed]}")
        return 1
    return 0


def cmd_merge(args):
    out_dir = Path(args.out)
    mf = load_manifest(out_dir)
    if not mf.get("segments"):
        print("尚無計畫。")
        return 1
    res = merge(out_dir, mf)
    dst = BUILD / "extract-all.json"
    tmp = dst.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(res["pages"], ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, dst)
    print(f"合併 {res['segments_merged']} 段 → {dst.relative_to(ROOT)}")
    print(f"  總頁數: {len(res['pages'])}")
    if res["issues"]:
        print(f"  ⚠ {len(res['issues'])} 段未合併:")
        for it in res["issues"][:10]:
            print(f"    段 {it['segment']}: {it['error']}")
    return 0


def cmd_status(args):
    out_dir = Path(args.out)
    mf = load_manifest(out_dir)
    if not mf.get("segments"):
        print("尚無計畫。")
        return 0
    segs = mf["segments"]
    counts = {}
    for rec in segs.values():
        st = rec.get("status", "unknown")
        counts[st] = counts.get(st, 0) + 1
    done = sum(1 for r in segs.values() if r.get("status", "").startswith("done"))
    pages_done = sum(r.get("ok", 0) for r in segs.values())
    print(f"計畫: {mf.get('total_pages')} 頁 / {len(segs)} 段  (segment_size={mf.get('segment_size')})")
    print(f"狀態分布: {counts}")
    print(f"已完成段: {done}/{len(segs)}   已抽頁數: {pages_done}")
    for key in sorted(segs, key=lambda x: int(x)):
        r = segs[key]
        f = part_path(out_dir, int(key))
        exists = "✓" if f.exists() else "·"
        print(f"  段 {key:>3} {r.get('status','?'):<16} "
              f"{r.get('ok',0)}/{r.get('count',0)} 頁  {r.get('elapsed_ms','-')}ms  檔:{exists}")
    return 0


def main():
    ap = argparse.ArgumentParser(description="資源抽取器分段批次執行器")
    ap.add_argument("command", choices=["plan", "run", "merge", "status"])
    ap.add_argument("--segments", help="只處理指定段，如 1-10 或 5")
    ap.add_argument("--segment-size", type=int, default=20, help="每段頁數上限（預設 20）")
    ap.add_argument("--out", default=str(DEFAULT_OUT), help="段檔輸出目錄")
    ap.add_argument("--force", action="store_true", help="忽略既有結果全部重跑")
    args = ap.parse_args()

    return {
        "plan": cmd_plan, "run": cmd_run,
        "merge": cmd_merge, "status": cmd_status,
    }[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
