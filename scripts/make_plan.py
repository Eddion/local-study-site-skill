# -*- coding: utf-8 -*-
"""
学习计划生成器：把 manifest 资料按「每天约1小时（含知识内化）」打包成 Day/Week，
写 site/_plan.json，build_site.py 的 index 会据此渲染 Week 小标题与 Day 分组。
（复制到课程根目录运行，与 build_site.py 同目录）

打包逻辑：
- 从标题提取章节号：【公式3】/【案例3-7】→ 第3章；无标签条目自成一章（如先导片）
- 文件序已由 build_site.py 的自然排序保证（按集数，非字典序），连续同章条目合成学习模块
- 模块按时长贪心装入天：加上本模块会超过 --day-minutes 就另起一天（默认25分钟视频，
  剩余约35分钟留给笔记与内化，合计每天约1小时）
- 每 --week-days 天（默认7）合为一周；周标题取本周覆盖最多的公式段落在 --modules
  里对应的阶段名（不给 --modules 就只标公式范围）

mp4 时长探测：优先复用 site/_asr/durations.json（transcribe 流程的产物），
否则用 av 探测（需 pip install av），都没有就按体积估算（约2.5MB/分钟）。
用法示例：
  python make_plan.py --day-minutes 25 --modules "1-2:建立你的能量场;3-12:减少能量耗损;13-28:提升你的能量;29-32:成事之道"
"""
import argparse
import json
import re
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parent
SITE = BASE / "site"
MANIFEST = SITE / "_manifest.json"
PLAN = SITE / "_plan.json"
DUR_CACHE = SITE / "_asr" / "durations.json"

sys.stdout.reconfigure(encoding="utf-8")


def probe_minutes(path: Path, size_mb: float, cache: dict) -> float:
    key = path.name
    if key in cache:
        return cache[key] / 60.0
    try:
        import av
        with av.open(str(path)) as c:
            return (c.duration or 0) / 1_000_000 / 60.0
    except Exception:
        return max(size_mb / 2.5, 1.0)  # 兜底：按 ~2.5MB/分钟 估算


def chapter_of(title: str):
    m = re.search(r"【公式\s*(\d+)\s*】", title)
    if m:
        return int(m.group(1))
    m = re.search(r"【案例\s*(\d+)\s*-\s*\d+\s*】", title)
    if m:
        return int(m.group(1))
    return None


def clean_formula_title(title: str) -> str:
    t = re.sub(r"^【公式\d+】\s*", "", title).strip()
    return t[:18] + ("…" if len(t) > 18 else "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day-minutes", type=float, default=25.0,
                    help="每天的视频分钟预算（1小时学习日 = 视频预算 + 内化时间）")
    ap.add_argument("--week-days", type=int, default=7)
    ap.add_argument("--modules", default="",
                    help="阶段划分，格式 '1-2:阶段名;3-12:阶段名'，用于周标题")
    args = ap.parse_args()

    items = json.loads(MANIFEST.read_text(encoding="utf-8"))
    cache = json.loads(DUR_CACHE.read_text("utf-8")) if DUR_CACHE.exists() else {}

    # 条目时长
    for it in items:
        if it["ext"] == "mp4":
            it["_min"] = probe_minutes(BASE / it["src"], it.get("size_mb", 0), cache)
        elif it["ext"] == "pdf":
            it["_min"] = it.get("pages", 0) * 2.0  # 粗估：每页2分钟
        else:
            it["_min"] = 5.0

    # 连续同章合成模块
    modules = []
    for it in items:
        ch = chapter_of(it["title"])
        key = ch if ch is not None else f"_{it['slug']}"
        if modules and modules[-1]["key"] == key:
            modules[-1]["items"].append(it)
            modules[-1]["min"] += it["_min"]
        else:
            modules.append({"key": key, "items": [it], "min": it["_min"]})

    # 贪心装入天
    days = []
    for mod in modules:
        first = mod["items"][0]
        if days and days[-1]["min"] + mod["min"] <= args.day_minutes * 1.35:
            days[-1]["mods"].append(mod)
            days[-1]["min"] += mod["min"]
            days[-1]["items"].extend(it["slug"] for it in mod["items"])
        else:
            days.append({"mods": [mod], "min": mod["min"],
                         "items": [it["slug"] for it in mod["items"]]})

    # Day 标题：取当天第一个带【公式N】的条目作主题，多章时标出范围
    for i, d in enumerate(days, 1):
        d["day"] = i
        chs = sorted({m["key"] for m in d["mods"] if isinstance(m["key"], int)})
        span = ""
        if chs:
            span = f"公式{chs[0]}" if len(chs) == 1 else f"公式{chs[0]}–{chs[-1]}"
        f_item = None
        for m in d["mods"]:
            hit = [it for it in m["items"] if "【公式" in it["title"]]
            if hit:
                f_item = hit[0]
                break
        if f_item:
            t = re.sub(r"^\s*\d+\s*[.、．]?\s*", "", f_item["title"])
            t = re.sub(r"^【公式\d+】\s*", "", t).strip()
            short = t[:14] + ("…" if len(t) > 14 else "")
            d["title"] = f"{span} · {short}" if span else short
        else:
            lead = d["mods"][0]["items"][0]
            t = re.sub(r"^\s*\d+\s*[.、．]?\s*", "", lead["title"])
            t = re.sub(r"^【[^】]*】\s*", "", t).strip()
            d["title"] = (t[:16] + ("…" if len(t) > 16 else "")) or "学习日"
        d["min"] = round(d["min"], 1)

    # 周
    ranges = []
    for part in args.modules.split(";"):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"(\d+)-(\d+):(.+)", part)
        if m:
            ranges.append((int(m.group(1)), int(m.group(2)), m.group(3).strip()))

    def phase_of(ch: int):
        for a, b, label in ranges:
            if a <= ch <= b:
                return label
        return None

    weeks = []
    for w_start in range(0, len(days), args.week_days):
        chunk = days[w_start:w_start + args.week_days]
        chs = sorted({m["key"] for d in chunk for m in d["mods"] if isinstance(m["key"], int)})
        phases = []
        for ch in chs:
            lab = phase_of(ch)
            if lab and lab not in phases:
                phases.append(lab)
        span = f"（公式{chs[0]}–{chs[-1]}）" if chs else ""
        if phases:
            title = " → ".join(phases) + span
        elif span:
            title = span.strip("（）")
        else:
            title = f"Day {chunk[0]['day']}–{chunk[-1]['day']}"
        weeks.append({"week": len(weeks) + 1, "title": title,
                      "days": [{k: d[k] for k in ("day", "title", "min", "items")} for d in chunk]})

    total_min = sum(d["min"] for d in days)
    buf = "，留1天复习缓冲" if args.week_days < 7 else ""
    plan = {
        "day_minutes": args.day_minutes,
        "week_days": args.week_days,
        "note": f"每天约1小时：视频约{args.day_minutes:.0f}分钟 + 笔记内化约{60-args.day_minutes:.0f}分钟；每周{args.week_days}个学习日{buf}",
        "total": f"{len(days)} 天 · {len(weeks)} 周 · 视频 {total_min/60:.1f} 小时",
        "weeks": weeks,
    }
    PLAN.write_text(json.dumps(plan, ensure_ascii=False, indent=1), encoding="utf-8")
    for wk in plan["weeks"]:
        ds = wk["days"]
        print(f"Week {wk['week']}  {wk['title']}  (Day {ds[0]['day']}–{ds[-1]['day']})")
        for d in ds:
            print(f"   Day {d['day']:>2}  {d['title']}  [{d['min']} min, {len(d['items'])} 个资料]")
    print(f"\n_plan.json written: {len(days)} days / {len(weeks)} weeks / {total_min/60:.1f} h video")


if __name__ == "__main__":
    main()
