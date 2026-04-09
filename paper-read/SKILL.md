---
name: paper-read
description: 自动爬取 arXiv cs.CV 当天所有新论文，两阶段筛选：先由 agent 读全量摘要快速初筛，再对 Top N 下载 PDF 全文精读；精读阶段从 paper-read-reviewer skill 注入完整 subagent prompt，结合 OpenJudge 五阶段审稿流水线综合打分，生成报告后清理本地 PDF。当用户要求"阅读论文"、"分析今日 arxiv 论文"、"paper-read"、"论文总结"时使用此 skill。
---

# Paper Read — arXiv 论文两阶段解读 + OpenJudge 审稿流水线

## 架构原则

```
脚本：爬取全量元数据（含摘要） → 输出 JSON（快，无需下载 PDF）
         ↓
主 Agent（阶段 1）：读全量摘要 → 快速评分初筛 → 选出 Top N
         ↓
脚本：仅下载 Top N 论文的 PDF
         ↓
主 Agent：每批并行启动 5 个 Task subagent（agent=5）
    └─ subagent × 5（每篇独立 200k 上下文）：
         读 PDF 全文 → 深度分析 → OpenJudge 五阶段 → 写 /tmp/arxiv_results/{id}.json
         ↓（等待本批 5 个全部完成）
主 Agent：收集所有结果 JSON → 综合打分排序 → 生成 Markdown 报告 → 清理
```

- **阶段 1 无需下载任何 PDF**，只读摘要，速度快，覆盖全量
- **精读阶段每篇论文独占一个 Task subagent**，完整 200k 上下文用于读文+审稿，杜绝 token 耗尽截断
- **agent=5 批量并行**：每次同时启动 5 个 subagent，等待本批全部写盘后再启动下一批
- 脚本不做文本提取，不调用任何 LLM

---

## 工作流

```
任务进度：
- [ ] Step 0：[前置检查] 查询最近 7 天是否有过精读，如有则跳过
- [ ] Step 1：安装依赖与创建 papers/{date} 目录
- [ ] Step 2：爬取当天全量论文元数据（含摘要）
- [ ] Step 3：[阶段 1] Agent 读全量摘要，初筛出 Top N
- [ ] Step 4：下载 Top N 论文的 PDF
- [ ] Step 5：[阶段 2] 批量并行启动 Task subagent 精读（agent=5，每批 5 篇并行）
             └─ 每个 subagent：读 PDF 全文 → 深度分析 → OpenJudge 五阶段 → 写结果 JSON
- [ ] Step 6：主 agent 收集全部结果，生成 Markdown 报告并保存至 papers/{date}/
- [ ] Step 7：清理 /tmp 缓存文件（保留 papers/{date} 结果）
```

> **Token 预算说明**：精读 + 审稿是重度 token 任务（单篇 PDF 可达 15-30k tokens），绝不在主 agent 内循环处理所有论文。每篇论文独立分配一个 Task subagent，在 200k 上下文内完整处理完再结束，避免主 agent 耗尽上下文。

---

## Step 0：前置检查 — 最近一周精读校验

在开始任何操作前，主 Agent 必须检查本地 `papers/` 目录：
1. **获取日期**：确定当前日期（YYYY-MM-DD）。
2. **扫描目录**：列出 `papers/` 下所有以日期命名的子目录。
3. **校验逻辑**：如果有任何目录的日期与当前日期的差距 **≤ 7 天**，则认为最近已完成过精读。
4. **决策**：
   - 若发现最近 7 天内有过精读：向用户报告"最近 7 天内已于 {found_date} 完成过论文精读，本次将跳过"，并**中止任务**。
   - 若没有发现：继续执行后续步骤。

---

## Step 1：环境准备

### 1.1 安装依赖
```bash
pip install requests beautifulsoup4
```

### 1.2 创建本地存储目录
```bash
mkdir -p papers/$(date +%Y-%m-%d)
```
后续生成的报告和关键数据将持久化保存于此，而不是随着清理步骤消失。

---

## Step 2：爬取全量元数据

```bash
python ~/.claude/skills/paper-read/scripts/crawl_and_extract.py \
  --category cs.CV \
  --output /tmp/arxiv_all.json \
  --mode meta-only
```

输出：**全部当天论文**的元数据 JSON（标题 + 摘要 + arXiv ID，无 PDF）。

---

## Step 3：阶段 1 — Agent 摘要初筛

读取 `/tmp/arxiv_all.json`，**不下载任何 PDF**，仅根据标题和摘要快速评分。

### 初筛评分矩阵（每篇独立打分，满分 6 分，≥4 分入选精读）

按以下四个维度各自打分后**加总**：

#### A. 方法新颖性（0-2 分）

| 分 | 判断依据 |
|----|---------|
| 2 | 提出全新范式、架构或训练策略（摘要中有"we propose / we introduce / novel framework"等措辞，且不是对已有方法的小修改） |
| 1 | 对现有方法做有意义的改进或组合（如"we extend / we improve"） |
| 0 | 增量工作、纯应用迁移、综述/Survey、数据集论文（无方法创新） |

#### B. 问题重要性（0-2 分）

| 分 | 判断依据 |
|----|---------|
| 2 | 攻克核心 CV 难题：视频生成/理解、3D 重建/生成、多模态感知、基础模型、open-world 检测/分割等高影响力方向 |
| 1 | 重要但非核心方向：特定领域应用（医疗/遥感/工业）、特定子任务优化 |
| 0 | 边缘方向、纯工程优化、与 CV 相关性弱的跨领域论文 |

#### C. 实验强度（0-1 分）

| 分 | 判断依据 |
|----|---------|
| 1 | 摘要中明确提到与 SOTA 对比，且有具体数字（如"surpasses X by Y%"、"achieves state-of-the-art on Z"） |
| 0 | 无具体数字，只说"competitive"或"promising" |

#### D. 机构/作者信号（0-1 分）

| 分 | 判断依据 |
|----|---------|
| 1 | 来自顶级 AI 机构（Google DeepMind / OpenAI / Meta AI / Microsoft Research / 清华 / PKU / CMU / MIT / Stanford / ETH 等）或已知活跃研究组 |
| 0 | 其他机构 |

### 入选规则

- **总分 ≥ 4**：入选精读（预计 10-30 篇）
- **总分 2-3**：记录在报告"摘要快览"表中，不精读
- **总分 ≤ 1**：直接跳过，不记录

### 快速排除规则（直接 0 分跳过，无需打分）

满足以下任一条件直接排除：
- 标题/摘要含 `survey` / `review` / `dataset` / `benchmark` / `we collect`
- 纯医疗影像论文（无通用方法贡献）
- arXiv 分类仅含 `eess.IV` 或 `cs.NE`（主分类非 cs.CV）

### 输出格式

将初筛结果写入两个文件：

`/tmp/arxiv_topn.json`（精读 ID 列表）：
```json
["2503.12345", "2503.67890"]
```

`/tmp/arxiv_screening.json`（含评分明细，供报告使用）：
```json
[
  {
    "arxiv_id": "2503.12345",
    "title": "...",
    "total_score": 5,
    "scores": { "novelty": 2, "importance": 2, "experiment": 1, "institution": 0 },
    "reason": "一句话说明入选/排除原因",
    "tier": "精读"
  }
]
```

---

## Step 4：下载 Top N 的 PDF

```bash
python ~/.claude/skills/paper-read/scripts/crawl_and_extract.py \
  --category cs.CV \
  --output /tmp/arxiv_selected.json \
  --pdf-dir /tmp/arxiv-papers \
  --mode download \
  --ids-file /tmp/arxiv_topn.json \
  --all-meta /tmp/arxiv_all.json
```

> `--ids-file` 来自 Step 3 写出的 `/tmp/arxiv_topn.json`（总分 ≥ 4 的 arxiv_id 列表）。

只下载初筛选出的论文 PDF，其余不下载。

---

## Step 5：阶段 2 — Task Subagent 逐篇精读 + OpenJudge 审稿

> **依赖**：本步需要独立 skill `paper-read-reviewer`，路径 `~/.claude/skills/paper-read-reviewer/SKILL.md`。若不存在，须先安装/复制该 skill，否则无法注入 subagent prompt。

> **架构说明**：精读 + 五阶段审稿是高 token 消耗任务，必须使用 **Task subagent** 处理，**不得在主 agent 上下文内循环精读多篇论文**。

### 执行规则

- **agent=5**：每批同时并行启动 **5 个** subagent，等待本批全部完成（结果均已写盘）后再启动下一批
- 每个 subagent 独占完整 200k 上下文，互不干扰
- 每个 subagent 的 prompt **必须**从 `~/.claude/skills/paper-read-reviewer/SKILL.md` 的 `## Prompt 模板` 节提取（见下方「Subagent Prompt 注入方式」），包含论文元数据占位符 + 完整分析框架 + OpenJudge 五阶段指令
- prompt 原文不可精简、不可省略，须完整注入 subagent
- subagent 完成后将结果写入 `/tmp/arxiv_results/{arxiv_id}.json`

### 主 agent 的操作流程

读取 `/tmp/arxiv_selected.json` 获取精读列表，**按批次**执行：

```
将所有待精读论文分组，每组 5 篇（最后一批不足 5 篇则取实际数量）

for each batch of 5 papers:
    1. 过滤：跳过 /tmp/arxiv_results/{arxiv_id}.json 已存在的论文（断点续传）
    2. 对本批剩余论文，同时启动 5 个 Task subagent（并行）
    3. 等待本批所有 subagent 全部完成
    4. 逐一读取结果文件，确认写入成功
    5. 处理下一批
```

### Subagent Prompt 注入方式

> ⚠️ **subagent 的完整 prompt 存放在独立 skill 文件中，主 agent 必须按以下步骤操作，不得自行编写或改写 prompt。**

**注入步骤（每篇论文启动 subagent 前必须执行）：**

1. 使用 Read 工具读取 `~/.claude/skills/paper-read-reviewer/SKILL.md` 全文
2. 从该文件的 `## Prompt 模板` 节中提取 ` ```text ` 代码块内的**全部内容**（原文，不做任何改动）
3. 将以下六个占位符替换为当前论文的实际值：
   - `{arxiv_id}` → 论文 arXiv ID
   - `{title}` → 论文标题
   - `{authors}` → 作者列表
   - `{pdf_path}` → 本地 PDF 路径（来自 `arxiv_selected.json` 的 `pdf_path` 字段）
   - `{submission_date}` → arXiv 提交日期（格式 `YYYY-MM-DD`，来自元数据）
   - `{abstract}` → 论文摘要
   - `{abs_url}` → ArXiv 详情页链接（来自 `arxiv_selected.json` 的 `abs_url` 字段）
4. 将替换后的字符串作为 Task subagent 的完整 prompt 传入

**禁止事项：**
- 禁止不读取 skill 文件直接凭记忆编写 prompt
- 禁止对 skill 文件内容做任何精简、改写、摘要或省略
- 禁止跳过 Read 步骤

> **OpenJudge 五阶段细则**：已迁至 `paper-read-reviewer/SKILL.md` 的 `## Prompt 模板` 代码块；本 skill 不再重复粘贴，避免与 subagent 注入内容双轨漂移。

---

## Step 6：主 Agent 汇总报告

> **执行时机**：等待所有 subagent 完成（`/tmp/arxiv_results/` 下的 JSON 数量 = 精读论文数）后，由**主 agent** 执行本步骤。读取全部 `{arxiv_id}.json`，汇总评分、排序、生成最终 Markdown 报告。

### 存储路径
- **Markdown 报告**: `papers/{date}/arxiv-{category}-{date}-report.md`
- **结果 JSON**: 将 `/tmp/arxiv_results/` 下的所有文件复制或移动到 `papers/{date}/results/`
- **元数据**: 复制 `/tmp/arxiv_screening.json` 和 `/tmp/arxiv_selected.json` 到 `papers/{date}/`

### 最终综合评分逻辑

OpenJudge 五阶段输出综合为最终评级：

| 条件 | 最终评级影响 |
|------|-------------|
| 安全检查 `abuse` | 直接标记，不参与评分 |
| 格式违规 ≥ 3 项 | review_score 下调 1 分 |
| correctness_score = 3（重大缺陷） | review_score 下调 1 分 |
| major_issues 数量 ≥ 2 | review_score 下调 1 分（最多叠加一次） |
| bib `error` 比例 > 20% | 报告标注 ⚠️ 引用存在确认性错误 |
| bib `unverifiable` 比例高 | **不影响评分**，仅在报告注明"PDF提取质量影响参考文献分析" |

最终分 = review_score（含以上调整，最低 1 分）

> **注意**：`unverifiable` 条目纯粹反映 PDF 提取质量，不得计入引用质量判断，不影响任何评分。

### 报告模板

```markdown
# arXiv {category} 论文解读 — {YYYY-MM-DD}

## 统计概览

| 指标 | 数值 |
|------|------|
| 当日总论文数 | XXX |
| 摘要初筛入选 | XX |
| 全文精读数 | XX |
| 平均 OpenJudge 分 | X.X / 6 |
| 安全异常论文数 | X |

---

## 精读论文（按最终评分排序）

### 1. [标题](abs_url) — X/6（Accept）

**原文链接**: [abs_url](abs_url)

**作者**: ... | **分类**: ... | **机构**: ...

**架构图（从 PDF 页面裁整块 Figure）**

![架构图]({architecture_img})

**结果图 / Benchmark（从 PDF 页面裁整块 Figure/Table）**

![实验结果]({benchmark_img})

#### 核心贡献
...

#### 实验指标
...

#### 消融实验
...

---

#### OpenJudge 审稿报告

**[阶段 1] 安全检查**
- 越狱检测：✅ safe / ⚠️ abuse
- 格式违规：X 项 — `[具体违规描述]`

**[阶段 2] 正确性评分**
- 分数：X/3（无显著错误 / 次要错误 / 重大缺陷）
- 推理过程：...
- 关键问题：
  - [问题1] ...

**[阶段 3] 学术评审**
- 评审分：X/6（等级标签）
- 优点：...
- 不足：...
- 关键问题（审稿人提问）：...

**[阶段 4] 严重性验证**
- Major Issues（已核实）：...
- Minor Issues（已核实）：...
- 误报（已排除）：...

**[阶段 5] 参考文献校验**
- 检查：XX 条 | ✅ verified: XX | ⚠️ suspect: XX | ❓ unverifiable: XX（PDF提取质量） | ❌ error: XX
- 可疑引用：...
- 确认错误：...

**最终综合评分：X/6**（调整说明：...）

---

## 摘要快览（总分 2-3 分，未精读）

| # | arXiv ID | 标题 | 新颖 | 重要 | 实验 | 机构 | 总分 | 备注 |
|---|----------|------|------|------|------|------|------|------|
...

## 全量汇总表

| # | arXiv ID | 标题 | 阶段 | 最终分 |
|---|----------|------|------|--------|
...
```

### 视觉材料提取实践（新增经验）

生成最终 Markdown 报告时，**不要只给图片路径，也不要只抽 PDF 内嵌 raster image**。应遵循以下规则：

1. **报告中直接嵌图**
   - 在 Markdown 中直接使用 `![](...)` 展示图片。
   - 不要仅写“图片路径：...”，否则报告可读性很差。

2. **只保留两类图：架构图 + 结果图**
   - 每篇精读论文最多展示两张：
     - `architecture_img`：模型架构 / 方法流程 / overview / pipeline
     - `benchmark_img`：主结果表、benchmark 对比、performance/comparison 图
   - 过滤无关图：定性样例拼贴、作者照片、数据样例、装饰性插图、零散子图。

3. **优先裁完整 Figure/Table block，而不是只抽 embedded image**
   - 许多论文中的“架构图”实际上是**整块 figure 区域**，包含：箭头、文字、公式、子图布局。
   - `pdfimages` 只能抽底图，往往会丢失关键标注，因此不应作为最终展示图的唯一来源。
   - 正确做法：根据 `Figure N` / `Fig. N` / `Table N` caption 的位置，从 PDF 页面裁出**完整 figure/table 区域**。

4. **推荐选择策略**
   - 架构图优先级：
     - `Figure 1`
     - 若 `Figure 1` 不是方法图，则选择 caption 含 `architecture / framework / overview / pipeline / method / training pipeline` 的 figure
   - 结果图优先级：
     - `Table 1`
     - 若 `Table 1` 不是主结果，则选择 caption 含 `benchmark / comparison / evaluation / results / performance` 的 table 或 figure

5. **裁剪启发式**
   - 对 figure：截取 caption 上方对应的整块视觉区域，并保留 caption
   - 对 table：从 caption 开始向下裁到下一张 `Figure/Table` caption 之前
   - 若同页存在多个 figure/table，必须用“当前 caption 到下一 caption”的边界裁切，避免把相邻内容混进来

6. **保存位置建议**
   - 报告相关图片统一保存到：
     - `papers/{date}/assets/selected/{arxiv_id}-arch.png`
     - `papers/{date}/assets/selected/{arxiv_id}-result.png`
   - 在 Markdown 中用相对路径直接嵌入：
     - `![...](assets/selected/{arxiv_id}-arch.png)`
     - `![...](assets/selected/{arxiv_id}-result.png)`

7. **与 reviewer skill 的关系**
   - `paper-read-reviewer` 里的 `visuals` 字段可以为空或保留 URL 占位；
   - **最终报告阶段**由主 agent 结合本地 PDF 再做一次“图像筛选 + 裁剪 + 嵌入”，这是更稳妥的做法。

---

## Step 7：清理缓存

```bash
# 仅清理 /tmp 下的中间过程文件和 PDF
python ~/.claude/skills/paper-read/scripts/cleanup.py --pdf-dir /tmp/arxiv-papers
rm -rf /tmp/arxiv_results/
rm /tmp/arxiv_all.json /tmp/arxiv_topn.json /tmp/arxiv_screening.json /tmp/arxiv_selected.json
```

> [!TIP]
> 经过此步后，`/tmp` 将被清空，但 `papers/{date}/` 中的报告和核心 JSON 将被永久保留。

---

## 快速参考

| 参数 | 默认 | 说明 |
|------|------|------|
| `--category` | `cs.CV` | arXiv 分类 |
| `--mode` | `meta-only` | `meta-only`=只爬元数据；`download`=下载指定 PDF |
| `--ids-file` | 无 | download 模式：指定要下载的 arxiv_id 列表文件 |
| `--all-meta` | 无 | download 模式：全量元数据文件路径 |
| `--pdf-dir` | `/tmp/arxiv-papers` | PDF 临时目录 |
| `--delay` | `1.5` | 下载间隔秒（避免 arXiv 限流） |
