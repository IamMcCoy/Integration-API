"""
2026-08-06 실험 재현용. data/ 의 PDF 전부에 세 분할 설정을 나란히 돌린다.

  A. h1~h3            실험 당시 구현
  B. h1~h6            헤더 전부를 경계로
  C. h1~h6 + 병합     헤더는 전부 잡되, chunk_size 에 못 미치는 인접 섹션은 이어 붙인다  ← 채택

C 는 MarkdownChunker 에 반영됐다. A/B 와 비교하려고 여기 세 구현을 다 남겨 둔다.
본 코드를 import 하지 않는 이유가 그것이다 — 지금 코드는 C 뿐이라 비교가 안 된다.

    PYTHONPATH=. poetry run python docs/experiments/scripts/2026-08-06-chunk-settings.py \
        /tmp/mdcache /tmp/result.json

변환한 마크다운은 첫 인자 디렉토리에 캐시한다. PDF 재파싱이 실행당 5분이라.
→ docs/experiments/2026-08-06-header-level-and-chunk-merge.md
"""
from __future__ import annotations

import asyncio
import json
import re
import statistics
import sys
from pathlib import Path

from src.core.chunker.TextSplitter import TextSplitter
from src.core.converter.PDFConverter import PDFConverter

PAGE_BREAK = re.compile(r'<!--\s*PAGE_BREAK:\s*(\d+)\s*-->')
FENCE = re.compile(r'^\s*(```|~~~)')
TABLE_DIVIDER = re.compile(r'^\s*\|[\s:|-]+\|\s*$')

CACHE = Path(sys.argv[1])


def split_by_header(content: str, max_level: int) -> list[tuple[str, list[str]]]:
    pattern = re.compile(r'^(#{1,%d})\s+(.+?)\s*$' % max_level)
    sections: list[tuple[str, list[str]]] = []
    stack = ['' for _ in range(max_level)]
    buffer: list[str] = []
    fence: str | None = None

    for line in content.split('\n'):
        marker = FENCE.match(line)
        if marker:
            if fence is None:
                fence = marker.group(1)
            elif marker.group(1) == fence:
                fence = None

        header = pattern.match(line) if fence is None else None
        if header:
            if any(x.strip() for x in buffer):
                sections.append(('\n'.join(buffer), [h for h in stack if h]))
            buffer = []
            level = len(header.group(1))
            stack[level - 1] = header.group(2)
            for lower in range(level, max_level):
                stack[lower] = ''
        buffer.append(line)

    if any(x.strip() for x in buffer):
        sections.append(('\n'.join(buffer), [h for h in stack if h]))
    return sections


def merge_small(sections: list[tuple[str, list[str]]], size: int) -> list[tuple[str, list[str]]]:
    """상한에 못 미치는 인접 섹션을 이어 붙인다. 헤더 경로는 묶음의 첫 섹션 것을 쓴다."""
    merged: list[tuple[str, list[str]]] = []
    buffer = ''
    headings: list[str] = []

    for text, path in sections:
        if buffer and len(buffer) + len(text) + 1 > size:
            merged.append((buffer, headings))
            buffer, headings = '', []
        if not buffer:
            headings = path
        buffer = f"{buffer}\n{text}" if buffer else text

    if buffer:
        merged.append((buffer, headings))
    return merged


def chunk(content: str, size: int, overlap: int, max_level: int, merge: bool) -> list[dict]:
    sections = split_by_header(content, max_level)
    if merge:
        sections = merge_small(sections, size)

    out: list[dict] = []
    page = 1
    for section, headings in sections:
        for piece in TextSplitter.split(section, size, overlap):
            markers = PAGE_BREAK.findall(piece)
            here = page
            if markers:
                page = int(markers[-1])
            text = PAGE_BREAK.sub('', piece).strip()
            if not text:
                continue
            out.append({'text': text, 'headings': headings, 'page': here})
    return out


def stats(chunks: list[dict], size: int) -> dict:
    if not chunks:
        return {'chunks': 0, 'median': 0, 'over': 0, 'tiny': 0, 'no_heading': 0, 'worst_group': 0}
    sizes = [len(c['text']) for c in chunks]
    groups: dict[str, int] = {}
    for c in chunks:
        key = ' > '.join(c['headings'])
        groups[key] = groups.get(key, 0) + 1
    return {
        'chunks': len(chunks),
        'median': int(statistics.median(sizes)),
        'over': sum(1 for s in sizes if s > size),
        'tiny': sum(1 for s in sizes if s < 50),
        'no_heading': sum(1 for c in chunks if not c['headings']),
        'worst_group': max(groups.values()),
    }


async def markdown_for(pdf: Path) -> str:
    cached = CACHE / (pdf.stem + '.md')
    if cached.is_file():
        return cached.read_text(encoding='utf-8')
    md = await PDFConverter().to_markdown(str(pdf))
    cached.write_text(md, encoding='utf-8')
    return md


async def main() -> None:
    size, overlap = 1000, 200
    CACHE.mkdir(parents=True, exist_ok=True)
    results = []

    for pdf in sorted(Path('data').glob('*.pdf')):
        md = await markdown_for(pdf)
        lines = md.split('\n')
        results.append({
            'file': pdf.name,
            'chars': len(md),
            'tables': sum(1 for x in lines if TABLE_DIVIDER.match(x)),
            'A': stats(chunk(md, size, overlap, 3, False), size),
            'B': stats(chunk(md, size, overlap, 6, False), size),
            'C': stats(chunk(md, size, overlap, 6, True), size),
        })
        print(f"done: {pdf.name}", file=sys.stderr, flush=True)

    Path(sys.argv[2]).write_text(json.dumps(results, ensure_ascii=False, indent=2), encoding='utf-8')


if __name__ == '__main__':
    asyncio.run(main())
