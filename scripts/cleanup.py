"""
清理 PDF 临时目录脚本

用法:
    python cleanup.py --pdf-dir /tmp/arxiv-papers
    python cleanup.py --pdf-dir /tmp/arxiv-papers --dry-run
"""

import argparse
import os
import shutil
import sys


def main():
    parser = argparse.ArgumentParser(description="清理 PDF 临时文件")
    parser.add_argument("--pdf-dir", default="/tmp/arxiv-papers", help="PDF 目录路径")
    parser.add_argument("--dry-run", action="store_true", help="仅列出文件，不实际删除")
    args = parser.parse_args()

    if not os.path.exists(args.pdf_dir):
        print(f"[跳过] 目录不存在: {args.pdf_dir}")
        sys.exit(0)

    pdf_files = [f for f in os.listdir(args.pdf_dir) if f.endswith(".pdf")]
    total_size = sum(
        os.path.getsize(os.path.join(args.pdf_dir, f))
        for f in pdf_files
        if os.path.isfile(os.path.join(args.pdf_dir, f))
    )

    print(f"[清理] 目录: {args.pdf_dir}")
    print(f"       PDF 文件数: {len(pdf_files)}")
    print(f"       占用空间: {total_size // 1024 // 1024:.1f} MB")

    if args.dry_run:
        print("[演习] 以下文件将被删除:")
        for f in pdf_files:
            print(f"  - {f}")
        return

    try:
        shutil.rmtree(args.pdf_dir)
        print(f"[OK] 已删除: {args.pdf_dir}")
    except Exception as e:
        print(f"[错误] 删除失败: {e}")
        print(f"       请手动删除: rm -rf {args.pdf_dir}")
        sys.exit(1)


if __name__ == "__main__":
    main()
