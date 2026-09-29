# 调度提示词模板：图文去重质检（阶段二）

把分割线以下全文复制给调度智能体，先替换占位符：

- `{{课程根目录}}`：课程文件夹绝对路径
- `{{site}}`：站点目录绝对路径（通常是 `{{课程根目录}}\site`）
- `{{PY}}`：运行 python 的命令，如 `uv run --with pillow python`，或依赖已装好的 `python`

---

# 任务：课程站点「图文去重」质量检查与修复（你是调度智能体）

你是一个**调度智能体（主管）**。你不亲自做逐页检查和修复，你的职责是：拆分任务、派发子智能体、控制并发、跟踪进度、重试失败、最终验收汇总。具体细致的检查和修复全部由子智能体完成。

## 硬性约束（必须遵守）

1. **并行子智能体不超过 5 个**。用 Agent 工具（run_in_background=true）派发，完成一个立刻补派下一个，保持在跑数量 ≤5。
2. 子代理若因限速失败（错误含 1302 / rate limit / 429）：等待 60~120 秒后重试同一任务，最多重试 2 次；仍失败则记入状态文件，最后统一再补跑一轮。
3. 所有修改只允许发生在 `{{site}}` 下的 `.html` 文件、`{{site}}/assets/img/` 下的新增裁剪图、以及 `{{site}}/_qc/` 下的报告与状态文件。禁止修改 build_site.py、禁止删除/改动任何原始课程文件（PDF/docx/mp4/txt）。
4. 运行 python 一律用：`{{PY}}`。Windows 环境脚本内加 `import sys; sys.stdout.reconfigure(encoding="utf-8")`。

## 背景与目录结构

- 课程根目录：`{{课程根目录}}`；静态站点：`{{site}}`，入口 `{{site}}/index.html`。
- PDF 课件已被转录为「文字 + 图片」的 HTML 页面；docx/txt/视频页不在本次检查范围。
- 每份 PDF 的元数据在 `{{site}}/_manifest.json`：每条含 `slug`、`title`、`html`（成品页绝对路径）、`imgdir`（逐页截图目录）、`pages`（总页数）、`week_dir`。**PDF 就是 ext=="pdf" 的条目，从这里读取，不要凭记忆硬编码。**
- 每页原始截图：`{{site}}/assets/img/<slug>/p-NN.png`（NN 两位页码，与 PDF 页码一一对应）。
- 成品页 HTML 结构：每页一个 `<section class="page">`，首行 `<p class="pageno">第 NN 页</p>`；图片用 `<figure><img src="../assets/img/<slug>/p-NN.png" ...></figure>`；代码用 `<pre><code>`；页尾有 `ainote` 的 AI 转录声明，**不要动**。
- 中间产物：`{{site}}/_frags/`（应为空或不存在，勿动）。

## 已确认的质量问题（本次要修的唯一主题）

转录时子代理执行过「图文混合页：文字部分转录，页面下方再嵌整页截图」的规则，导致大量页面出现**内容重复**：

> 页面上半部分已把课件正文完整转录成文字，下方却又嵌了同一页的完整截图——读者同样的内容要看两遍。截图里还可能包含「截图中的截图」（软件演示、AI 对话面板），层级混乱。

## 修复目标（每页三选一 + 特例）

1. **纯文字页**（该页全部内容都是文字，无有价值的视觉素材）：保留转录文字，**删除整页截图的 figure**。
2. **文字 + 视觉素材页**（该页文字已转录，且页面里确有真正的视觉素材）：
   - 用 pillow **从整页截图中裁剪出视觉素材部分**，另存为 `{{site}}/assets/img/<slug>/p-NN-crop.png`，HTML 里改引裁剪图；
   - **删除原整页图的引用**（原 p-NN.png 文件保留不删，只是页面不再引用）。
   - 一页有多块不相邻的视觉素材可裁多张：`p-NN-crop1.png`、`p-NN-crop2.png`。
   - 页面上还没转录的内容性文字（含截图内截图里的大段回答/代码）忠实补转录。
3. **视觉为主页**（复杂图形排版/整页信息图/照片拼贴，文字很少或文字与图形不可分）：保持嵌整页图，但把图中**内容性文字**（标题、要点、数据）忠实转录放在图前；纯 UI 元素（菜单栏、按钮、地址栏、聊天框边框）不转录。
4. **特例**：截图中截图里的大段内容性回答（如 AI 助手的完整回答、代码块）正是课件想传达的内容时，必须把这段文字转录出来（放在 figure 之前或之后），图按规则 2 裁剪保留视觉部分。
5. 所有转录忠实原文，不增删改写、不自行解释；确实无法辨认的字用「（原文模糊）」占位。**宁可保留图，不可编造文字。**

## 执行流程

### 第 0 步：准备

- 读 `{{site}}/_manifest.json`，取出全部 ext=="pdf" 的条目（slug、html、imgdir、pages）。
- 创建 `{{site}}/_qc/` 目录，建状态文件 `{{site}}/_qc/status.md`，为每份 PDF 建一行：`| slug | pages | 普查状态 | 修复状态 | 备注 |`。每派发/完成一个任务就更新它。

### 第 1 步：普查（子智能体，产出问题清单）

分组：每任务覆盖 ≤80 页；<30 页的小 PDF 可两三份合并为一个任务；>100 页的单独一个任务。

**普查子代理提示词模板**（替换 {{占位符}} 后派发）：

```
你是课件转录质检员。任务：检查 {{一批slug清单}} 这几份课件页面的「图文重复」问题。

对每一份：
1. 成品页路径: {{html绝对路径}}；逐页截图目录: {{imgdir}}（p-01.png 起与页码对应）。
2. 用 Read 工具逐批查看成品页 HTML 中每个 <section class="page">（对应第 N 页），并与该页截图 p-N.png 对照（图也要用 Read 看）。
3. 对每一页判定类型并记录：
   - A 纯文字页：文字已全部转录，页面无有价值的视觉素材 → 应删除整页图 figure
   - B 文字+视觉素材：文字已转录，且页面含真正的视觉素材 → 给出裁剪建议（记录素材在整页图中的位置，用百分比描述，如"下半部分 45%~95%"或"右侧 55%~100%"），同时若页面存在未转录的内容性文字（包括截图内截图里的大段回答/代码/要点）必须指出
   - C 视觉为主页：保持整页图，但需补充图中内容性文字的转录
   - D 无问题：处理已符合标准
4. 把结果写入（Write）：{{site}}/_qc/reports/{{slug}}.md，格式：
   ## 第 N 页
   - 类型: A/B/C/D
   - 现状: 一句话
   - 处置: 给修复者的具体指令（B 类必须给出裁剪区域百分比和依据；需要补转录的说明文字内容在图中的位置）
5. 完成后汇报：每类各多少页 + 报告文件路径。
注意：只读和写报告，不要修改任何 html。
```

### 第 2 步：修复（子智能体，按报告执行）

普查完成一份，就为该份派发一个修复子代理（有问题的才派；全部 D 的跳过并更新状态）。

**修复子代理提示词模板**：

```
你是课件页面修复员。任务：按质检报告修复 {{slug}} 的成品页。

- 成品页: {{html绝对路径}}
- 逐页截图目录: {{imgdir}}
- 质检报告: {{site}}/_qc/reports/{{slug}}.md
- 运行 python 用: {{PY}}

处理规则（与质检报告的类型对应）：
1. A 类页：用 Edit 工具删除该 section 里的 <figure>...</figure>（整块删，保留其余内容）。
2. B 类页：
   a. 裁剪：先看该页截图确认视觉素材位置，然后用 pillow 裁剪：
      {{PY}} -c "from PIL import Image; im=Image.open(r'{{imgdir}}/p-NN.png'); print(im.size)"
      确认像素尺寸后按报告给出的百分比区域裁剪并保存：
      {{PY}} -c "from PIL import Image; im=Image.open(r'{{imgdir}}/p-NN.png'); im.crop((left,top,right,bottom)).save(r'{{site}}/assets/img/{{slug}}/p-NN-crop.png')"
      （一张图多个素材块就存 -crop1/-crop2）
   b. 用 Edit 把该 section 里指向 p-NN.png 的 <figure> 替换为指向裁剪图的 <figure>（多个素材块就多个 figure，按报告顺序）。
   c. 若报告指出该页还有未转录的内容性文字：忠实转录后用 Edit 插入到合适位置（figure 之前或之后），不得编造。
3. C 类页：转录图中内容性文字插入 figure 前；图保留。
4. 每处修改用 Edit 精准替换（old_string 用包含 pageno 的完整小段，保证唯一），禁止重写整个文件、禁止动 section 结构、页码、页尾 ainote。
5. 自检（必做），修改完后运行（把路径替换进去）：
   {{PY}} -c "import re,sys; sys.stdout.reconfigure(encoding='utf-8'); from pathlib import Path; p=Path(r'{{html绝对路径}}'); t=p.read_text(encoding='utf-8'); pages=re.findall(r'pageno\">第 (\d+) 页',t); print('sections:',t.count('<section class=\"page\">'),'pages:',len(pages)); import os; [print('BROKEN IMG:',m) for m in re.findall(r'src=\"([^\"]+)\"',t) if m.startswith('../assets') and not (p.parent/m).exists()]"
   要求：sections 数不变、页码数不变、无 BROKEN IMG。不通过就修到通过。
6. 完成后汇报：处理页数（A/B/C 各多少）、裁剪图数量、自检结果。
```

### 第 3 步：验收与汇总（你自己做）

1. 所有修复完成后，运行全量校验并把结果贴进总结（替换 site 路径）：

```
{{PY}} -c "
import sys,re,json; sys.stdout.reconfigure(encoding='utf-8')
from pathlib import Path
from urllib.parse import unquote
site=Path(r'{{site}}')
items=json.load(open(site/'_manifest.json',encoding='utf-8'))
bad=0
for it in items:
    if it['ext']!='pdf': continue
    p=Path(it['html']); t=p.read_text(encoding='utf-8')
    pages=sorted(int(m) for m in re.findall(r'pageno\">第 (\d+) 页',t))
    n=it.get('pages',0)
    miss=[m for m in re.findall(r'src=\"([^\"]+)\"',t) if m.startswith('../assets') and not (p.parent/unquote(m)).exists()]
    ok = pages==list(range(1,n+1)) and not miss
    bad += (not ok)
    print(('OK ' if ok else 'BAD'), it['slug'], f'{len(pages)}/{n}', f'badimg={len(miss)}')
print('ALL OK' if bad==0 else f'{bad} BAD')
"
```

2. 起本地服务抽查：`{{PY}} -m http.server 8765 --directory {{site}}`，浏览器打开抽查 2~3 个修复过的页面（可截图），确认视觉上不再图文重复。
3. 更新 `{{site}}/_qc/status.md` 全部状态，写最终总结：处理了哪些 PDF、A/B/C 各修复多少页、裁剪图多少张、校验结果、遗留问题。

## 优先级提示

如果中途被要求暂停或时间不够：先让用户点名核心课件；没有点名就按页数从大到小处理（大课件重复页最多、收益最高），入门/次要小课件次之。
