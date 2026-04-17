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
         读 PDF 全文 → 深度分析 → OpenJudge 五阶段 → 写 tmp/arxiv_results/{id}.json
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
- [ ] Step 0：[前置检查] 构建最近 7 天已处理 arXiv ID 集合，并校验 today/recent 是否真的出现新批次
- [ ] Step 1：安装依赖与创建 papers/{date} 目录
- [ ] Step 2：爬取 recent 全量元数据（含摘要）
- [ ] Step 3：[增量过滤] 仅保留“本次新增论文”
- [ ] Step 4：[阶段 1] Agent 读新增论文摘要，初筛出 Top N
- [ ] Step 5：下载 Top N 论文的 PDF
- [ ] Step 6：[阶段 2] 批量并行启动 Task subagent 精读（agent=5，每批 5 篇并行）
             └─ 每个 subagent：读 PDF 全文 → 深度分析 → OpenJudge 五阶段 → 写结果 JSON
- [ ] Step 7：主 agent 收集全部结果，生成 Markdown 报告并保存至 papers/{date}/
- [ ] Step 8：清理 /tmp 缓存文件（保留 papers/{date} 结果）
```

> **Token 预算说明**：精读 + 审稿是重度 token 任务（单篇 PDF 可达 15-30k tokens），绝不在主 agent 内循环处理所有论文。每篇论文独立分配一个 Task subagent，在 200k 上下文内完整处理完再结束，避免主 agent 耗尽上下文。

---

## Step 0：前置检查 — 新批次校验 + 近 7 天去重集合

在开始任何操作前，主 Agent 必须检查本地 `papers/` 目录，但**不再因为近 7 天做过日报就直接跳过**。正确做法是：

1. **获取日期**：确定当前日期（YYYY-MM-DD）。
2. **扫描目录**：列出 `papers/` 下所有以日期命名的子目录，并读取最近 7 天内的：
   - `arxiv_selected.json`
   - `arxiv_screening.json`
   - 若存在旧版 `arxiv_topn.json` 也一并读取
3. **构建已处理 ID 集合**：把最近 7 天内已进入“精读”或已下载 PDF 的 `arxiv_id` 汇总成 `processed_ids`。
4. **目的**：后续所有筛选必须以“增量”为单位，避免把 arXiv `recent` 页上同一批论文重复写成多天日报。

### Step 0.1：recent 页是否真的出现新批次

`recent` 页不等于“今天新增”。主 Agent 必须额外检查：

1. 从 `recent` 抓到的论文中，优先读取列表页对应的 **showing_date**（即 arXiv recent 页面展示批次日期）；
2. 同时保留每篇论文详情页里的 `Submitted on ...` 作为 `submission_date` 参考信息；
3. 批次判断时**优先依据 `showing_date`**，仅当缺失时才回退到 `submission_date`；
4. 判断当前 `recent` 是否仍然只是前一天/前几天的同一展示批次。

### 决策规则

- 若 `recent` 页中的论文展示批次日期（`showing_date`）并未进入新一天，且相对 `processed_ids` **没有任何新增论文**：
  - 明确向用户报告：
    - `当前 cs.CV recent 仍是旧批次，今天没有新增论文，不应重复生成日报。`
  - **停止任务，不生成重复报告。**
- 若 `recent` 页仍是旧批次，但相对 `processed_ids` 仍存在**未处理的新论文**：
  - 继续执行，但必须以“**增量补充**”模式进行；
  - 报告中明确注明：`这不是新的 arXiv 日更批次，而是对上一批中尚未处理论文的增量补读。`
- 若确实出现新的展示批次（优先 `showing_date`）：
  - 正常继续后续步骤。

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

## Step 2：爬取 `/new` 全量元数据

```bash
python3 scripts/paper-read/crawl_and_extract.py \
  --category cs.CV \
  --output tmp/arxiv_all.json \
  --mode meta-only
```

输出：`/new` 页上的全量元数据 JSON（标题 + 摘要 + arXiv ID + `section` + `showing_date` + `submission_date` + `comments`，无 PDF）。

> 新版脚本默认请求 `https://arxiv.org/list/{category}/new`，并在列表页直接提取摘要，同时标注：
> - `section = new`
> - `section = cross_list`
> - `section = replacement`
>
> 路径约定统一使用**相对路径**：`tmp/`、`papers/`、`scripts/`，不要写死绝对路径。
>
> **新增事实标注要求**：阶段 1 与最终报告都必须尽量基于 arXiv 原文元数据补齐并展示：
> - `comments` 字段
> - 论文类型：`main_conference` / `workshop` / `preprint` / `tech_report`
> - 任务领域、方法关键词、数据集、核心数字
>
> 其中“论文类型”默认根据 `comments` 做保守判断：
> - comments 明确含 `CVPR/ICCV/ECCV/NeurIPS/ICLR/AAAI main conference / accepted / oral / spotlight` 等主会信号时，标为 `main_conference`
> - comments 明确含 `workshop / challenge / competition / demo / extended abstract` 时，标为 `workshop`
> - comments 仅描述 arXiv 版本、技术报告、submitted to、under review，或无明确信号时，标为 `preprint`
> - comments 明确写 `technical report` 时，标为 `tech_report`
>
> 若 `comments` 缺失或歧义，必须明确写“按 preprint 暂定”，禁止擅自拔高为主会。
---

## Step 3：增量过滤 — 只保留本次真正新增论文

主 Agent 必须读取 `tmp/arxiv_all.json`，结合 Step 0 得到的 `processed_ids` 做去重。

推荐直接调用增量过滤脚本：

```bash
python3 scripts/paper-read/filter_incremental.py \
  --all-meta tmp/arxiv_all.json \
  --papers-dir papers \
  --lookback-days 7 \
  --output tmp/arxiv_incremental.json \
  --processed-output tmp/arxiv_processed_ids.json \
  --new-batch-output tmp/arxiv_latest_batch.json \
  --strict-new-batch
```

该脚本会：
- 扫描 `papers/` 下最近 7 天目录；
- 自动汇总 `arxiv_selected.json` / `arxiv_screening.json` / `arxiv_topn.json` 中的历史 ID；
- 自动识别 `/new` 中最新批次日期：**优先 `showing_date`，其次 `submission_date`**；
- 默认仅保留 `section=new` 的论文，自动排除 `cross_list` 与 `replacement`；
- 输出严格增量后的 `tmp/arxiv_incremental.json`。

### 过滤规则

保留同时满足以下条件的论文：
1. `arxiv_id` **不在** `processed_ids` 中；
2. 默认 `section == new`；`cross_list` / `replacement` 不进入“今日主批次”筛选池，除非用户明确要求放开；
3. 若本次目标是“今日新批次”，则其 `showing_date` 必须属于当前新批次；若缺失才回退到 `submission_date`；
4. 若当前并无新批次，但用户仍要求继续，则仅允许保留“旧批次里尚未处理的增量论文”。

### 必须写出的文件

`tmp/arxiv_incremental.json`：
```json
[
  {
    "arxiv_id": "2604.07350",
    "title": "...",
    "abstract": "...",
    "showing_date": "2026-04-09",
    "submission_date": "2026-04-08"
  }
]
```

### 零增量处理

如果过滤后论文数为 0：
- 直接向用户报告：`当前 /new 主批次没有新增论文（或近 7 天内均已处理），本次不生成重复日报。`
- **中止任务**。

---

## Step 4：阶段 1 — Agent 摘要初筛

读取 `tmp/arxiv_incremental.json`，**不下载任何 PDF**，仅根据标题和摘要快速评分。

### 初筛前的必做标注（新增）

主 Agent 在摘要初筛前，必须先对每篇增量论文补齐一份轻量事实卡，至少包含：
- `arxiv_id`
- 原文摘要（不得用二手转述摘要替代）
- `comments`
- 论文类型：主会 / Workshop / preprint / tech report
- 任务领域
- 方法关键词
- 提及的数据集
- 摘要中出现的核心数字（若无具体数字则显式写“摘要未给出硬数字”）

> 这里的“核心数字”仅作为阶段 1 的快速线索；**任何进入最终报告正文的关键数字，都必须在后续原文全文中再次核实，并补充对应 setting**。

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

#### D. 作者/机构背景信号（0-1 分，弱参考）

| 分 | 判断依据 |
|----|---------|
| 1 | 来自该方向公认活跃研究组、工业研究机构或长期稳定产出顶会论文的团队；**仅作弱参考，不得单独决定是否入选** |
| 0 | 无明显额外信号 |

> 不再使用固定机构白名单；若论文本身方法与实验较弱，不得因为机构强而抬高入选优先级。

### 入选规则

- **总分 ≥ 4**：入选精读（预计 10-30 篇）
- **总分 2-3**：记录在报告"摘要快览"表中，不精读
- **总分 ≤ 1**：直接跳过，不记录

### 快速排除规则（默认直接 0 分跳过，但需先判断是否为“纯数据/综述类”）

满足以下任一条件时，可直接排除：
- **纯** Survey / Review：主要贡献是整理文献、分类总结、趋势回顾，而非提出新方法/新训练范式/新推理框架
- **纯** Dataset / Benchmark / Data Collection：主要贡献是收集数据、构建基准或设计评测协议，而非提出可复用的方法创新
- 纯医疗影像论文（无通用方法贡献）
- arXiv 分类仅含 `eess.IV` 或 `cs.NE`（主分类非 cs.CV）

### 重要例外（禁止误杀）

即使标题/摘要中出现 `dataset` / `benchmark` / `we collect` / `we construct` / `we curate`，**只要同时满足以下任一条件，就不得直接快速排除，必须进入正常打分**：
- 明确提出了新方法/新框架/新训练策略/新推理策略（如 `we propose` / `we present a model` / `we design` / `framework` / `loss` / `module` / `strategy`）
- 数据集/benchmark 只是支持性贡献，论文主体仍是方法创新
- 论文同时解决一个清晰的新问题设定，并给出完整的方法闭环（问题定义 + 方法设计 + 实验验证）

### 启发式判断原则

- 若论文的“数据/benchmark”贡献只是为了验证一个新方法，**不要**因为出现关键词就直接排除
- 若论文读起来更像“提出一种方法，并顺带构建数据/评测”，则按方法论文处理
- 只有当论文读起来主要是在“搭数据集 / 搭 benchmark / 做综述”，而不是“提方法”，才使用快速排除

### 输出格式

将初筛结果写入两个文件：

`tmp/arxiv_topn.json`（精读 ID 列表）：
```json
["2503.12345", "2503.67890"]
```

`tmp/arxiv_screening.json`（含评分明细，供报告使用）：
```json
[
  {
    "arxiv_id": "2503.12345",
    "title": "...",
    "paper_type": "preprint",
    "comments": "...",
    "facts": {
      "task": ["..."] ,
      "method_keywords": ["..."],
      "datasets": ["..."],
      "core_numbers": ["..."]
    },
    "total_score": 5,
    "scores": { "novelty": 2, "importance": 2, "experiment": 1, "background": 0 },
    "reason": "一句话说明入选/排除原因",
    "tier": "精读"
  }
]
```

### 初筛结果标准化（强制执行）

Agent 产出 `tmp/arxiv_screening.json` 后，主流程必须立刻运行：

```bash
python3 scripts/paper-read/normalize_screening.py \
  --incremental tmp/arxiv_incremental.json \
  --screening tmp/arxiv_screening.json \
  --output tmp/arxiv_screening.json
```

该步骤会强制补齐：
- `paper_type`
- `comments`
- `authors`
- `subjects`
- `showing_date` / `submission_date`
- `facts.task` / `facts.method_keywords` / `facts.datasets` / `facts.core_numbers`

若 Agent 漏写这些字段，不得直接进入下载与最终渲染步骤。
### 强制约束（新增）

- **禁止**把最近 7 天内已经进入 `arxiv_selected.json` 的论文再次纳入 `tmp/arxiv_topn.json`
- **禁止**把同一展示批次（优先 `showing_date`）的旧论文反复当成“今日论文”输出
- 若用户要求“重新跑今天论文”，默认含义是：
  - **先判断是否有新批次**；
  - 若无新批次，则只允许输出“增量未处理论文”或明确告知“今天没有新增”
- 若确需重跑旧批次，报告标题和正文必须明确写成：
  - `增量补读` / `补充筛选`
  - 不能伪装成新的 `today report`
- **禁止**在未区分 `main_conference / workshop / preprint` 的情况下，对论文做等权推荐
- **禁止**不经原文核验就在报告正文转述实验数字
- **禁止**使用“最强 / 最稳 / 突破 / 首个”等绝对化措辞，除非后续给出同期竞争参照与依据

---

## Step 5：下载 Top N 的 PDF

```bash
python3 scripts/paper-read/crawl_and_extract.py \
  --category cs.CV \
  --output tmp/arxiv_selected.json \
  --pdf-dir tmp/arxiv-papers \
  --mode download \
  --ids-file tmp/arxiv_topn.json \
  --all-meta tmp/arxiv_all.json
```

> `--ids-file` 来自 Step 4 写出的 `tmp/arxiv_topn.json`（总分 ≥ 4 的 arxiv_id 列表）。

只下载初筛选出的论文 PDF，其余不下载。

---

## Step 6：阶段 2 — Task Subagent 逐篇精读 + OpenJudge 审稿

> **依赖**：本步需要独立 skill `paper-read-reviewer`，路径 `~/.claude/skills/paper-read-reviewer/SKILL.md`。若不存在，须先安装/复制该 skill，否则无法注入 subagent prompt。

> **架构说明**：精读 + 五阶段审稿是高 token 消耗任务，必须使用 **Task subagent** 处理，**不得在主 agent 上下文内循环精读多篇论文**。

### 执行规则

- **动态 batch 调度必须先落盘再执行**：主 agent 不应临场拍脑袋决定并行度，必须先运行 `scripts/paper-read/plan_batches.py` 生成 `tmp/arxiv_batches.json`
- 批次规则默认如下：短论文 `batch_size=5`，中等论文 `batch_size=3`，超长论文 `batch_size=2`
- 每个 subagent 独占完整 200k 上下文，互不干扰
- 每个 subagent 的 prompt **必须**从 `~/.claude/skills/paper-read-reviewer/SKILL.md` 的 `## Prompt 模板` 节提取（见下方「Subagent Prompt 注入方式」），包含论文元数据占位符 + 完整分析框架 + OpenJudge 五阶段指令
- prompt 原文不可精简、不可省略，须完整注入 subagent
- subagent 完成后将结果写入 `tmp/arxiv_results/{arxiv_id}.json`

### 主 agent 的操作流程

读取 `tmp/arxiv_selected.json` 获取精读列表后，**必须先生成批次计划**：

```bash
python3 scripts/paper-read/plan_batches.py \
  --selected tmp/arxiv_selected.json \
  --output tmp/arxiv_batches.json
```

`tmp/arxiv_batches.json` 会为每篇论文补充：
- `pdf_size_mb`
- `pdf_pages`
- `size_tier`（small / medium / large）
- `recommended_batch_size`
- `estimated_token_cost`

并输出按 tier 切分后的批次列表，供主 agent 逐批执行。

执行顺序：默认 `large -> medium -> small`，避免长论文拖到最后形成不可预测尾延迟。

```
读取 tmp/arxiv_batches.json

for each batch in batches:
    1. 过滤：跳过 tmp/arxiv_results/{arxiv_id}.json 已存在的论文（断点续传）
    2. 对本批剩余论文，同时启动 batch.batch_size 个 Task subagent（并行）
    3. 等待本批所有 subagent 全部完成
    4. 逐一读取结果文件，确认写入成功
    5. 立刻运行 normalize_results.py 补齐 schema
    6. 处理下一批
```

每一批 subagent 完成后，主流程必须运行：

```bash
python3 scripts/paper-read/normalize_results.py \
  --results-dir tmp/arxiv_results \
  --selected tmp/arxiv_selected.json \
  --screening tmp/arxiv_screening.json
```

该步骤会强制补齐 reviewer JSON 中的：
- `paper_type`
- `tags.task` / `tags.method_keywords` / `tags.datasets`
- `analysis.checklist.*`
- `openjudge.competition.*`
- `visual_hints.*`
- `visuals.*`

若某篇 reviewer JSON 缺这些字段，必须先标准化再汇总，禁止把不完整 JSON 直接交给 `render_report.py`。

### 渲染前硬校验（新增）

`render_report.py` 现在会在启动时对 `tmp/arxiv_screening.json` 和 `tmp/arxiv_results/*.json` 做 schema 硬校验。

若以下字段缺失，将**直接失败退出**，而不是静默兜底：
- `arxiv_screening.json`：`paper_type`、`comments`、`facts.*`
- reviewer JSON：`paper_type`、`tags.*`、`analysis.checklist.*`、`openjudge.competition.*`、`visual_hints.*`、`visuals.*`

因此主流程顺序必须严格为：
1. Agent 产出 `tmp/arxiv_screening.json`
2. 运行 `normalize_screening.py`
3. 下载 PDF / 启动 subagent
4. 每批 subagent 完成后运行 `normalize_results.py`
5. 最后才允许运行 `render_report.py`

### 命令级闭环（推荐）

为避免人工漏跑标准化步骤，优先直接使用总控脚本：

```bash
python3 scripts/paper-read/finalize_report.py \
  --date $(date +%Y-%m-%d) \
  --category cs.CV \
  --incremental tmp/arxiv_incremental.json \
  --screening tmp/arxiv_screening.json \
  --selected tmp/arxiv_selected.json \
  --batches tmp/arxiv_batches.json \
  --results-dir tmp/arxiv_results \
  --style human \
  --output papers/$(date +%Y-%m-%d)/arxiv-cs.CV-$(date +%Y-%m-%d)-report.md \
  --sync-dir papers/$(date +%Y-%m-%d)
```

该脚本会按固定顺序自动执行：
1. `normalize_screening.py`
2. `normalize_results.py`
3. `render_report.py`
4. 可选把 `incremental/screening/selected/batches/results/report` 同步归档到 `papers/{date}/`

除非在调试单步脚本，否则推荐统一走 `finalize_report.py`，不要手工拼接最后几步。### `plan_batches.py` 的默认分档规则

- `small`：`pdf_pages < 12` 且 `pdf_size_mb < 2.0` → `batch_size = 5`
- `medium`：`pdf_pages <= 25` 且 `pdf_size_mb <= 6.0` → `batch_size = 3`
- `large`：其余情况 → `batch_size = 2`
- 若页数不可得，则回退到 PDF 大小判断；若大小也不可得，则保守归为 `medium`

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

## Step 7：主 Agent 汇总报告

> **执行时机**：等待所有 subagent 完成（`tmp/arxiv_results/` 下的 JSON 数量 = 精读论文数）后，由**主 agent** 执行本步骤。读取全部 `{arxiv_id}.json`，汇总评分、排序、生成最终 Markdown 报告。

### 存储路径
- **Markdown 报告**: `papers/{date}/arxiv-{category}-{date}-report.md`
- **结果 JSON**: 将 `tmp/arxiv_results/` 下的所有文件复制或移动到 `papers/{date}/results/`
- **元数据**: 复制 `tmp/arxiv_screening.json`、`tmp/arxiv_selected.json`、`tmp/arxiv_incremental.json`、`tmp/arxiv_batches.json` 到 `papers/{date}/`

### 推荐渲染方式（优先）

优先使用统一渲染脚本生成 Markdown，避免主 agent 手写字符串导致字段漂移：

```bash
python3 scripts/paper-read/render_report.py \
  --date $(date +%Y-%m-%d) \
  --category cs.CV \
  --incremental tmp/arxiv_incremental.json \
  --screening tmp/arxiv_screening.json \
  --selected tmp/arxiv_selected.json \
  --batches tmp/arxiv_batches.json \
  --results-dir tmp/arxiv_results \
  --style human \
  --daily-conclusion-file tmp/daily_conclusion.md \
  --output papers/$(date +%Y-%m-%d)/arxiv-cs.CV-$(date +%Y-%m-%d)-report.md
```

其中：
- `--style human`：**默认推荐**，生成更适合人读的日报风格；会优先输出统一的 `## 今日关注` 段落，先给总判断，再按主题展开，并在该部分把重点论文写成 `简称（[arxiv_id](link) · score）` 的格式。
- `--style template`：保留完整字段化模板，更适合调试、验收和结构化检查；同样使用统一的 `## 今日关注` 段落，而不是把“今日结论”和“今天值得关注什么”拆成两个重复 section。
- `tmp/daily_conclusion.md` 为可选输入；若存在，渲染脚本会将其作为 `## 今日关注` 中的补充说明插入，而不是单独再生成一个重复总结段。

若使用脚本失败，主 agent 才可回退到手工拼接 Markdown，但必须遵守下文的字段对齐规则。

### 新增：横向竞争定位与价值判断（必须执行）

在生成最终报告前，主 Agent 必须为每篇**精读论文**补做一轮横向定位，默认窗口为**提交日期前后 ±90 天**。可使用 Semantic Scholar、arXiv search API 或其他可复现的论文检索源。

对每篇精读论文至少完成以下工作：
1. 搜索同期同任务方向论文，优先匹配：
   - 任务领域
   - 方法关键词
   - 目标数据集 / benchmark
2. 给出竞争定位标签之一：
   - `首提`
   - `同期并行`
   - `跟进`
   - `跟进但系统化更强`
   - `工程整合型优化`
3. 明确比较轴是否一致：
   - 是否同数据集 / split / metric
   - 是否同 backbone / model size
   - 是否使用额外数据 / 额外教师模型
   - 是否使用更多 test-time compute / search / reranking
4. 若比较轴不一致，必须显式写“不可直接横比”，禁止硬下结论

同时，对每篇精读论文都要回答以下 checklist：
- □ 问题意识是否系统级：是在解决 pipeline 级依赖 / 评测设定 / 训练-推理闭环瓶颈，还是仅改局部组件
- □ 方法新颖性是否在竞争参照下仍成立，不能孤立评价
- □ 关键实验数字是否已从原文全文核实，并注明对应 setting（dataset / metric / baseline / inference budget）
- □ 性能提升主要来自方法，还是来自更多数据 / 更大模型 / 更高推理预算
- □ 推理代价与部署成本是否被论文诚实讨论

这些结论应写回最终报告；若无法完成竞争搜索，必须写明证据不足，不能把主观印象写成定论。

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
| `bibcheck.executed = false` | **不影响评分**，报告注明跳过原因 |

最终分 = review_score（含以上调整，最低 1 分）

> **注意**：`unverifiable` 条目纯粹反映 PDF 提取质量，不得计入引用质量判断，不影响任何评分。

### 报告生成字段对齐规则

主 agent 在生成最终报告前，必须逐篇读取 reviewer JSON，并按以下字段映射统一取值：

| 报告字段 | reviewer JSON 来源 |
|---|---|
| 标题 | `title` |
| 原文链接 | `abs_url` |
| 论文类型 | `paper_type` |
| 任务/方法/数据集标签 | `tags.task` / `tags.method_keywords` / `tags.datasets` |
| 核心贡献 | `analysis.core_contribution` |
| 实验指标 | `analysis.experiments` |
| 消融实验 | `analysis.ablation` |
| 价值判断 checklist | `analysis.checklist.*` |
| 同期竞争定位 | `openjudge.competition.*` |
| 越狱检测 | `openjudge.safety.jailbreak` |
| 格式违规 | `openjudge.safety.format_violations` + `violations_list` |
| 正确性评分 | `openjudge.correctness.score` + `reasoning` + `key_issues` |
| 学术评审 | `openjudge.review.score` + `level` + `strengths` + `weaknesses` + `questions` |
| 严重性验证 | `openjudge.criticality.major_issues` / `minor_issues` / `false_positives` |
| 参考文献校验 | `openjudge.bibcheck.*` |
| 最终分 | `final_score` |
| 调整说明 | `score_adjustments` |
| 图片线索 | `visual_hints.*` |
| 图片占位 | `visuals.*` |

### 缺失字段与降级规则

- 若 `visuals.architecture_img` 为空：
  - 主 agent 先尝试依据 `visual_hints` + 本地 PDF 裁图；
  - 若仍失败，则在报告中写 `未提取到合适图片`，不得留空 broken link。
- `visuals.benchmark_img` 不再作为最终报告默认必需项：
  - 默认只把 benchmark 结果转写成文字和数字解读；
  - 仅当用户明确要求时，才在报告中嵌 benchmark 图片。
- 若 `openjudge.bibcheck.executed = false`：
  - 报告中显示 `参考文献校验：已跳过（原因：...）`；
  - 不得伪造 0 条检查结果冒充“已完成检查”。
- 若 `score_adjustments` 为空数组：
  - 报告中写 `无额外调整`。
- 若 `questions` / `major_issues` / `minor_issues` / `false_positives` 为空：
  - 报告中写 `无`，保持结构稳定。
- 若单篇 reviewer JSON 缺关键字段：
  - 主 agent 必须在汇总时显式标记 `字段缺失`，并继续处理其他论文，避免整份报告失败。

### 批次统计字段对齐

生成报告统计概览时，除论文内容外，还必须从 `tmp/arxiv_batches.json` 读取：
- `summary.total_papers`
- `summary.batch_count`
- `summary.tier_counts.small`
- `summary.tier_counts.medium`
- `summary.tier_counts.large`
- `summary.missing_pdf_count`

并在统计概览中展示动态调度情况，便于回溯执行成本。

### 输出分层规则（新增）

最终日报必须显式分为三层，而不是把所有精读论文等量展开：

1. **优先读（≤3 篇）**
   - 给出：`为什么值得优先读`、`阅读时该盯什么`、`实验应该怎么看`
   - 优先分配给：问题意识更系统、竞争定位更清楚、数字核验更完整、并且具有较强外溢价值的论文
2. **选读（≤3 篇）**
   - 给出：一句话定位 + 方向标签
   - 适用于：方向重要但贡献更偏跟进、或受众面较窄的论文
3. **快评（其余）**
   - 给出：一句话总结 + 受益读者标签
   - 禁止只写“不值一读”；若论文较窄，必须明确“推荐给哪些方向的人”

若论文是 Workshop 或明显领域专项工作，可进入“选读/快评”，但**不得与主会主线论文等权排序**，除非其方法贡献和竞争定位有非常充分的证据支撑。

### 报告模板

> 实际执行时，**优先生成 `--style human` 的版本** 作为最终面向用户的日报；以下模板主要描述 `template` 风格下的字段对齐要求。若需要更像人写的报告，正文应优先采用“`今日关注`（总判断 + 主线展开） / 为什么值得看 / 一句话结论 / 实验亮点 / 我会怎么评价它”的顺序，而不是在正文里直接堆叠全部 OpenJudge 字段。

```markdown
# arXiv {category} 论文解读 — {YYYY-MM-DD}

## 今日关注

先说结论：今天最值得关注的主线是……最值得优先读的论文是……

> `今日关注` / 总结段里的重点论文，推荐统一写成：
> - `HY-World 2.0（[2604.14268](https://arxiv.org/abs/2604.14268) · 5/6）`
> - `Sketch-to-Multi-View（[2604.14302](https://arxiv.org/abs/2604.14302) · 5/6）`
> - `DharmaOCR（[2604.14314](https://arxiv.org/abs/2604.14314) · 4/6）`
>
> 也就是：**论文简称 + 全角括号 + arXiv 链接 + 分数**。

具体看：
- 主线 A：... 重点论文写作 `论文简称（[arxiv_id](link) · score）`
- 主线 B：... 重点论文写作 `论文简称（[arxiv_id](link) · score）`

> 写结论时必须遵守：
> - 结论优先建立在“同期竞争参照 + 原文数字核验”上
> - 若仅是 Workshop / preprint，需显式标注，不得混写成“今日最强”
> - 若比较轴不一致，使用“值得关注 / 值得跟踪”，不要写成绝对领先

## 统计概览

| 指标 | 数值 |
|------|------|
| `/new` 主批次数量 | XXX |
| 增量论文数 | XX |
| 摘要初筛入选 | XX |
| 全文精读数 | XX |
| 动态批次数 | XX |
| large / medium / small | X / X / X |
| 缺失 PDF 数 | X |
| 平均 OpenJudge 分 | X.X / 6 |
| 安全异常论文数 | X |

---

## 优先读（≤3 篇）

### 1. [标题](abs_url) — X/6（Accept）

**原文链接**: [abs_url](abs_url)

**作者**: ... | **分类**: ... | **类型**: main_conference / workshop / preprint / tech_report | **背景信号**: ...

**任务/方法标签**: ...

**竞争定位**: 首提 / 同期并行 / 跟进 / 跟进但系统化更强 / 工程整合型优化

**比较轴说明**: 同数据集/metric/backbone/推理预算？若不可直接横比需明确写出

**调度信息**: `tier={size_tier}` | `pages={pdf_pages}` | `pdf={pdf_size_mb}MB` | `batch_size={recommended_batch_size}`

**架构图（从 PDF 页面裁整块 Figure）**

![架构图]({architecture_img})

> 若未裁图成功：写 `未提取到合适图片`；若仅有线索，可追加 `候选线索：{visual_hints.architecture_candidates}`

#### 核心贡献
...

#### 实验指标
...

> 所有关键数字必须写成“数字 + setting”，例如：`在 XXX dataset 的 YYY metric 上，相比 ZZZ baseline，从 A 提升到 B；是否使用额外数据/更大模型/更多 test-time compute：...`。

#### 消融实验
...

#### 价值判断 checklist
- 系统级问题意识：...
- 新颖性竞争参照：...
- 原文核实数字：...
- 方法贡献 vs 资源贡献：...
- 推理/部署成本讨论：...

#### 为什么优先读 / 盯什么 / 怎么看实验
- 为什么：...
- 盯什么：...
- 怎么看实验：...

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
- 若 `bibcheck.executed = true`：
  - 检查：XX 条 | ✅ verified: XX | ⚠️ suspect: XX | ❓ unverifiable: XX（PDF提取质量） | ❌ error: XX
  - 可疑引用：...
  - 确认错误：...
- 若 `bibcheck.executed = false`：
  - 参考文献校验：已跳过（原因：`skip_reason`）

**最终综合评分：X/6**（调整说明：若 `score_adjustments` 为空则写 `无额外调整`）

---

## 选读（≤3 篇）

- `[arXiv ID] 标题`：一句话定位。**方向标签**：... | **类型**：main_conference / workshop / preprint / tech_report
- ...

## 快评（其余）

- `[arXiv ID] 标题`：一句话。**推荐给**：...方向读者 | **类型**：main_conference / workshop / preprint / tech_report
- ...

## 摘要快览（总分 2-3 分，未精读）

| # | arXiv ID | 标题 | 类型 | 新颖 | 重要 | 实验 | 背景 | 总分 | 备注 |
|---|----------|------|------|------|------|------|------|------|------|
...

## 全量汇总表

| # | arXiv ID | 标题 | 阶段 | tier | pages | batch | 最终分 |
|---|----------|------|------|------|-------|-------|--------|
...
```

### 视觉材料提取实践（新增经验）

生成最终 Markdown 报告时，**不要只给图片路径，也不要只抽 PDF 内嵌 raster image**。应遵循以下规则：

0. **默认 source-first，而不是 PDF-only**
   - 对 arXiv 论文，优先尝试 `https://arxiv.org/e-print/{arxiv_id}` 获取 source
   - 若 source 中存在单独完整 figure 文件，优先直接使用 source 资产
   - 若 source 中对应 figure 为 TikZ / `\\input{...}` / 多 panel 组合图，则禁止只取其中一个 panel 充当整图
   - 此时应回退到 PDF caption block 裁图

1. **报告中直接嵌图**
   - 在 Markdown 中直接使用 `![](...)` 展示图片。
   - 不要仅写“图片路径：...”，否则报告可读性很差。

2. **最终报告默认只保留流程图 / 架构图**
   - 每篇精读论文默认最多展示一张：
     - `architecture_img`：模型架构 / 方法流程 / overview / pipeline
   - `benchmark_img` 不再默认嵌入最终报告；benchmark 信息优先转写成正文里的数字与 setting 解读。
   - 过滤无关图：定性样例拼贴、作者照片、数据样例、装饰性插图、零散子图。

3. **优先裁完整 Figure/Table block，而不是只抽 embedded image**
   - 更具体地说：
     - **先 source**：能拿到完整原图就直接导出
     - **再 PDF**：当 source 无法可靠恢复整图时，再做 caption/block 裁图
     - **最后 fallback**：固定坐标裁图只能是最后保底，不应成为默认流程
   - 许多论文中的“架构图”实际上是**整块 figure 区域**，包含：箭头、文字、公式、子图布局。
   - `pdfimages` 只能抽底图，往往会丢失关键标注，因此不应作为最终展示图的唯一来源。
   - 正确做法：根据 `Figure N` / `Fig. N` / `Table N` caption 的位置，从 PDF 页面裁出**完整 figure/table 区域**。

4. **推荐选择策略**
   - 架构图优先级：
     - `Figure 1`
     - 若 `Figure 1` 不是方法图，则选择 caption 含 `architecture / framework / overview / pipeline / method / training pipeline` 的 figure
   - benchmark 图仅在用户明确要求时才考虑：
     - 优先 `Table 1`
     - 若 `Table 1` 不是主结果，则选择 caption 含 `benchmark / comparison / evaluation / results / performance` 的 table 或 figure

5. **裁剪启发式**
   - 对 figure：截取 caption 上方对应的整块视觉区域，并保留 caption
   - 对 table：从 caption 开始向下裁到下一张 `Figure/Table` caption 之前
   - 若同页存在多个 figure/table，必须用“当前 caption 到下一 caption”的边界裁切，避免把相邻内容混进来

6. **保存位置建议**
   - 报告默认相关图片统一保存到：
     - `papers/{date}/assets/selected/{arxiv_id}-arch.png`
   - 若用户明确要求 benchmark 图，才额外保存：
     - `papers/{date}/assets/selected/{arxiv_id}-result.png`
   - 在 Markdown 中默认只嵌入：
     - `![...](assets/selected/{arxiv_id}-arch.png)`

7. **与 reviewer skill 的关系**
   - `paper-read-reviewer` **不再负责访问 HTML、下载图片或裁图**；它只输出 `visual_hints`（如 Figure 1 / Table 1 / caption 关键词）或保留空的 `visuals` 占位；
   - **最终报告阶段**统一由主 agent 做“图像筛选 + 裁剪 + 嵌入”，但实现顺序更新为：
     1. 先尝试 arXiv source 提取完整 figure
     2. source 不可靠时，回退到本地 PDF 的 caption/block 裁图
   - 默认只嵌流程图，benchmark 图不作为最终报告标准配置。

8. **当前项目中的推荐脚本**
   - 优先使用：
     - `scripts/paper-read/extract_report_figures.py`
   - 该脚本默认执行：
     - source-first 提取
     - 单文件 figure 直接导出
     - 组合/TikZ figure 自动回退到 PDF 裁图
     - 输出到 `papers/{date}/assets/selected/{arxiv_id}-arch.png`

---

## Step 8：清理缓存

```bash
# 仅清理 tmp/ 下的中间过程文件和 PDF
python3 scripts/paper-read/cleanup.py --pdf-dir tmp/arxiv-papers
rm -rf tmp/arxiv_results/
rm -f tmp/arxiv_all.json tmp/arxiv_topn.json tmp/arxiv_screening.json tmp/arxiv_selected.json tmp/arxiv_incremental.json tmp/arxiv_processed_ids.json tmp/arxiv_latest_batch.json tmp/arxiv_batches.json
```

> [!TIP]
> 经过此步后，`tmp/` 将被清空，但 `papers/{date}/` 中的报告和核心 JSON 将被永久保留。

---

## 快速参考

| 参数 | 默认 | 说明 |
|------|------|------|
| `--category` | `cs.CV` | arXiv 分类 |
| `--mode` | `meta-only` | `meta-only`=只爬 `/new` 元数据；`download`=下载指定 PDF |
| `--ids-file` | 无 | download 模式：指定要下载的 arxiv_id 列表文件 |
| `--all-meta` | 无 | download 模式：全量元数据文件路径 |
| `--pdf-dir` | `tmp/arxiv-papers` | PDF 临时目录 |
| `--delay` | `1.5` | 下载间隔秒（避免 arXiv 限流） |
