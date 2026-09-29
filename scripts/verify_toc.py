# -*- coding: utf-8 -*-
"""校验章节导航注入结果（阶段三）。

用法:
  python verify_toc.py <site目录>                 # 扫描全站，校验所有含 <nav class="tocnav"> 的页面
  python verify_toc.py <site目录> <文件...>       # 只校验指定页面（相对 site 的路径）

校验项: tocnav 恰好注入一次; <style> 标签成对; 锚点 id 与链接 1:1（无重复 id、
无悬空链接、无未被链接的 id）; nav 位置在 topbar 之后 <main> 之前;
目录链接文字与目标 h1/h2 标题逐字一致。全部通过输出「全部通过」并退出码 0。
"""
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")


def check(p: Path):
    t = p.read_text(encoding="utf-8")
    errs = []
    nav = t.count('<nav class="tocnav">')
    style, style_close = t.count("<style>"), t.count("</style>")
    if nav != 1:
        errs.append(f"tocnav x{nav}")
    if style != 1 or style_close != 1:
        errs.append(f"style x{style}/{style_close}")
    hrefs = re.findall(r'href="#(toc-\d+)"', t)
    ids = re.findall(r'id="(toc-\d+)"', t)
    dup_ids = sorted({i for i in ids if ids.count(i) > 1})
    if dup_ids:
        errs.append(f"重复id:{dup_ids}")
    missing = [h for h in hrefs if h not in ids]
    if missing:
        errs.append(f"悬空链接:{missing}")
    orphan = [i for i in ids if i not in hrefs]
    if orphan:
        errs.append(f"未被链接的id:{orphan}")
    # nav 必须在 topbar 之后 main 之前
    m = re.search(r'<nav class="tocnav">.*?</nav>', t, re.S)
    if m:
        topbar_pos = t.find('<nav class="topbar">')
        main_pos = t.find("<main>")
        if not (topbar_pos < m.start() < main_pos):
            errs.append("tocnav位置不对")
        # 链接文字与目标标题一致
        for anchor, text in re.findall(r'<li><a href="#(toc-\d+)">([^<]+)</a></li>', m.group(0)):
            hm = re.search(r'<h[12] id="' + anchor + r'">([^<]+)</h[12]>', t)
            if not hm:
                errs.append(f"{anchor}: 目标标题不存在")
            elif hm.group(1) != text:
                errs.append(f"{anchor}: 文字不一致 «{hm.group(1)}» vs «{text}»")
    return len(hrefs), len(ids), errs


def main():
    args = sys.argv[1:]
    if not args:
        print(__doc__)
        sys.exit(2)
    site = Path(args[0]).resolve()
    if len(args) > 1:
        files = [site / a for a in args[1:]]
    else:
        files = [p for p in sorted(site.rglob("*.html"))
                 if '<nav class="tocnav">' in p.read_text(encoding="utf-8")]
    if not files:
        print("未找到含 tocnav 的页面（阶段三还没跑？）")
        sys.exit(1)
    ok = True
    for p in files:
        n_anchors, n_ids, errs = check(p)
        rel = p.relative_to(site) if p.is_relative_to(site) else p
        status = "OK " if not errs else "FAIL"
        if errs:
            ok = False
        print(f"[{status}] {rel} nav条目:{n_anchors} 锚点id:{n_ids} {'; '.join(errs)}")
    print("\n全部通过" if ok else "\n存在问题，需修复")
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
