# Paper Read Scripts

## 设计原则

脚本只做 HTTP I/O（爬取 + 下载）。  
Agent 用自身的文件读取能力阅读 PDF **全文**，无页数限制，无文本提取损耗。  
无 LLM API 调用，无 pdfplumber。

## 依赖

```bash
pip install requests beautifulsoup4
```

## crawl_and_extract.py

爬取论文列表 + 下载 PDF → 输出 JSON（含 `pdf_path`）。

```bash
python crawl_and_extract.py \
  --category cs.CV \
  --output /tmp/arxiv_papers.json \
  --pdf-dir /tmp/arxiv-papers \
  --max 20 \
  --keywords "diffusion" "video generation"
```

输出 JSON 结构（每篇）：

```json
{
  "arxiv_id": "2503.12345",
  "title": "...",
  "authors": ["..."],
  "abstract": "...",
  "abs_url": "https://arxiv.org/abs/2503.12345",
  "subjects": "cs.CV; cs.AI",
  "pdf_path": "/tmp/arxiv-papers/2503.12345.pdf"
}
```

Agent 收到 `pdf_path` 后，直接读取该文件获得完整论文文本，再进行分析。

## cleanup.py

删除 PDF 临时目录。

```bash
python cleanup.py --pdf-dir /tmp/arxiv-papers
python cleanup.py --pdf-dir /tmp/arxiv-papers --dry-run  # 仅列出不删除
```
