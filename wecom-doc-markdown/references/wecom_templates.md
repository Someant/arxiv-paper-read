# WeCom Document Templates

## Safe Markdown Surface

Treat these as the safest default tools:

- `#` `##` `###` headings
- `-` unordered lists
- short plain-text label lines such as `作者：...`
- fenced code blocks only when the user truly needs raw text, commands, or quoted rules

Use cautiously:

- tables, only for very small summaries
- checklists, only if the paste target preserves them well enough for the user's workflow

Avoid by default:

- heavy divider lines
- complicated nesting
- layout that depends on alignment

## Rewrite Pattern For Table-Heavy Input

When the source contains Markdown tables or report cards, convert each row or record into a repeated `###` block.

Preferred pattern:

```markdown
### 1. Title
- 论文：Full paper title
- 作者：Author A, Author B
- 方向：Category
- 一句话总结：One-sentence takeaway.
- 关键数据：
  - Data point 1
  - Data point 2
- 为什么值得读：
  - Reason 1
  - Reason 2
```

## Template:资讯快报

```markdown
# 今日资讯快报

## 一句话总结
- 今天最重要的 3 件事
- 对业务最相关的变化
- 需要关注的风险或机会

## 数据概览
- 总量：xx
- 精读：xx
- 平均质量：xx

## 重点内容
### 1. 标题
- 来源：xxx
- 时间：yyyy-mm-dd
- 一句话总结：xxx
- 核心信息：
  - 要点 1
  - 要点 2
- 影响判断：
  - 对业务
  - 对产品

## 值得跟进
- 动作 1
- 动作 2
```

## Template:论文/研报速览

```markdown
# {{日期}}论文速览

## 一句话总结
- 今日共 xx 篇进入筛选，xx 篇进入精读。
- 整体质量 xx，无安全异常。
- 今日热点：xxx。

## 数据概览
- 当日论文总数：xx
- 进入摘要初筛：xx
- 深度精读：xx
- 平均得分：xx / 6

## 评分分布
- ⭐⭐⭐⭐⭐⭐ 5/6 分：xx 篇
- ⭐⭐⭐⭐⭐ 4/6 分：xx 篇
- ⭐⭐⭐⭐ 3/6 分：xx 篇
- ⭐⭐⭐ 2/6 分：xx 篇

## 精选论文
### 1. {{简称}}
- 论文：{{Full Title}}
- 作者：{{Authors}}
- 机构：{{Affiliations}}
- 方向：{{Category}}
- 一句话总结：{{Takeaway}}
- 关键数据：
  - {{Metric 1}}
  - {{Metric 2}}
- 为什么值得读：
  - {{Reason 1}}
  - {{Reason 2}}
- OpenJudge 得分：{{Score}}

## 需要注意的论文
### {{Paper}}
- 评分：{{Score}}
- 核心问题：{{Main issue}}
- 其他问题：
  - {{Issue 1}}
  - {{Issue 2}}
- 建议：{{Recommendation}}
```

## Template:周报

```markdown
# 本周资讯周报

## 本周重点
- 重点 1
- 重点 2
- 重点 3

## 行业动态
- 动态 1
- 动态 2

## 竞品动态
### 竞品 A
- 更新点：xxx
- 影响：xxx

### 竞品 B
- 更新点：xxx
- 影响：xxx

## 建议动作
- 立即可做
- 本周可做
- 持续观察
```

## Editing Heuristics

When rewriting for WeCom docs:

- start with the summary, not the background
- keep one bullet to one idea
- if a section feels dense, split it into more `###` blocks
- replace large tables with repeated labels
- preserve numbers, names, and paper titles exactly
- normalize field labels across all entries
