#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
chm_html_normalizer.py
批量整理 CHM 解包後的 HTML（編碼轉 UTF-8、簡轉繁 s2t、清理過時樣式）。

只做三件事：
  1. 編碼轉換：GB2312 / GBK / GB18030 / UTF-8 -> UTF-8（無 BOM），並統一 charset 宣告
  2. 簡 -> 繁：僅 OpenCC s2t（不做台灣/香港用語、不做詞彙在地化）
  3. HTML 清理：移除 inline style、font/center/basefont 等已淘汰標籤

絕不做：改檔名、改資料夾、改路徑、改 href/src 等任何引用屬性。

安全機制：
  - 嚴格解碼，不使用 errors="replace"（除非明確加 --lenient）
  - 每個檔案寫入前做「結構指紋」比對（連結屬性、frame/script/a/img/table… 標籤數、
    JavaScript 骨架與路徑字串），不一致就拒絕寫入並列為失敗
  - 原子寫入（暫存檔 + os.replace），失敗不會留下半成品
  - 支援 --dry-run（只分析）與 --backup（修改前完整複製整個目錄）

需求：Python 3.9+，pip install opencc-python-reimplemented（或 opencc）

用法：
  python chm_html_normalizer.py ./rulebook --dry-run
  python chm_html_normalizer.py ./rulebook --backup
  python chm_html_normalizer.py ./rulebook --backup-dir ./rulebook_bak --report report.json
"""
from __future__ import annotations

import argparse
import codecs
import json
import os
import re
import shutil
import stat
import sys
import tempfile
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote

# --------------------------------------------------------------------------- #
# 設定
# --------------------------------------------------------------------------- #
HTML_EXTS = {".htm", ".html"}
GB_CANDIDATES = ("gb2312", "gbk", "gb18030")  # 由嚴到寬，先成功者為準
UTF8_ALIASES = {"utf-8", "utf8"}

# 已淘汰標籤：UNWRAP = 移除標籤但保留內容；DROP = 整個標籤移除（皆為空元素）
OBSOLETE_UNWRAP = {
    "font", "center", "big", "strike", "tt", "blink", "marquee",
    "nobr", "acronym", "multicol",
}
OBSOLETE_DROP = {"basefont", "bgsound", "isindex", "nextid", "spacer"}

# 指紋檢查時要求數量不變的標籤（清理邏輯不會動它們）
KEEP_TAGS = {
    "table", "img", "frame", "frameset", "noframes", "iframe", "script", "link",
    "a", "div", "span", "p", "ul", "ol", "li", "map", "area", "form", "body",
}
# 指紋檢查時要求「完全不變」的屬性
REF_ATTRS = {
    "href", "src", "background", "action", "target", "name", "id",
    "usemap", "data", "codebase", "longdesc",
}
# 只有這些屬性值會做簡轉繁（其餘屬性一律不碰，避免破壞路徑/ID/JS 呼叫）
CONVERT_ATTRS = {"title", "alt"}

META_UTF8 = '<meta charset="utf-8">'

# --------------------------------------------------------------------------- #
# 正規表達式
# --------------------------------------------------------------------------- #
_Q2 = '"[^"]*"'
_Q1 = "'[^']*'"
_OT = "[^'\">]"
_ATTRS = "(?:" + _Q2 + "|" + _Q1 + "|" + _OT + ")*"

TOKEN_RE = re.compile(
    r"(?P<comment><!--.*?(?:-->|\Z))"
    r"|(?P<script><script\b" + _ATTRS + r">.*?(?:</script\s*>|\Z))"
    r"|(?P<style><style\b" + _ATTRS + r">.*?(?:</style\s*>|\Z))"
    r"|(?P<tag></?[A-Za-z!?]" + _ATTRS + r">)"
    r"|(?P<text>[^<]+|<)",
    re.IGNORECASE | re.DOTALL,
)
SCRIPT_SPLIT_RE = re.compile(
    r"(<script\b" + _ATTRS + r">)(.*?)(</script\s*>)?\Z", re.IGNORECASE | re.DOTALL
)
TAG_NAME_RE = re.compile(r"<(/?)([A-Za-z][^\s/>]*)")
ATTR_RE = re.compile(
    r"(?P<lead>\s+|(?<=[\"']))"
    r"(?P<name>[^\s\"'<>/=]+)"
    r"(?:(?P<eq>\s*=\s*)(?P<val>\"[^\"]*\"|'[^']*'|[^\s\"'>]+))?",
)
XML_ENC_RE = re.compile(r"(<\?xml[^>]*?\bencoding\s*=\s*[\"'])[^\"']*([\"'])", re.I)
CHARSET_RE = re.compile(r"charset\s*=\s*[\"']?\s*([A-Za-z0-9_\-]+)", re.I)
NONASCII_RE = re.compile(r"[^\x00-\x7f]")
CJK_RE = re.compile(r"[\u3400-\u9fff\uf900-\ufaff]")
JS_STR_RE = re.compile(r"\"(?:\\.|[^\"\\\n])*\"|'(?:\\.|[^'\\\n])*'", re.DOTALL)
PATHLIKE_RE = re.compile(
    r"[/\\<>=?&#]|://|javascript:|"
    r"\.(?:html?|js|css|gif|jpe?g|png|bmp|ico|swf|chm|txt|xml|asp|php|pdf|"
    r"mp3|wav|avi|mid|zip|rar|exe)(?![A-Za-z0-9])",
    re.IGNORECASE,
)


def is_pathlike(s: str) -> bool:
    """像路徑 / URL / 檔名 / 標記的字串，一律不做簡轉繁。"""
    return bool(PATHLIKE_RE.search(s))


def _unq(v: str | None) -> str:
    if not v:
        return ""
    if v[0] in "\"'" and v[-1] == v[0] and len(v) >= 2:
        return v[1:-1]
    return v


# --------------------------------------------------------------------------- #
# 編碼偵測
# --------------------------------------------------------------------------- #
class DecodeError(Exception):
    pass


def sniff_declared(raw: bytes) -> str | None:
    m = CHARSET_RE.search(raw[:4096].decode("latin-1"))
    return m.group(1).lower() if m else None


def decode_bytes(raw: bytes, lenient: bool = False) -> tuple[str, str]:
    """回傳 (編碼標籤, 文字)。嚴格解碼；失敗丟 DecodeError。"""
    if raw.startswith(codecs.BOM_UTF8):
        try:
            return "utf-8-sig", raw.decode("utf-8-sig")
        except UnicodeDecodeError as e:
            raise DecodeError(f"有 UTF-8 BOM 但內容不合法: {e}")
    try:
        return "ascii", raw.decode("ascii")
    except UnicodeDecodeError:
        pass
    try:
        return "utf-8", raw.decode("utf-8")
    except UnicodeDecodeError:
        pass
    if sniff_declared(raw) in UTF8_ALIASES:
        raise DecodeError("宣告為 UTF-8，但內容不是合法 UTF-8（不猜測，請人工檢查）")
    for enc in GB_CANDIDATES:
        try:
            return enc, raw.decode(enc)
        except UnicodeDecodeError:
            continue
    if lenient:
        return "gb18030(lossy)", raw.decode("gb18030", errors="replace")
    raise DecodeError("無法以 UTF-8 / GB2312 / GBK / GB18030 嚴格解碼（可加 --lenient）")


# --------------------------------------------------------------------------- #
# 轉換核心
# --------------------------------------------------------------------------- #
@dataclass
class TransformStats:
    style_removed: int = 0
    tags_removed: Counter = field(default_factory=Counter)
    meta_updated: bool = False
    meta_inserted: bool = False
    text_changed: bool = False
    js_strings_converted: int = 0
    script_srcs: list = field(default_factory=list)


def insert_meta(html: str) -> str:
    nl = "\r\n" if "\r\n" in html else "\n"
    for pat in (r"<head\b[^>]*>", r"<html\b[^>]*>", r"<!DOCTYPE[^>]*>"):
        m = re.search(pat, html, re.IGNORECASE)
        if m:
            return html[: m.end()] + nl + META_UTF8 + html[m.end():]
    return META_UTF8 + nl + html


class Transformer:
    def __init__(self, conv, convert_js_strings: bool = True):
        self.conv = conv
        self.convert_js_strings = convert_js_strings
        self.st = TransformStats()
        self.meta_seen = False

    # -- 工具 ---------------------------------------------------------------
    def cc(self, s: str) -> str:
        if not NONASCII_RE.search(s):
            return s
        r = self.conv(s)
        if r != s:
            self.st.text_changed = True
        return r

    # -- 主流程 -------------------------------------------------------------
    def run(self, text: str) -> tuple[str, TransformStats]:
        self.st = TransformStats()
        self.meta_seen = False
        out = []
        for m in TOKEN_RE.finditer(text):
            kind, tok = m.lastgroup, m.group()
            if kind == "text":
                out.append(self.cc(tok))
            elif kind == "tag":
                out.append(self.tag(tok))
            elif kind == "script":
                out.append(self.script(tok))
            else:  # comment / <style> 區塊：原樣保留
                out.append(tok)
        result = "".join(out)
        if not self.meta_seen and NONASCII_RE.search(result):
            result = insert_meta(result)
            self.st.meta_inserted = True
        return result, self.st

    # -- <script> -----------------------------------------------------------
    def script(self, tok: str) -> str:
        m = SCRIPT_SPLIT_RE.match(tok)
        open_tag, body, close = m.group(1), m.group(2), m.group(3) or ""
        open_new = self.tag(open_tag)
        if self.convert_js_strings and NONASCII_RE.search(body):
            body = self.js_strings(body)
        return open_new + body + close

    def js_strings(self, body: str) -> str:
        """只轉換含中文、且不像路徑/URL/標記的字串字面值；程式碼本體不動。"""

        def repl(m):
            s = m.group(0)
            inner = s[1:-1]
            if not CJK_RE.search(inner) or is_pathlike(inner):
                return s
            new = self.conv(inner)
            if new != inner:
                self.st.js_strings_converted += 1
                self.st.text_changed = True
            return s[0] + new + s[0]

        return JS_STR_RE.sub(repl, body)

    # -- 一般標籤 -----------------------------------------------------------
    def tag(self, tag: str) -> str:
        if tag[:5].lower() == "<?xml":
            new = XML_ENC_RE.sub(r"\1utf-8\2", tag)
            if new != tag:
                self.st.meta_updated = True
            return new
        nm = TAG_NAME_RE.match(tag)
        if not nm:  # <!DOCTYPE ...> 等
            return tag
        closing, name = nm.group(1), nm.group(2).lower()

        if name in OBSOLETE_UNWRAP or name in OBSOLETE_DROP:
            if not closing:
                self.st.tags_removed[name] += 1
            return ""
        if closing:
            return tag

        if name == "meta":
            r = self.meta(tag, nm)
            if r is not None:
                return r

        head, rest = tag[: nm.end()], tag[nm.end():]

        def repl(am):
            an = am.group("name").lower()
            if an == "style":
                self.st.style_removed += 1
                return ""
            val = am.group("val")
            if name == "script" and an == "src" and val:
                self.st.script_srcs.append(_unq(val))
            if an in CONVERT_ATTRS and val:
                if val[0] in "\"'":
                    new = val[0] + self.cc(val[1:-1]) + val[-1]
                else:
                    new = self.cc(val)
                return am.group("lead") + am.group("name") + am.group("eq") + new
            return am.group(0)

        return head + ATTR_RE.sub(repl, rest)

    def meta(self, tag: str, nm) -> str | None:
        attrs = {
            a.group("name").lower(): _unq(a.group("val"))
            for a in ATTR_RE.finditer(tag[nm.end():])
        }
        is_cs = "charset" in attrs or attrs.get("http-equiv", "").strip().lower() == "content-type"
        if not is_cs:
            return None
        if self.meta_seen:  # 重複的 charset 宣告：移除
            self.st.meta_updated = True
            return ""
        self.meta_seen = True
        if tag != META_UTF8:
            self.st.meta_updated = True
        return META_UTF8


# --------------------------------------------------------------------------- #
# 結構指紋（轉換前後必須一致）
# --------------------------------------------------------------------------- #
def fingerprint(html: str):
    tags, refs, jspaths = Counter(), Counter(), Counter()
    skeletons = []
    for m in TOKEN_RE.finditer(html):
        kind = m.lastgroup
        if kind == "script":
            sm = SCRIPT_SPLIT_RE.match(m.group())
            tag, body = sm.group(1), sm.group(2)
            skeletons.append(JS_STR_RE.sub('""', body))
            for s in JS_STR_RE.finditer(body):
                inner = s.group(0)[1:-1]
                if is_pathlike(inner):
                    jspaths[inner] += 1
        elif kind == "tag":
            tag = m.group()
        else:
            continue
        nm = TAG_NAME_RE.match(tag)
        if not nm or nm.group(1):
            continue
        name = nm.group(2).lower()
        if name in OBSOLETE_UNWRAP or name in OBSOLETE_DROP:
            continue
        if name in KEEP_TAGS:
            tags[name] += 1
        for am in ATTR_RE.finditer(tag[nm.end():]):
            an = am.group("name").lower()
            if an in REF_ATTRS:
                refs[(name, an, _unq(am.group("val")))] += 1
    return tags, refs, skeletons, jspaths


# --------------------------------------------------------------------------- #
# 檔案處理
# --------------------------------------------------------------------------- #
@dataclass
class FileResult:
    path: str
    encoding: str = ""
    ok: bool = False
    modified: bool = False
    error: str = ""
    st: TransformStats | None = None


def atomic_write(path: Path, data: bytes) -> None:
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".chmnorm_", suffix=".part")
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
        shutil.copystat(path, tmp)  # 保留權限與時間戳
        if not os.access(path, os.W_OK):  # CHM 解包檔常為唯讀（Windows）
            os.chmod(path, os.stat(path).st_mode | stat.S_IWRITE)
        os.replace(tmp, path)
    except BaseException:
        if os.path.exists(tmp):
            os.unlink(tmp)
        raise


def process_file(path: Path, tf: Transformer, dry_run: bool, lenient: bool) -> FileResult:
    res = FileResult(path=str(path))
    try:
        raw = path.read_bytes()
        enc, text = decode_bytes(raw, lenient)
        res.encoding = enc
        new_text, st = tf.run(text)
        res.st = st
        if fingerprint(text) != fingerprint(new_text):
            raise ValueError("結構指紋不一致（連結/標籤/JS 骨架被改動），拒絕寫入")
        new_raw = new_text.encode("utf-8")
        res.modified = new_raw != raw
        if res.modified and not dry_run:
            atomic_write(path, new_raw)
        res.ok = True
    except Exception as e:  # noqa: BLE001  單檔失敗不影響整批
        res.ok = False
        res.error = f"{type(e).__name__}: {e}"
    return res


def collect_files(root: Path, exclude: Path | None) -> list[Path]:
    files = []
    for dp, dn, fn in os.walk(root):  # 預設不跟隨 symlink
        if exclude is not None:
            dn[:] = [d for d in dn if (Path(dp) / d).resolve() != exclude]
        for f in fn:
            if Path(f).suffix.lower() in HTML_EXTS:
                files.append(Path(dp) / f)
    files.sort()
    return files


def make_backup(root: Path, backup_dir: Path) -> None:
    if backup_dir.exists():
        raise SystemExit(f"備份目錄已存在，為避免覆蓋已中止: {backup_dir}")
    print(f"[備份] 複製 {root} -> {backup_dir} ...", file=sys.stderr)
    shutil.copytree(root, backup_dir, symlinks=True)
    print("[備份] 完成", file=sys.stderr)


def make_converter():
    try:
        import opencc  # type: ignore
    except ImportError:
        raise SystemExit("找不到 OpenCC，請先安裝：pip install opencc-python-reimplemented")
    last = None
    for cfg in ("s2t", "s2t.json"):  # 不同套件版本的設定名稱不同；只用 s2t，不用 tw/hk
        try:
            return lru_cache(maxsize=100000)(opencc.OpenCC(cfg).convert)
        except Exception as e:  # noqa: BLE001
            last = e
    raise SystemExit(f"OpenCC 初始化失敗: {last}")


def scan_external_js(results: list[FileResult]) -> list[str]:
    """找出被 HTML 引用、含非 ASCII 內容的本地 .js（它們不在轉換範圍內）。"""
    seen: set[Path] = set()
    for r in results:
        if not r.ok or not r.st:
            continue
        base = Path(r.path).parent
        for src in r.st.script_srcs:
            if "://" in src or src.startswith(("//", "data:", "javascript:")):
                continue
            p = base / unquote(src.split("?")[0].split("#")[0])
            seen.add(p)
    risky = []
    for p in sorted(seen):
        try:
            if p.is_file() and NONASCII_RE.search(p.read_bytes().decode("latin-1")):
                risky.append(str(p))
        except OSError:
            continue
    return risky


# --------------------------------------------------------------------------- #
# 報告
# --------------------------------------------------------------------------- #
def build_report(root, results, dry_run, elapsed, backup_dir, js_risk):
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    enc = Counter(r.encoding for r in ok)
    for r in failed:
        enc["(解碼失敗)"] += 1 if not r.encoding else 0
    tags_removed, style_removed = Counter(), 0
    for r in ok:
        tags_removed.update(r.st.tags_removed)
        style_removed += r.st.style_removed
    return {
        "root": str(root),
        "mode": "dry-run" if dry_run else "write",
        "backup_dir": str(backup_dir) if backup_dir else None,
        "total": len(results),
        "success": len(ok),
        "failed": len(failed),
        "modified": sum(1 for r in ok if r.modified),
        "encoding_distribution": dict(enc.most_common()),
        "s2t_changed_files": sum(1 for r in ok if r.st.text_changed),
        "meta_updated_files": sum(1 for r in ok if r.st.meta_updated),
        "meta_inserted_files": sum(1 for r in ok if r.st.meta_inserted),
        "style_attrs_removed": style_removed,
        "obsolete_tags_removed": dict(tags_removed.most_common()),
        "js_strings_converted": sum(r.st.js_strings_converted for r in ok),
        "external_js_with_non_ascii": js_risk,
        "failures": [{"path": r.path, "error": r.error} for r in failed],
        "elapsed_sec": round(elapsed, 2),
    }


def print_report(rep: dict) -> None:
    line = "=" * 60
    verb = "將被修改" if rep["mode"] == "dry-run" else "已修改"
    print(f"\n{line}\n轉換報告（{'DRY-RUN，未寫入任何檔案' if rep['mode'] == 'dry-run' else '已寫入'}）\n{line}")
    print(f"目錄              : {rep['root']}")
    if rep["backup_dir"]:
        print(f"備份位置          : {rep['backup_dir']}")
    print(f"處理檔案數        : {rep['total']}")
    print(f"成功數            : {rep['success']}")
    print(f"失敗數            : {rep['failed']}")
    print(f"{verb}的檔案數    : {rep['modified']}")
    print("原始編碼分布      :")
    for k, v in rep["encoding_distribution"].items():
        print(f"    {k:<16}{v}")
    print(f"簡轉繁有變動檔案  : {rep['s2t_changed_files']}")
    print(f"charset 更新/插入 : {rep['meta_updated_files']} / {rep['meta_inserted_files']}")
    print(f"移除 style 屬性   : {rep['style_attrs_removed']}")
    print(f"移除過時標籤      : {rep['obsolete_tags_removed'] or '無'}")
    print(f"JS 字串簡轉繁     : {rep['js_strings_converted']}")
    print(f"耗時              : {rep['elapsed_sec']} 秒")
    if rep["failures"]:
        print(f"\n失敗清單（前 50 筆，這些檔案維持原樣）：")
        for f in rep["failures"][:50]:
            print(f"  - {f['path']}\n      {f['error']}")
    if rep["external_js_with_non_ascii"]:
        n = len(rep["external_js_with_non_ascii"])
        print(
            f"\n[注意] 有 {n} 個被引用的外部 .js 含非 ASCII 字元（未在轉換範圍內，仍是原編碼）。\n"
            "       若這些 JS 內有中文（例如 dTree 節點名稱），頁面轉成 UTF-8 後可能亂碼，請人工確認："
        )
        for p in rep["external_js_with_non_ascii"][:30]:
            print(f"  - {p}")


# --------------------------------------------------------------------------- #
# 入口
# --------------------------------------------------------------------------- #
def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="整理 CHM 解包後的 TRPG 規則書 HTML")
    ap.add_argument("root", type=Path, help="要處理的根目錄")
    ap.add_argument("--dry-run", action="store_true", help="只分析，不寫入任何檔案")
    ap.add_argument("--backup", action="store_true",
                    help="修改前完整備份（預設放在根目錄旁：<目錄名>_backup_<時間>）")
    ap.add_argument("--backup-dir", type=Path, help="指定備份目錄（隱含 --backup）")
    ap.add_argument("--report", type=Path, help="另存 JSON 報告")
    ap.add_argument("--no-js-strings", action="store_true",
                    help="不對內嵌 <script> 內的中文字串做簡轉繁（預設會，路徑類字串除外）")
    ap.add_argument("--lenient", action="store_true",
                    help="嚴格解碼失敗時，改用 gb18030 + 取代字元（有損，預設關閉）")
    ap.add_argument("-v", "--verbose", action="store_true", help="逐檔輸出結果")
    args = ap.parse_args(argv)

    try:
        sys.stdout.reconfigure(errors="replace")
        sys.stderr.reconfigure(errors="replace")
    except Exception:  # noqa: BLE001
        pass

    root = args.root.resolve()
    if not root.is_dir():
        raise SystemExit(f"不是有效目錄: {root}")

    want_backup = (args.backup or args.backup_dir is not None) and not args.dry_run
    backup_dir = None
    if want_backup:
        backup_dir = (args.backup_dir or
                      root.parent / f"{root.name}_backup_{datetime.now():%Y%m%d_%H%M%S}").resolve()
        if backup_dir == root or root in backup_dir.parents or backup_dir in root.parents:
            raise SystemExit("備份目錄不可位於根目錄內，也不可包含根目錄")
    elif args.dry_run and (args.backup or args.backup_dir):
        print("[提示] dry-run 不會寫入，因此不建立備份。", file=sys.stderr)

    conv = make_converter()
    tf = Transformer(conv, convert_js_strings=not args.no_js_strings)

    files = collect_files(root, backup_dir)
    if not files:
        print("找不到 .htm / .html 檔案。")
        return 0
    print(f"找到 {len(files)} 個 HTML 檔案。", file=sys.stderr)

    if backup_dir:
        make_backup(root, backup_dir)

    t0 = time.time()
    results: list[FileResult] = []
    for i, f in enumerate(files, 1):
        r = process_file(f, tf, args.dry_run, args.lenient)
        results.append(r)
        if args.verbose:
            tag = "OK  " if r.ok else "FAIL"
            mod = "*" if r.modified else " "
            print(f"[{tag}]{mod} {r.encoding or '-':<14} {f.relative_to(root)} {r.error}", file=sys.stderr)
        elif i % 100 == 0 or i == len(files):
            print(f"  進度 {i}/{len(files)}", file=sys.stderr)

    rep = build_report(root, results, args.dry_run, time.time() - t0, backup_dir,
                       scan_external_js(results))
    print_report(rep)
    if args.report:
        args.report.write_text(json.dumps(rep, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"\nJSON 報告已寫入: {args.report}")
    return 1 if rep["failed"] else 0


if __name__ == "__main__":
    sys.exit(main())
