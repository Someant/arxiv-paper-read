"""
arXiv 论文爬取 + PDF 下载脚本（两阶段模式）

模式 1 — meta-only（默认）：
  爬取当天全量论文元数据（标题 + 摘要），无需下载 PDF。
  供 agent 读全量摘要做初筛。

模式 2 — download：
  读取 --ids-file 中的 arxiv_id 列表，只下载这些论文的 PDF。
  供 agent 对初筛结果做全文深度阅读。

用法:
  # 阶段 1：爬取元数据
  python crawl_and_extract.py \
    --category cs.CV \
    --output /tmp/arxiv_all.json \
    --mode meta-only

  # 阶段 2：下载 Top N PDF
  python crawl_and_extract.py \
    --category cs.CV \
    --output /tmp/arxiv_selected.json \
    --pdf-dir /tmp/arxiv-papers \
    --mode download \
    --ids-file /tmp/arxiv_topn.json \
    --all-meta /tmp/arxiv_all.json
"""

import argparse
import json
import os
import re
import sys
import time
from typing import Optional

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:
    print("错误: 请先安装依赖: pip install requests beautifulsoup4")
    sys.exit(1)


ARXIV_LIST_URL = "https://arxiv.org/list/{category}/recent?skip=0&show=2000"
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
}


# ─────────────────────────────────────────────────────────
# 爬取元数据
# ─────────────────────────────────────────────────────────


def fetch_arxiv_list(category: str) -> list[dict]:
    """爬取 arXiv 列表页，返回当天全量论文元数据"""
    url = ARXIV_LIST_URL.format(category=category)
    print(f"[爬取] {url}")

    resp = requests.get(url, headers=HEADERS, timeout=30)
    resp.raise_for_status()
    soup = BeautifulSoup(resp.text, "html.parser")

    papers = []
    dl = soup.find("dl")
    if not dl:
        print("[警告] 未找到论文列表，arXiv 页面结构可能已变化")
        return papers

    for dt, dd in zip(dl.find_all("dt"), dl.find_all("dd")):
        abs_link = dt.find("a", title="Abstract")
        if not abs_link:
            continue

        href = abs_link.get("href", "")
        arxiv_id = href.replace("/abs/", "").strip()

        title_div = dd.find("div", class_="list-title")
        title = re.sub(
            r"^Title:\s*",
            "",
            title_div.get_text(strip=True) if title_div else "Unknown",
        )

        authors_div = dd.find("div", class_="list-authors")
        authors = (
            [a.get_text(strip=True) for a in authors_div.find_all("a")]
            if authors_div
            else []
        )

        abstract = ""

        subjects_div = dd.find("div", class_="list-subjects")
        subjects = (
            subjects_div.get_text(strip=True).replace("Subjects:", "").strip()
            if subjects_div
            else ""
        )

        papers.append(
            {
                "arxiv_id": arxiv_id,
                "title": title,
                "authors": authors,
                "abstract": abstract,
                "abs_url": f"https://arxiv.org{href}",
                "subjects": subjects,
            }
        )

    print(f"[爬取] 发现 {len(papers)} 篇论文")
    return papers


def fetch_abstract_for_paper(arxiv_id: str, delay: float = 0.5) -> str:
    """从详情页抓取单篇论文摘要"""
    abs_url = f"https://arxiv.org/abs/{arxiv_id}"
    try:
        time.sleep(delay)
        resp = requests.get(abs_url, headers=HEADERS, timeout=30)
        resp.raise_for_status()
        soup = BeautifulSoup(resp.text, "html.parser")
        bq = soup.find("blockquote", class_="abstract")
        if bq:
            return bq.get_text(strip=True).replace("Abstract:", "").strip()
    except Exception as e:
        print(f"    [警告] 获取 {arxiv_id} 摘要失败: {e}")
    return ""


# ─────────────────────────────────────────────────────────
# PDF 下载
# ─────────────────────────────────────────────────────────


def download_pdf(arxiv_id: str, pdf_dir: str, delay: float = 1.5) -> Optional[str]:
    """下载单篇 PDF，返回本地路径；失败返回 None"""
    safe_id = arxiv_id.replace("/", "_")
    pdf_path = os.path.join(pdf_dir, f"{safe_id}.pdf")

    if os.path.exists(pdf_path):
        print(f"    [缓存] {arxiv_id}")
        return pdf_path

    time.sleep(delay)
    url = ARXIV_PDF_URL.format(arxiv_id=arxiv_id)

    for attempt in range(3):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=60, allow_redirects=True)
            resp.raise_for_status()

            if len(resp.content) < 5000:
                print(f"    [跳过] {arxiv_id}: 响应过小（非 PDF）")
                return None

            with open(pdf_path, "wb") as f:
                f.write(resp.content)

            print(f"    [OK] {arxiv_id} ({len(resp.content) // 1024} KB)")
            return pdf_path

        except Exception as e:
            if attempt < 2:
                print(f"    [重试 {attempt + 1}] {arxiv_id}: {e}")
                time.sleep(delay * 2)
            else:
                print(f"    [失败] {arxiv_id}: {e}")
                return None

    return None


# ─────────────────────────────────────────────────────────
# 主流程
# ─────────────────────────────────────────────────────────


def mode_meta_only(args):
    """阶段 1：爬取全量元数据，保存 JSON，不下载 PDF"""
    papers = fetch_arxiv_list(args.category)
    if not papers:
        print("[错误] 未爬取到论文")
        sys.exit(1)

    print(f"[抓取摘要] 开始从详情页获取 {len(papers)} 篇论文摘要...")
    for i, paper in enumerate(papers):
        if i % 20 == 0:
            print(f"  进度: {i + 1}/{len(papers)}")
        paper["abstract"] = fetch_abstract_for_paper(paper["arxiv_id"])

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(papers, f, ensure_ascii=False, indent=2)

    non_empty = sum(1 for p in papers if p.get("abstract", "").strip())
    print(f"\n{'=' * 50}")
    print(f"完成: {len(papers)} 篇元数据 (其中 {non_empty} 篇有摘要)")
    print(f"输出: {args.output}")
    print(f"{'=' * 50}")
    print(f"\n[下一步] 读取 {args.output}，对每篇标题和摘要做初筛评分。")
    print(
        f"         将初筛选出的 arxiv_id 写入 /tmp/arxiv_topn.json，再运行 --mode download。"
    )


def mode_download(args):
    """阶段 2：读取 ids-file，只下载指定论文的 PDF"""
    if not args.ids_file or not os.path.exists(args.ids_file):
        print(f"[错误] --ids-file 未指定或不存在: {args.ids_file}")
        sys.exit(1)
    if not args.all_meta or not os.path.exists(args.all_meta):
        print(f"[错误] --all-meta 未指定或不存在: {args.all_meta}")
        sys.exit(1)

    with open(args.ids_file, encoding="utf-8") as f:
        selected_ids: list[str] = json.load(f)

    with open(args.all_meta, encoding="utf-8") as f:
        all_papers: list[dict] = json.load(f)

    id_set = set(selected_ids)
    selected = [p for p in all_papers if p["arxiv_id"] in id_set]

    if not selected:
        print("[错误] 在全量元数据中未找到 ids-file 中的任何论文")
        sys.exit(1)

    os.makedirs(args.pdf_dir, exist_ok=True)
    print(f"[下载] 共 {len(selected)} 篇论文需要下载 PDF")

    for i, paper in enumerate(selected):
        arxiv_id = paper["arxiv_id"]
        print(f"[{i + 1}/{len(selected)}] {arxiv_id}: {paper['title'][:55]}...")
        paper["pdf_path"] = download_pdf(arxiv_id, args.pdf_dir, delay=args.delay)

    with open(args.output, "w", encoding="utf-8") as f:
        json.dump(selected, f, ensure_ascii=False, indent=2)

    downloaded = sum(1 for p in selected if p.get("pdf_path"))
    print(f"\n{'=' * 50}")
    print(f"完成: {len(selected)} 篇，PDF 下载 {downloaded} 篇")
    print(f"输出: {args.output}")
    print(f"{'=' * 50}")
    print(f"\n[下一步] 读取 {args.output}，对每篇 pdf_path 全文精读并深度分析。")


def main():
    parser = argparse.ArgumentParser(
        description="arXiv 两阶段论文爬取（meta-only / download）",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--category", default="cs.CV", help="arXiv 分类")
    parser.add_argument(
        "--output", default="/tmp/arxiv_all.json", help="输出 JSON 路径"
    )
    parser.add_argument("--pdf-dir", default="/tmp/arxiv-papers", help="PDF 临时目录")
    parser.add_argument(
        "--mode",
        default="meta-only",
        choices=["meta-only", "download"],
        help="meta-only=只爬元数据；download=下载指定 PDF",
    )
    parser.add_argument(
        "--ids-file", default=None, help="[download] 初筛 arxiv_id 列表 JSON"
    )
    parser.add_argument(
        "--all-meta", default=None, help="[download] 全量元数据 JSON 路径"
    )
    parser.add_argument("--delay", type=float, default=1.5, help="下载间隔秒数")
    args = parser.parse_args()

    if args.mode == "meta-only":
        mode_meta_only(args)
    else:
        mode_download(args)


if __name__ == "__main__":
    main()
