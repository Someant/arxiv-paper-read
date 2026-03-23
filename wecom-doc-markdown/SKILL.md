---
name: wecom-doc-markdown
description: Use this skill when the user wants to write, rewrite, or template content for WeCom/Enterprise WeChat documents, especially news briefs, research digests, daily reports, weekly reports, or other information-dense content that must read well after pasting into a WeCom doc. This skill is for formatting around WeCom's lightweight Markdown support, stable heading/list structure, and scannable Chinese business writing.
---

# WeCom Doc Markdown

## Overview

Use this skill to turn rough notes, long-form summaries, or table-heavy drafts into WeCom-friendly documents.
Optimize for content that will be pasted into a WeCom document and needs to remain readable even if advanced Markdown features degrade.

## When To Use It

Apply this skill when the user is doing one or more of these:

- asking for a WeCom or Enterprise WeChat document template
- formatting资讯类内容, 日报, 周报, 研报摘要, 论文速览, 会议纪要, or internal briefings
- asking what Markdown is safe to use in WeCom docs
- pasting content that currently relies on heavy tables, separators, or decorative symbols

Do not use this skill for:

- GitHub README or developer docs where richer Markdown is expected
- slide writing, web publishing, or PDF-first layouts

## Working Rules

Assume WeCom doc Markdown is lightweight and can be inconsistent across paste flows.
Prefer the most stable structure:

- `#` for the document title
- `##` for sections
- `###` for repeated items such as one paper, one news item, or one issue
- `-` bullet lists for almost all detail
- short paragraphs only when a list would feel unnatural
- plain text labels such as `论文：` or `建议：` instead of complex formatting

Avoid depending on:

- large Markdown tables
- dense separators such as repeated lines or symbol walls
- deep nesting
- intricate emphasis patterns

When the user provides table-heavy source material, flatten it into repeated list blocks unless the table is truly tiny and still readable after paste.

## Default Output Shape

Unless the user asks otherwise, produce WeCom-ready output in this order:

1. One-screen summary
2. Key metrics or overview
3. Main items in repeated blocks
4. Risks, caveats, or follow-ups
5. Optional compact full list

For information-dense writing, each repeated block should usually be:

- title
- authors or source
- direction or category
- one-sentence takeaway
- 2 to 3 reasons it matters, key numbers, or caveats

## Writing Style

Write for fast scanning:

- put conclusions before background
- keep each bullet to one idea
- cut repetition aggressively
- normalize field names across entries
- favor consistent labels over decorative variation

For Chinese business or research digests:

- use short labels like `一句话总结` `关键数据` `为什么值得读` `建议`
- keep paragraphs compact
- preserve important English paper titles when present

## Templates And Reference

Read [references/wecom_templates.md](references/wecom_templates.md) when you need ready-to-paste templates, a rewrite pattern for table-heavy drafts, or a compact list of safe Markdown features.
