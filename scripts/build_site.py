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
TRANSCRIPTS = SITE / "_transcripts"            # 原始转录 JSON（可选，ASR 产出）
ENRICHED = SITE / "_transcripts_enriched"      # 提炼版转录 JSON（可选，优先渲染）
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


def nat_key(p: Path):
    """自然排序：按文件名开头的集数数字排序（1,2,...,10,11 而非 1,10,100,11）。
    网盘课程的文件名几乎都带集数前缀，字典序会把 10 排在 2 前面，必须用数字序。"""
    m = re.match(r"(\d+)", p.stem)
    return (int(m.group(1)) if m else 10**9, p.name)


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
        subgroups = [wd] + sorted((d for d in wd.iterdir() if d.is_dir()), key=nat_key)
        for g in subgroups:
            for f in sorted(g.iterdir(), key=nat_key):
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
    for f in sorted(BASE.iterdir(), key=nat_key):
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
        tr = transcript_of(item)
        if tr:
            body += ('<p class="hint">文字稿由 AI 从音频转录并提炼排版，可能存在少量误差。</p>' + tr)
        else:
            body += '<p class="hint">暂无文字稿（转录完成后重新生成页面即可嵌入）。</p>'
        body += WATCH_TRACK_JS
    elif item["ext"] == "ts":
        body = (f'<p class="hint">浏览器无法直接播放 .ts 格式，点击下载后用本地播放器（如 VLC、PotPlayer）打开。</p>'
                f'<p><a class="dl" href="{rel}" download>⬇ 下载视频文件（{item["size_mb"]} MB）</a></p>')
    else:  # jpg
        body = f'<img class="solo" src="{rel}" alt="{html_mod.escape(item["title"])}">'
    write_page(item["_html"], item["title"], crumb_of(item), body)


def crumb_of(item):
    return item["week_dir"] + (f" · {item['group']}" if item["group"] else "")


# ---------------------------------------------------------------- transcript
def expand_segments(segments):
    """把 ASR 长段按句末标点拆成句子级 cue，时间按字数比例插值（供点击跳转）。"""
    out = []
    for s in segments:
        text = (s.get("text") or "").strip()
        if not text:
            continue
        dur = s["end"] - s["start"]
        parts = [p for p in re.split(r"(?<=[。！？!?；;])", text) if p.strip()]
        if len(parts) <= 1 or dur <= 0:
            out.append({"start": s["start"], "end": s["end"], "text": text})
            continue
        total = sum(len(p) for p in parts)
        t = s["start"]
        for p in parts:
            share = dur * len(p) / total
            out.append({"start": round(t, 2), "end": round(t + share, 2), "text": p.strip()})
            t += share
    return out


def fmt_ts(sec):
    sec = int(sec)
    return f"{sec//60:02d}:{sec%60:02d}"


def render_blocks(data):
    """把转录 JSON 渲染成 HTML。原始版按 segments 拆句；提炼版按 blocks
    （h2 小标题 / cue 可点击句子）。cue 文本先整体转义、再放行 <strong>。"""
    if "blocks" in data:
        blocks = data["blocks"]
    else:
        blocks = [{"t": "cue", **s} for s in expand_segments(data.get("segments", []))]
    cues = []
    for b in blocks:
        if b.get("t") == "h2":
            cues.append(f'<h2 class="trh2">{html_mod.escape(str(b.get("x", "")))}</h2>')
            continue
        s, e = float(b.get("s", b.get("start", 0))), float(b.get("e", b.get("end", 0)))
        t = html_mod.escape(str(b.get("x", b.get("text", ""))))
        if not t:
            continue
        t = t.replace("&lt;strong&gt;", "<strong>").replace("&lt;/strong&gt;", "</strong>")
        cues.append(f'<p class="cue" data-start="{s}" data-end="{e}">'
                    f'<span class="ts">{fmt_ts(s)}</span>{t}</p>')
    if not cues:
        return None
    return (f'<section class="transcript"><h2>文字稿</h2>'
            f'<p class="hint">点击任意一句可跳转视频到对应位置</p>{"".join(cues)}</section>'
            f"""<script>
(function(){{
  var v=document.querySelector('video');if(!v)return;
  var cues=[].slice.call(document.querySelectorAll('.cue')),last=null;
  cues.forEach(function(c){{c.addEventListener('click',function(){{
    v.currentTime=parseFloat(c.dataset.start);v.play();}});}});
  v.addEventListener('timeupdate',function(){{
    var t=v.currentTime,cur=null;
    for(var i=0;i<cues.length;i++){{
      if(t>=+cues[i].dataset.start-0.15&&t<+cues[i].dataset.end+0.15){{cur=cues[i];break;}}
    }}
    if(cur!==last){{if(last)last.classList.remove('active');if(cur){{cur.classList.add('active');
      if(window.__cueFollow!==false)cur.scrollIntoView({{block:'nearest',behavior:'smooth'}});}}last=cur;}}
  }});
}})();
</script>""")


def transcript_of(item):
    """读转录 JSON：优先提炼版（_transcripts_enriched，含小标题/加粗），
    否则退回原始版（_transcripts，纯句子流）。有则返回 HTML，无则 None。"""
    for d in (ENRICHED, TRANSCRIPTS):
        f = d / f"{item['slug']}.json"
        if f.exists():
            try:
                data = json.loads(f.read_text(encoding="utf-8"))
            except Exception:
                continue
            body = render_blocks(data)
            if body:
                return body
    return None


# 视频页观看时长记录：播放中的秒数按自然日累计进 localStorage（键 c2sw:年-月-日），
# 供 index.html 的学习热力图读取。file:// 下 localStorage 各浏览器行为不一，仅作参考。
WATCH_TRACK_JS = """<script>
(function(){
  var v=document.querySelector('video');if(!v)return;
  var acc=0,last=null,timer=null;
  function dkey(){var d=new Date();return 'c2sw:'+d.getFullYear()+'-'+(d.getMonth()+1)+'-'+d.getDate();}
  function flush(){
    if(acc<=0)return;
    try{var p=parseFloat(localStorage.getItem(dkey())||'0');
      localStorage.setItem(dkey(),(p+acc).toFixed(1));}catch(e){}
    acc=0;
  }
  function tick(){if(last){acc+=(Date.now()-last)/1000;last=Date.now();flush();}}
  v.addEventListener('play',function(){last=Date.now();if(!timer)timer=setInterval(tick,10000);});
  v.addEventListener('pause',function(){if(last){acc+=(Date.now()-last)/1000;last=null;}flush();});
  v.addEventListener('ended',function(){if(last){acc+=(Date.now()-last)/1000;last=null;}flush();});
  document.addEventListener('visibilitychange',function(){
    if(document.hidden&&last){acc+=(Date.now()-last)/1000;last=null;flush();}});
  window.addEventListener('pagehide',function(){if(last){acc+=(Date.now()-last)/1000;last=null;}flush();});
})();
</script>"""


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
/* study plan (Week/Day) */
.week h2 .wkmeta{margin-left:.8em;font-size:.85rem;color:#8b949e;font-weight:normal}
.dayhd{display:flex;align-items:baseline;gap:.8rem;margin:.9rem 0 .3rem;padding:.45rem .9rem;background:#f6f8fa;border-left:3px solid #0969da;border-radius:4px;font-weight:600}
.dayhd .dmin{margin-left:auto;font-size:.8rem;color:#8b949e;font-weight:normal;white-space:nowrap}
/* transcript */
.trh2{font-size:1.05rem;margin:1.4rem 0 .4rem;padding-left:.55rem;border-left:3px solid #0969da}
.transcript{margin-top:1.6rem}
.transcript .cue{margin:.15rem 0;padding:.28rem .6rem;border-radius:6px;cursor:pointer;font-size:.95rem}
.transcript .cue:hover{background:#f0f4ff}
.transcript .cue.active{background:#fff3cd}
.transcript .ts{display:inline-block;min-width:3.2em;margin-right:.6em;color:#8b949e;font-size:.82rem;font-variant-numeric:tabular-nums}
/* study heatmap (GitHub style) */
.heatmap{max-width:960px;margin:0 auto 1.5rem;padding:1rem 1.2rem;background:#fff;border:1px solid #e4e7eb;border-radius:12px}
.hmtitle{margin:0 0 .8rem;font-weight:600}
.hmtitle #hm-sum{color:#57606a;font-weight:normal}
.hm-wrap{display:flex;gap:.6rem}
.hm-months{display:grid;grid-template-columns:repeat(5,15px);gap:3px;font-size:.72rem;color:#8b949e;margin-left:1.6rem;height:1em}
.hm-months span{overflow:visible;white-space:nowrap}
.hm-body{display:flex;gap:.35rem}
.hm-days{display:grid;grid-template-rows:repeat(7,13px);gap:3px;font-size:.72rem;color:#8b949e}
.hm-grid{display:grid;grid-template-rows:repeat(7,13px);grid-auto-flow:column;grid-auto-columns:13px;gap:3px}
.hm{display:inline-block;width:13px;height:13px;border-radius:3px;vertical-align:-2px}
.hm.c0{background:#ebedf0}.hm.c1{background:#9be9a8}.hm.c2{background:#40c463}.hm.c3{background:#30a14e}.hm.c4{background:#216e39}
.hm-legend{margin:.8rem 0 0;color:#8b949e;font-size:.8rem;display:flex;align-items:center;gap:4px}
.hm-legend .hmnote{margin-left:auto}
"""


HEATMAP_HTML = """
<section class="heatmap"><p class="hmtitle">📊 学习热力图 · <span id="hm-sum">加载中…</span></p>
<div class="hm-wrap"><div id="hm-months" class="hm-months"></div><div class="hm-body">
<div class="hm-days"><span>一</span><span></span><span>三</span><span></span><span>五</span><span></span><span></span></div>
<div id="hm-grid" class="hm-grid"></div></div></div>
<p class="hm-legend">少 <span class="hm c0"></span><span class="hm c1"></span><span class="hm c2"></span><span class="hm c3"></span><span class="hm c4"></span> 多
<span class="hmnote">在视频页观看时自动按天累计（仅存本浏览器）</span></p>
</section>
<script>
(function(){
  function dkey(d){return 'c2sw:'+d.getFullYear()+'-'+(d.getMonth()+1)+'-'+d.getDate();}
  var today=new Date();today.setHours(0,0,0,0);
  var dow=(today.getDay()+6)%7;               /* 周一=0 */
  var start=new Date(today);start.setDate(today.getDate()-dow-28);  /* 5 周前的周一 */
  var grid=document.getElementById('hm-grid');if(!grid)return;
  var total=0,days=0,cells=[],months=[],lastMonth=null;
  for(var i=0;i<35;i++){
    var d=new Date(start);d.setDate(start.getDate()+i);
    var v=parseFloat(localStorage.getItem(dkey(d))||'0');
    if(v>0){total+=v;days++;}
    var lv=v<=0?0:v<=10?1:v<=25?2:v<=45?3:4;
    cells.push('<span class="hm c'+lv+'" title="'+(d.getMonth()+1)+'月'+d.getDate()+'日：'+Math.round(v)+' 分钟"></span>');
    if(i%7===0){var m=d.getMonth()+1;months.push(m!==lastMonth?('<span>'+(m)+'月</span>'):'<span></span>');lastMonth=m;}
  }
  grid.innerHTML=cells.join('');
  document.getElementById('hm-months').innerHTML=months.join('');
  document.getElementById('hm-sum').textContent='近 5 周共 '+Math.round(total)+' 分钟，学习 '+days+' 天';
})();
</script>
"""


# ---------------------------------------------------------------- index
def item_row(it):
    badge, cls = BADGES[it["ext"]]
    exists = it["_html"].exists()
    if exists:
        link = f'<a href="{href_of(it["_html"])}">{html_mod.escape(it["title"])}</a>'
    else:
        link = f'<span class="badge b-miss">AI转录中</span> {html_mod.escape(it["title"])}'
        badge, cls = "待生成", "b-miss"
    size = f' · {it["size_mb"]} MB' if it["ext"] in ("mp4", "ts") else ""
    grp = f'<span class="grp">{it["ext"]}{size}</span>'
    return f'<div class="item"><span class="badge {cls}">{badge}</span>{link}{grp}</div>'


def planned_parts(items):
    """有 _plan.json（make_plan.py 产出）时按 Week/Day 学习计划渲染目录；
    没有则返回 None，退回按目录分组的平铺渲染。"""
    f = SITE / "_plan.json"
    if not f.exists():
        return None
    plan = json.loads(f.read_text(encoding="utf-8"))
    by_slug = {it["slug"]: it for it in items}
    planned = set()
    parts = []
    for wk in plan["weeks"]:
        rows = []
        for d in wk["days"]:
            rows.append(f'<p class="dayhd">Day {d["day"]} · {html_mod.escape(str(d["title"]))}'
                        f'<span class="dmin">视频约 {d["min"]:.0f} 分钟 · 建议配 {60 - d["min"]:.0f} 分钟笔记内化</span></p>')
            for slug in d["items"]:
                it = by_slug.get(slug)
                if not it:
                    continue
                planned.add(slug)
                rows.append(item_row(it))
        parts.append(f'<section class="week"><h2>Week {wk["week"]} · {html_mod.escape(str(wk["title"]))}'
                     f'<span class="wkmeta">Day {wk["days"][0]["day"]}–{wk["days"][-1]["day"]}</span></h2>'
                     f'<div class="items">{"".join(rows)}</div></section>')
    rest = [it for it in items if it["slug"] not in planned]
    if rest:
        rows = "".join(item_row(it) for it in rest)
        parts.append(f'<section class="week"><h2>其他资料</h2><div class="items">{rows}</div></section>')
    return parts, plan


def build_index(items):
    planned = planned_parts(items)
    if planned:
        plan_sections, plan = planned
        hero_line = f'{plan.get("note", "学习计划")} · 共 {plan.get("total", "")} · 点击条目学习 · 视频可直接播放'
    else:
        plan_sections = None
        hero_line = f'共 {len(items)} 个资料 · 点击条目学习 · 视频可直接播放'
    parts = [f'<div class="hero"><h1>{COURSE_TITLE}</h1><p>{hero_line}</p></div>', HEATMAP_HTML]

    if plan_sections:
        parts.extend(plan_sections)
    else:
        weeks = {}
        for it in items:
            weeks.setdefault((it["week"], it["week_dir"]), []).append(it)
        for (wk, wdir), its in sorted(weeks.items()):
            label = wdir if wk else "附 · 其他资料"
            rows = []
            last_grp = None
            for it in its:
                if it["group"] and it["group"] != last_grp:
                    rows.append(f'<p class="sub">— {html_mod.escape(it["group"])} —</p>')
                last_grp = it["group"]
                rows.append(item_row(it))
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

    if cmd == "videos":
        # 重生成媒体页（视频页是脚本产物，可安全覆盖），把已完成的转录/提炼嵌入页面
        n = 0
        for it in items:
            if it["ext"] in ("mp4", "ts"):
                conv_media(it); n += 1
        print(f"[videos] regenerated {n} media pages")
        build_index(items)
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
