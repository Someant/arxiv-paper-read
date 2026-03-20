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
- [ ] Step 1：安装依赖
- [ ] Step 2：爬取当天全量论文元数据（含摘要）
- [ ] Step 3：[阶段 1] Agent 读全量摘要，初筛出 Top N
- [ ] Step 4：下载 Top N 论文的 PDF
- [ ] Step 5：[阶段 2] 批量并行启动 Task subagent 精读（agent=5，每批 5 篇并行）
             └─ 每个 subagent：读 PDF 全文 → 深度分析 → OpenJudge 五阶段 → 写结果 JSON
- [ ] Step 6：主 agent 收集全部结果，综合打分、排序、生成 Markdown 报告
- [ ] Step 7：清理本地 PDF 文件
```

> **Token 预算说明**：精读 + 审稿是重度 token 任务（单篇 PDF 可达 15-30k tokens），绝不在主 agent 内循环处理所有论文。每篇论文独立分配一个 Task subagent，在 200k 上下文内完整处理完再结束，避免主 agent 耗尽上下文。

---

## Step 1：安装依赖

```bash
pip install requests beautifulsoup4
```

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
4. 将替换后的字符串作为 Task subagent 的完整 prompt 传入

**禁止事项：**
- 禁止不读取 skill 文件直接凭记忆编写 prompt
- 禁止对 skill 文件内容做任何精简、改写、摘要或省略
- 禁止跳过 Read 步骤

> **OpenJudge 五阶段细则**：已迁至 `paper-read-reviewer/SKILL.md` 的 `## Prompt 模板` 代码块；本 skill 不再重复粘贴，避免与 subagent 注入内容双轨漂移。

---

## Step 6：主 Agent 汇总报告

> **执行时机**：等待所有 subagent 完成（`/tmp/arxiv_results/` 下的 JSON 数量 = 精读论文数）后，由**主 agent** 执行本步骤。读取全部 `{arxiv_id}.json`，汇总评分、排序、生成最终 Markdown 报告。

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

**作者**: ... | **分类**: ... | **机构**: ...

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

---

## Step 7：清理 PDF 和临时结果

```bash
python ~/.claude/skills/paper-read/scripts/cleanup.py --pdf-dir /tmp/arxiv-papers
rm -rf /tmp/arxiv_results/
```

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
