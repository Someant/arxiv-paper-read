# arxiv-paper-read

A toolset for automatically reading and reviewing arXiv papers, specifically focusing on the `cs.CV` category.

## Workflow

```mermaid
graph TD
    subgraph paper-read [paper-read Pipeline]
    A[arXiv Daily Crawl] --> B{Abstract-level Screening}
    B -->|Score >= 4| C[Download PDF]
    B -->|Score 2-3| B1[Abstract Summary Only]
    B -->|Score <= 1| Z[Discard]
    
    subgraph scoring [Quick Scoring Matrix]
    B -- "Novelty (0-2)" --- B_S
    B -- "Importance (0-2)" --- B_S
    B -- "Experiment (0-1)" --- B_S
    B -- "Institution (0-1)" --- B_S
    B_S[Total Score / 6]
    end

    C --> D{PDF-level Screening}
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
    B1 --> J
```

## Quick Scoring Matrix

In Stage 1 (Abstract Screening), papers are evaluated based on their title and abstract (no PDF download yet):

- **Novelty (0-2)**: New paradigms or meaningful improvements.
- **Importance (0-2)**: Core CV problems (Video, 3D, Multi-modal, Foundation Models).
- **Experiment (0-1)**: SOTA comparisons with specific performance numbers.
- **Institution (0-1)**: Top-tier AI labs or recognized research groups.

**Selection Rules**:
- **Score ≥ 4**: Selected for Deep Review (PDF download).
- **Score 2-3**: Included in the "Abstract-only Summary" section.
- **Score ≤ 1**: Discarded.

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
