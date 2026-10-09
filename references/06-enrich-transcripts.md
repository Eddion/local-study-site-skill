# 文字稿提炼：小标题 + 重点加粗 + 轻校对

## 为什么要有

ASR 直出的文字稿是"句子流水"：没有结构、没有重点、还有同音字错误，读起来费劲。
提炼这一步用宿主 Agent 的语言能力做后处理：归纳小标题、给关键句加粗、轻校对错别字——
内容仍然忠实原音频，只是"好读"了。

## 流程

前置：每节视频/音频已有转录 JSON（`site/_transcripts/<slug>.json`，含
`segments[{start,end,text}]`）。音频转录本身参考 `references/01-transcribe.md` 的
"按课程类型选择首轮策略"（音频按时间戳 ASR + 抽查），本技能默认用本机
faster-whisper（`large-v3` + CUDA 批量推理，133 节/11.2 小时音频约十几分钟转完，
可用 uv 建独立环境，模型走国内镜像 `HF_ENDPOINT=https://hf-mirror.com`；
注意 PyAV 需 <15，否则 faster-whisper 报 `metadata_errors` 参数错误）。

1. **分块**：把 slug 清单按每 10 个文件一块切成任务（133 节 → 14 块），写
   `site/_transcripts_enriched/_prompts.txt` 和 `status.md`（与转录阶段同一套
   调度纪律：≤5 并发、完成即补派、状态落盘、失败重试 2 次）。
2. **派发提炼子代理**，提示词要点（全文见下）：
   - 读输入 JSON → 写 `site/_transcripts_enriched/<slug>.json`
   - 输出格式：`{"slug","title","blocks":[{"t":"h2","x":"小标题"} |
     {"t":"cue","s":起点,"e":终点,"x":"文本"}]}`
   - **cue 与 segments 一一对应**：时间戳原样保留，条数不变——点击跳转靠它
   - 小标题 4~14 字，约每 2~4 分钟一个，放主题边界；开头放一个开场标题
   - 每条 cue 最多 1~2 处 `<strong>`，宁少勿滥；文本内不得出现其他标签/换行
   - 轻校对仅限明显同音字/错别字（如"情商客"→"情商课"），口语、语气词、
     重复一律保留，不删减、不改写、不概括
3. **校验**：跑 `scripts/validate_enriched.py`（复制到课程根目录），全量核对——
   - enriched 文件数 = 视频数；JSON 可解析；slug/title 与 manifest 一致
   - 每个 enriched 的 cue 条数 = 原 segments 条数，且 s/e 序列逐一相等
   - x 中除 `<strong>` 外无其他 HTML 标签、无换行；每个文件至少一个 h2
4. **重建页面**：跑 `build_site.py videos`（或 index/merge）——渲染时优先读
   `_transcripts_enriched/`，没有该目录自动退回原始转录，互不覆盖。

## 渲染行为（build_site.py 已内置）

- `_transcripts_enriched/<slug>.json` 存在 → 按 blocks 渲染：h2 作小节标题，
  cue 为可点击句子（点击跳转视频、播放时高亮跟随）；
- 否则 `_transcripts/<slug>.json` 存在 → 原始句子流按句末标点细分后渲染；
- 两者都无 → 页面显示"文字稿转录中"。
- cue 文本先整体 HTML 转义、再放行 `<strong>`，子代理即使忘写转义也不会注入。

## 学习热力图（同批交付）

首页目录 hero 下方内置 GitHub 风格学习热力图（近 5 周，格子颜色 = 当天观看分钟数）：

- 视频页内嵌 `WATCH_TRACK_JS`：播放中的秒数按自然日累计进
  `localStorage["c2sw:年-月-日"]`（每 10 秒与暂停/离开时落盘）；
- index 内嵌渲染脚本：读最近 35 天记录，分 5 档上色（≤10/≤25/≤45/>45 分钟），
  顶部汇总"近 5 周共 X 分钟，学习 Y 天"，月份标签自动标在换月的第一列。
- 全部内联 JS、纯本地；`file://` 下 localStorage 各浏览器行为不一，提示文案
  已注明"仅存本浏览器"，不要当唯一数据源。
