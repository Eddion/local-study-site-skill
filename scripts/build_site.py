# -*- coding: utf-8 -*-
"""
课程静态站构建脚本（放到课程根目录运行）
用法:
  python build_site.py build     # 渲染PDF页图 + 转换docx/txt/媒体 + 写manifest和转录任务提示词
  python build_site.py merge     # 片段覆盖全部页码的PDF拼装成品页 + 刷新index
  python build_site.py index     # 生成index.html + 修剪未引用图片
  python build_site.py fallback  # 为还没有成品页的PDF生成纯截图兜底页
依赖: pymupdf(PDF渲染) mammoth(docx) pillow(阶段二裁剪)
  uv run --with pymupdf --with mammoth --with pillow python build_site.py build
已存在的 html 不会被覆盖（保护子代理转录产物）。
"""
import json
import re
import sys
import html as html_mod
import os
from pathlib import Path
from urllib.parse import quote

BASE = Path(__file__).resolve().parent
SITE = BASE / "site"
ASSETS = SITE / "assets"
IMG = ASSETS / "img"
FRAGS = SITE / "_frags"
COURSE_TITLE = "我的课程"  # ← 改成你的课程名
WEEK_DIR_RE = re.compile(r"^\d+\.")  # ← 周目录名正则，默认匹配 01.xxx / 02.xxx，不符合就改
CHUNK = 40  # 每个转录子代理负责的PDF页数

sys.stdout.reconfigure(encoding="utf-8")

BADGES = {
    "pdf": ("课件", "b-pdf"),
    "docx": ("文档", "b-doc"),
    "txt": ("提示词", "b-prompt"),
    "mp4": ("视频", "b-video"),
    "ts": ("视频·下载", "b-video"),
    "jpg": ("图片", "b-img"),
}


def sanitize(name: str) -> str:
    return re.sub(r'[\\/:*?"<>|\s]+', "-", name).strip("-.")


def week_no_of(name: str) -> int:
    """从目录名提取周号：取第一个数字串（'01.xxx'→1，'第1周'→1）。"""
    m = re.search(r"\d+", name)
    return int(m.group()) if m else 0


def pdf_page_count(src: str):
    """权威页数直接读 PDF 原件——磁盘截图可能缺失（被清理）或混入裁剪图，数不可靠。"""
    try:
        import fitz
        with fitz.open(src) as doc:
            return doc.page_count
    except Exception:
        return None


def href_of(path: Path) -> str:
    return quote(path.relative_to(SITE).as_posix())


# ---------------------------------------------------------------- manifest
def build_manifest():
    items = []
    week_dirs = sorted(d for d in BASE.iterdir()
                       if d.is_dir() and WEEK_DIR_RE.match(d.name))
    for wd in week_dirs:
        week_no = week_no_of(wd.name)
        subgroups = [wd] + sorted(d for d in wd.iterdir() if d.is_dir())
        for g in subgroups:
            for f in sorted(g.iterdir()):
                if not f.is_file():
                    continue
                ext = f.suffix.lower().lstrip(".")
                if ext not in BADGES:
                    continue
                items.append({
                    "week": week_no,
                    "week_dir": wd.name,
                    "group": None if g == wd else g.name,
                    "title": f.stem,
                    "ext": ext,
                    "src": str(f.resolve()),
                    "size_mb": round(f.stat().st_size / 1048576, 1),
                })
    # 根目录散落文件
    for f in sorted(BASE.iterdir()):
        if f.is_file() and f.suffix.lower().lstrip(".") in BADGES:
            items.append({
                "week": 0,
                "week_dir": "00.附-其他资料",
                "group": None,
                "title": f.stem,
                "ext": f.suffix.lower().lstrip("."),
                "src": str(f.resolve()),
                "size_mb": round(f.stat().st_size / 1048576, 1),
            })
    # 分配 slug / 输出路径
    # manifest 只存相对路径：src 相对课程根目录，html/imgdir 相对 site/（搬迁友好、不泄露本机目录结构）
    for i, it in enumerate(items, 1):
        slug = f"w{it['week']:02d}-{i:03d}"
        it["slug"] = slug
        if it["ext"] == "pdf":
            pages_now = pdf_page_count(it["src"])  # src 此刻还是绝对路径
            it["imgdir"] = f"assets/img/{slug}"
            if pages_now is None:  # PDF 打不开时退回数截图（严格匹配，裁剪图不算页）
                d = IMG / slug
                pages_now = len([p for p in d.iterdir()
                                 if re.fullmatch(r"p-\d+\.png", p.name)]) if d.exists() else 0
            it["pages"] = pages_now
        it["src"] = Path(it["src"]).relative_to(BASE).as_posix()
        it["html"] = (Path(it["week_dir"]) / f"{slug}-{sanitize(it['title'])}.html").as_posix()
    return items


def to_abs(items):
    """运行期内部统一用绝对路径；manifest 落盘前剥掉下划线开头的临时字段。"""
    for it in items:
        it["_src"] = BASE / it["src"]
        it["_html"] = SITE / it["html"]
        if it["ext"] == "pdf":
            it["_imgdir"] = SITE / it["imgdir"]


# ---------------------------------------------------------------- template
PAGE_TMPL = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{title} · {course}</title>
<link rel="stylesheet" href="{rel}assets/style.css">
</head>
<body>
<nav class="topbar"><a href="{rel}index.html">← 返回目录</a><span class="crumb">{crumb}</span></nav>
<main>
<h1>{title}</h1>
{content}
</main>
<footer><a href="{rel}index.html">← 返回目录</a></footer>
</body>
</html>
"""


def write_page(html_path: str, title: str, crumb: str, content: str, ai_note=False):
    p = Path(html_path)
    rel = "../" * (len(p.relative_to(SITE).parts) - 1)
    note = '<p class="ainote">本页内容由 AI 从原始文件转录生成，可能有少量误差，请以原始文件为准。</p>' if ai_note else ""
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(PAGE_TMPL.format(
        title=html_mod.escape(title), course=COURSE_TITLE,
        rel=rel, crumb=html_mod.escape(crumb), content=content + note), encoding="utf-8")


# ---------------------------------------------------------------- converters
def render_pdf(item):
    import fitz
    imgdir = Path(item["_imgdir"])
    imgdir.mkdir(parents=True, exist_ok=True)
    doc = fitz.open(item["_src"])
    n = 0
    for i, page in enumerate(doc, 1):
        out = imgdir / f"p-{i:02d}.png"
        if out.exists():
            n += 1
            continue
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(2.0, 2.0))
            pix.save(out)
            n += 1
        except Exception as e:
            print(f"  [render fail] page {i}: {e}")
    total = doc.page_count
    doc.close()
    return total, n


def conv_docx(item):
    import mammoth
    with open(item["_src"], "rb") as f:
        result = mammoth.convert_to_html(f)
    write_page(item["_html"], item["title"], crumb_of(item),
               f'<article class="doc">{result.value}</article>')
    for w in result.messages[:3]:
        print(f"  [mammoth] {w.type}: {str(w)[:80]}")


def conv_txt(item):
    raw = item["_src"].read_bytes()
    text = None
    for enc in ("utf-8", "gb18030", "utf-16"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    if text is None:
        text = raw.decode("utf-8", errors="replace")
    body = f'<pre class="prompt">{html_mod.escape(text)}</pre>'
    write_page(item["_html"], item["title"], crumb_of(item), body, ai_note=False)


def conv_media(item):
    p = item["_src"]
    rel = quote(os.path.relpath(p, item["_html"].parent).replace("\\", "/"))
    if item["ext"] == "mp4":
        body = (f'<p class="hint">视频 {item["size_mb"]} MB · 可直接在线播放</p>'
                f'<video controls preload="metadata" src="{rel}"></video>')
    elif item["ext"] == "ts":
        body = (f'<p class="hint">浏览器无法直接播放 .ts 格式，点击下载后用本地播放器（如 VLC、PotPlayer）打开。</p>'
                f'<p><a class="dl" href="{rel}" download>⬇ 下载视频文件（{item["size_mb"]} MB）</a></p>')
    else:  # jpg
        body = f'<img class="solo" src="{rel}" alt="{html_mod.escape(item["title"])}">'
    write_page(item["_html"], item["title"], crumb_of(item), body)


def crumb_of(item):
    return item["week_dir"] + (f" · {item['group']}" if item["group"] else "")


# ---------------------------------------------------------------- css
STYLE = """*{box-sizing:border-box}body{margin:0;font-family:system-ui,"Microsoft YaHei",sans-serif;background:#f6f7f9;color:#24292f;line-height:1.75}
a{color:#0969da;text-decoration:none}a:hover{text-decoration:underline}
.topbar{position:sticky;top:0;background:#ffffffee;backdrop-filter:blur(6px);border-bottom:1px solid #e4e7eb;padding:.6rem 1.2rem;display:flex;gap:1rem;align-items:center;z-index:9}
.crumb{color:#8b949e;font-size:.9rem}
main{max-width:900px;margin:1.5rem auto 3rem;padding:0 1.2rem;background:#fff;border:1px solid #e4e7eb;border-radius:12px;padding:2rem 2.4rem}
h1{font-size:1.5rem;border-bottom:1px solid #e4e7eb;padding-bottom:.6rem}
h2{font-size:1.2rem;margin-top:2rem}
.pageno{color:#8b949e;font-size:.85rem;margin:2.2rem 0 .2rem;border-top:1px dashed #e4e7eb;padding-top:1rem}
figure{margin:1rem 0;text-align:center}figure img,.slide img,.solo{max-width:100%;border:1px solid #e4e7eb;border-radius:8px}
pre{background:#0d1117;color:#e6edf3;padding:1.2rem;border-radius:10px;overflow-x:auto;white-space:pre-wrap;word-break:break-word;font-size:.88rem;line-height:1.6}
code{background:#eff1f3;padding:.1em .4em;border-radius:4px;font-size:.9em}
pre code{background:none;padding:0}
table{border-collapse:collapse;margin:1rem 0}th,td{border:1px solid #d0d7de;padding:.45rem .8rem}th{background:#f6f8fa}
video{width:100%;border-radius:10px;background:#000}
.hint{color:#8b949e;font-size:.92rem}
.dl{display:inline-block;padding:.6rem 1.2rem;border:1px solid #0969da;border-radius:8px}
.doc img{max-width:100%}
.ainote{margin-top:3rem;color:#8b949e;font-size:.82rem;border-top:1px solid #e4e7eb;padding-top:.8rem}
footer{max-width:900px;margin:0 auto 3rem;padding:0 1.2rem;color:#8b949e}
/* index */
.hero{max-width:960px;margin:2rem auto 1rem;padding:0 1.2rem}
.hero h1{border:none;font-size:1.8rem}.hero p{color:#57606a}
.week{max-width:960px;margin:0 auto 1.5rem;padding:0 1.2rem}
.week h2{font-size:1.15rem;margin:0 0 .6rem}
.items{background:#fff;border:1px solid #e4e7eb;border-radius:12px;overflow:hidden}
.item{display:flex;align-items:center;gap:.8rem;padding:.65rem 1.2rem;border-bottom:1px solid #f0f2f4;margin:0}
.item:last-child{border-bottom:none}
.item .grp{color:#8b949e;font-size:.8rem;margin-left:auto;white-space:nowrap}
.badge{font-size:.75rem;padding:.15rem .55rem;border-radius:99px;white-space:nowrap}
.b-pdf{background:#fdf0e6;color:#b3541e}.b-doc{background:#e8f0fe;color:#1a56b8}
.b-prompt{background:#e6f7ee;color:#0f7b3e}.b-video{background:#f3e8fd;color:#6b21a8}
.b-img{background:#fff3cd;color:#8a6d00}.b-miss{background:#ffebe9;color:#c0392b}
.sub{margin-left:1.2rem;color:#8b949e}
"""


# ---------------------------------------------------------------- index
def build_index(items):
    weeks = {}
    for it in items:
        weeks.setdefault((it["week"], it["week_dir"]), []).append(it)

    parts = [f'<div class="hero"><h1>{COURSE_TITLE}</h1><p>共 {len(items)} 个资料 · 点击条目学习 · 视频可直接播放</p></div>']
    for (wk, wdir), its in sorted(weeks.items()):
        label = wdir if wk else "附 · 其他资料"
        rows = []
        last_grp = None
        for it in its:
            if it["group"] and it["group"] != last_grp:
                rows.append(f'<p class="sub">— {html_mod.escape(it["group"])} —</p>')
            last_grp = it["group"]
            badge, cls = BADGES[it["ext"]]
            exists = it["_html"].exists()
            if exists:
                link = f'<a href="{href_of(it["_html"])}">{html_mod.escape(it["title"])}</a>'
            else:
                link = f'<span class="badge b-miss">AI转录中</span> {html_mod.escape(it["title"])}'
                badge, cls = "待生成", "b-miss"
            size = f' · {it["size_mb"]} MB' if it["ext"] in ("mp4", "ts") else ""
            grp = f'<span class="grp">{it["ext"]}{size}</span>'
            rows.append(f'<div class="item"><span class="badge {cls}">{badge}</span>{link}{grp}</div>')
        parts.append(f'<section class="week"><h2>{html_mod.escape(label)}</h2><div class="items">{"".join(rows)}</div></section>')

    (SITE / "index.html").write_text(PAGE_TMPL.format(
        title="目录", course=COURSE_TITLE, rel="", crumb="首页",
        content="".join(parts)), encoding="utf-8")
    print(f"index.html written, {len(items)} items")


def prune_images(items):
    """清理未被引用的原始页图。保护规则：PDF 还没有转录成品页（或仍是兜底页）时，
    它的全部页图一律保留——后续转录分块和阶段二质检随时要回读原图；只有已确认
    完成转录的 PDF 才清理其未被引用的原始页图（例如被裁剪图替换后）。裁剪图等
    衍生图永不自动删除。"""
    referenced = set()
    for h in SITE.rglob("*.html"):
        for m in re.findall(r'assets/img/([\w\-]+/p-[\d]+\.png)', h.read_text(encoding="utf-8")):
            referenced.add(m.replace("\\", "/"))
    protected = set()
    for it in items:
        if it["ext"] != "pdf":
            continue
        h = it["_html"]
        if h.exists() and "<!-- img-fallback -->" not in h.read_text(encoding="utf-8"):
            continue  # 已有转录成品页，允许清理其未引用原图
        d = IMG / it["slug"]
        if d.exists():
            for f in d.glob("*.png"):
                protected.add(f.as_posix().replace("\\", "/").split("assets/img/")[-1])
    removed = kept = 0
    for d in IMG.iterdir() if IMG.exists() else []:
        for f in d.glob("*.png"):
            key = f.as_posix().replace("\\", "/").split("assets/img/")[-1]
            if key in referenced or key in protected or not re.fullmatch(r"p-\d+\.png", f.name):
                kept += 1
            else:
                removed += 1
                f.unlink()
        if d.exists() and not any(d.iterdir()):
            d.rmdir()
    print(f"pruned {removed} unreferenced page images, kept {kept}")


# ---------------------------------------------------------------- chunks
def pdf_chunks(item):
    """[(part_no, start, end)] 每 CHUNK 页一块"""
    pages = item.get("pages", 0)
    return [(k, (k - 1) * CHUNK + 1, min(k * CHUNK, pages))
            for k in range(1, (pages + CHUNK - 1) // CHUNK + 1)]


def frag_final_html(item):
    """所有片段按 part 顺序拼接后，页码必须按阅读顺序恰为 1..pages 才拼装成品页
    （缺页、重复页、错序页一律拒绝，保留片段待修复），防止坏结构混进成品。"""
    d = FRAGS / item["slug"]
    frags = d.glob("part-*.html") if d.exists() else []
    frags = sorted(frags, key=lambda f: int(re.search(r"part-(\d+)", f.name).group(1)))
    if not frags:
        return False
    pages = item.get("pages", 0)
    seq = []
    for f in frags:
        seq.extend(int(m) for m in re.findall(r'class="pageno">第 (\d+) 页', f.read_text(encoding="utf-8")))
    if pages and seq != list(range(1, pages + 1)):
        missing = sorted(set(range(1, pages + 1)) - set(seq))
        dup = sorted({n for n in seq if seq.count(n) > 1})
        pos = next((i for i in range(min(len(seq), pages)) if seq[i] != i + 1), None)
        if pos is not None:
            hint = f"第 {pos + 1} 个位置应是页 {pos + 1}、实际是页 {seq[pos]}"
        elif len(seq) < pages:
            hint = "片段页数少于总页数"
        else:
            hint = "片段页数超过总页数"
        print(f"[pending] {item['slug']} 片段页码未按顺序完整覆盖 1..{pages}"
              f"（{hint}；缺:{missing[:10]}{'...' if len(missing) > 10 else ''}"
              f" 重复:{dup[:10]}{'...' if len(dup) > 10 else ''}），保留片段待修复")
        return False
    body = "\n".join(f.read_text(encoding="utf-8") for f in frags)
    write_page(item["_html"], item["title"], crumb_of(item), body, ai_note=True)
    for f in frags:
        f.unlink()
    d.rmdir()
    print(f"[merged] {item['slug']} <- {len(frags)} frags, {len(seq)} pages")
    return True


def img_fallback_page(item):
    """全部页截图的兜底页（带标记，可被转录合并覆盖）"""
    if item["_html"].exists():
        return
    rel = "../"
    figs = []
    n = item.get("pages", 0)
    for i in range(1, n + 1):
        figs.append(f'<section class="page"><p class="pageno">第 {i} 页</p>'
                    f'<figure><img src="{rel}assets/img/{item["slug"]}/p-{i:02d}.png" alt="第 {i} 页"></figure></section>')
    write_page(item["_html"], item["title"], crumb_of(item), "\n".join(figs))
    # 追加标记，便于识别这是兜底页
    p = item["_html"]
    p.write_text(p.read_text(encoding="utf-8").replace(
        "</head>", "</head>\n<!-- img-fallback -->"), encoding="utf-8")
    print(f"[fallback] {item['slug']} ({n} pages)")


# ---------------------------------------------------------------- main
def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else "build"
    items = build_manifest()
    to_abs(items)
    SITE.mkdir(exist_ok=True)
    ASSETS.mkdir(exist_ok=True)
    (ASSETS / "style.css").write_text(STYLE, encoding="utf-8")

    if cmd == "index":
        build_index(items)
        prune_images(items)
        return

    if cmd == "fallback":
        for it in items:
            if it["ext"] == "pdf":
                img_fallback_page(it)
        return

    if cmd == "merge":
        for it in items:
            if it["ext"] == "pdf":
                frag_final_html(it)
        build_index(items)
        prune_images(items)
        return

    prompts = []
    for it in items:
        target = it["_html"]
        if target.exists():
            print(f"[skip exists] {target.name}")
            continue
        ext = it["ext"]
        if ext == "pdf":
            total, rendered = render_pdf(it)
            it["pages"] = total  # 权威页数以 PDF 原件为准，个别页渲染失败也不缩小任务范围
            print(f"[pdf rendered] {rendered}/{total} pages -> {it['slug']}")
        elif ext == "docx":
            conv_docx(it); print(f"[docx] {it['title']}")
        elif ext == "txt":
            conv_txt(it); print(f"[txt] {it['title']}")
        else:
            conv_media(it); print(f"[{ext}] {it['title']}")

    (SITE / "_manifest.json").write_text(
        json.dumps([{k: v for k, v in it.items() if not k.startswith("_")} for it in items],
                   ensure_ascii=False, indent=1), encoding="utf-8")

    for it in items:
        if it["ext"] != "pdf":
            continue
        if it["_html"].exists() and "<!-- img-fallback -->" not in it["_html"].read_text(encoding="utf-8"):
            continue  # 已有转录成品
        for k, s, e in pdf_chunks(it):
            prompts.append(f"""=== 任务 {it['slug']}-part{k} | {it['title']} (第{s}-{e}页) ===
源PDF: {it['_src']}
你负责的截图: {it['_imgdir']} 目录下的 p-{s:02d}.png 到 p-{e:02d}.png
输出片段绝对路径: {FRAGS / it['slug'] / f'part-{k}.html'}
""")
    (SITE / "_agent_prompts.txt").write_text(
        "\n".join(prompts) + RULES, encoding="utf-8")
    print(f"manifest + {len(prompts)} chunk prompts written to site/")


RULES = """
================ 通用转录规则（附在每个分块任务后） ================
你在把课件 PDF 的某几页截图转成 HTML 片段（不是完整网页，不要写 <!DOCTYPE/html/head/body>）：
1. 用 Read 工具逐批查看你负责范围的截图（每批 4-6 张，读完再读下一批，直到范围内全部页看完）。
2. 按页判断内容类型，忠实转录，不要增删改写原文、不要自行解释或补充内容：
   - 大段文字为主的页：转录为结构化 HTML（h2/p/ul/ol/table/pre+code 保留代码），保留原文措辞。
   - 图表/截图/流程图/复杂图形排版为主的页：用 <figure><img src="../assets/img/<slug>/p-NN.png" alt="第 N 页"></figure> 保留整页截图（把 <slug> 换成任务名里给出的 slug）。
   - 图文混合页：文字部分转录，页面下方再嵌整页截图。
3. 每页一个 <section class="page">，其内第一行是 <p class="pageno">第 N 页</p>（N 用真实页码，两位数字如 07）。
4. 全部页看完后，用 Write 一次性写出输出片段文件（只含这些 section）。若某页截图不存在（渲染失败），跳过并在汇报中说明页码。
5. 完成后汇报一句话：输出路径 + 文字页数 + 嵌图页数 + 跳过页（如有）。
"""


if __name__ == "__main__":
    main()
