# arxiv-paper-read

A toolset for automatically reading and reviewing arXiv papers, specifically focusing on the `cs.CV` category.

## Workflow

```mermaid
graph TD
    subgraph paper-read [paper-read Pipeline]
    A[arXiv Daily Crawl] --> B{Abstract Screening}
    B -->|Passed| C[Download PDF]
    B -->|Discarded| Z[End]
    C --> D{PDF Screening}
    D -->|Passed| E[Trigger paper-read-reviewer]
    D -->|Discarded| Z
    end

    subgraph paper-read-reviewer [paper-read-reviewer Subagent]
    E --> F[Full PDF Parsing]
    F --> G[Deep Analysis: Contribution/Exp/Ablation]
    G --> H{OpenJudge 5-Stage Review}
    subgraph OpenJudge [OpenJudge Pipeline]
    H --> H1[Security Check]
    H --> H2[Correctness Check]
    H --> H3[Academic Review]
    H --> H4[Criticality Verification]
    H --> H5[BibCheck]
    end
    H1 & H2 & H3 & H4 & H5 --> I[Synthesize Review Result]
    end

    I --> J[Final Markdown Report Generation]
    J --> K[WeCom Push / Slack Notification]
```

## Components

### 1. [paper-read](./paper-read)
An automated pipeline that:
- Crawls the daily new papers from arXiv.
- Performs a two-stage screening process (Abstract-level followed by PDF-level).
- Uses the `paper-read-reviewer` subagent for in-depth analysis.
- Generates a comprehensive Markdown report.

### 2. [paper-read-reviewer](./paper-read-reviewer)
A specialized subagent skill for:
- Reading full PDF papers.
- Performing deep analysis (contributions, experiments, ablations).
- Executing the **OpenJudge 5-stage review** pipeline (Security, Correctness, Academic Review, Criticality Verification, BibCheck).
- Generating structured analysis results.

## Usage
Refer to the `SKILL.md` files in each directory for detailed instructions.
