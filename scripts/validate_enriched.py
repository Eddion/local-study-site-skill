# -*- coding: utf-8 -*-
"""
提炼产物全量校验（复制到课程根目录运行，或指明 site 路径）
检查 site/_transcripts_enriched/ 与 manifest、原始转录的一致性：
  - 文件齐全：每个 mp4 都有 enriched JSON 且可解析
  - slug/title 与 manifest 一致
  - cue 条数 = 原 segments 条数，s/e 时间戳逐一相等（点击跳转的依据）
  - x 字段除 <strong></strong> 外不含任何 HTML 标签、不含换行
  - 每个文件至少有一个 h2 小标题
用法: python validate_enriched.py [course_dir]
"""
import json
import re
import sys
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8")
BASE = Path(sys.argv[1]).resolve() if len(sys.argv) > 1 else Path(__file__).resolve().parent
SITE = BASE / "site"
TAG_RE = re.compile(r"<(?!/strong>|strong>)[^>]{1,40}")


def main():
    man = json.loads((SITE / "_manifest.json").read_text(encoding="utf-8"))
    slugs = [it["slug"] for it in man if it["ext"] in ("mp4", "ts", "m4a", "mp3")]
    titles = {it["slug"]: it["title"] for it in man}
    bad = []
    h2_total = cue_total = 0
    for s in slugs:
        f = SITE / "_transcripts_enriched" / f"{s}.json"
        raw = SITE / "_transcripts" / f"{s}.json"
        if not f.exists():
            bad.append((s, "missing")); continue
        try:
            d = json.loads(f.read_text(encoding="utf-8"))
        except Exception as e:
            bad.append((s, f"json: {e}")); continue
        if str(d.get("title", "")).strip() != titles[s].strip():
            bad.append((s, "title mismatch"))
        if not raw.exists():
            continue  # 无原始稿可对（如手工撰写），跳过时间戳校验
        segs = json.loads(raw.read_text(encoding="utf-8")).get("segments", [])
        cues = [b for b in d.get("blocks", []) if b.get("t") == "cue"]
        h2s = [b for b in d.get("blocks", []) if b.get("t") == "h2"]
        if len(cues) != len(segs):
            bad.append((s, f"cue {len(cues)} != segments {len(segs)}")); continue
        for c, g in zip(cues, segs):
            if abs(float(c["s"]) - float(g["start"])) > 1e-6 or abs(float(c["e"]) - float(g["end"])) > 1e-6:
                bad.append((s, f"timestamp drift @ {c['s']} vs {g['start']}")); break
        if not h2s:
            bad.append((s, "no h2"))
        for b in d.get("blocks", []):
            x = str(b.get("x", ""))
            if "\n" in x or "\r" in x:
                bad.append((s, "newline in x")); break
            m = TAG_RE.search(x)
            if m:
                bad.append((s, f"stray tag: {m.group(0)[:30]}")); break
        h2_total += len(h2s)
        cue_total += len(cues)
    print(f"checked {len(slugs)} files: h2={h2_total}, cues={cue_total}, failures={len(bad)}")
    for s, why in bad[:20]:
        print(f"  [fail] {s}: {why}")
    sys.exit(1 if bad else 0)


if __name__ == "__main__":
    main()
