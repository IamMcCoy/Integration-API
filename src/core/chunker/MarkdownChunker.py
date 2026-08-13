"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import re

from src.core.chunker.TextSplitter import TextSplitter

PAGE_BREAK_PATTERN = re.compile(r'<!--\s*PAGE_BREAK:\s*(\d+)\s*-->')
HEADER_PATTERN = re.compile(r'^(#{1,6})\s+(.+?)\s*$')
FENCE_PATTERN = re.compile(r'^\s*(```|~~~)')
TABLE_ROW_PATTERN = re.compile(r'^\s*\|')
TABLE_DIVIDER_PATTERN = re.compile(r'^\s*\|[\s:|-]+\|\s*$')

#: 마크다운 헤더 레벨 수. 전부 경계로 삼는다.
MAX_HEADER_LEVEL = 6


class MarkdownChunker:
    """
    마크다운 문자열을 청크로 분할한다. 어떤 파일에서 왔는지는 알지 못한다.

    헤더(h1~h6)로 먼저 나누고, 상한에 못 미치는 인접 섹션은 다시 이어 붙인 뒤,
    남은 큰 덩어리를 TextSplitter 로 자른다.

    PAGE_BREAK 마커가 있으면 page_number 메타데이터로 옮기고 본문에서는 지운다.
    마커가 없는 포맷은 전부 1페이지로 취급된다.
    """

    @staticmethod
    def chunk(content: str, chunk_size: int, chunk_overlap: int) -> list[dict]:
        chunks_with_metadata: list[dict] = []
        current_page = 1

        sections = MarkdownChunker._merge_small(
            MarkdownChunker._split_by_header(content), chunk_size,
        )

        for section, headings in sections:
            table_header = MarkdownChunker._find_table_header(section)

            for piece in TextSplitter.split(section, chunk_size, chunk_overlap):
                piece = MarkdownChunker._restore_table_header(piece, table_header, chunk_size)

                # 청크 시작 시점의 페이지를 그 청크의 페이지로 본다.
                # 한 청크가 페이지 경계를 걸치면 뒷부분은 이전 페이지로 기록된다.
                # 정확한 페이지별 분할이 필요해지면 마커 기준으로 먼저 자를 것.
                markers = PAGE_BREAK_PATTERN.findall(piece)
                chunk_page = current_page
                if markers:
                    current_page = int(markers[-1])

                text = PAGE_BREAK_PATTERN.sub('', piece).strip()
                if not text:
                    continue

                chunks_with_metadata.append({
                    'chunk_id': len(chunks_with_metadata),
                    'text': text,
                    'metadata': {
                        'headings': list(headings),
                        'page_number': chunk_page,
                    },
                })

        return chunks_with_metadata

    @staticmethod
    def _split_by_header(content: str) -> list[tuple[str, list[str]]]:
        """
        헤더를 만날 때마다 섹션을 끊고, 그 시점의 헤더 경로를 붙인다.

        h1~h6 를 전부 경계로 본다. 대상 문서는 개별 API 이름이 h5 에, 요청/응답 구분이
        h6 에 있어서 상위 세 레벨만 보면 청크가 어느 API 것인지 알 수 없다.

        헤더 줄은 본문에 남긴다 — 파라미터 표가 어느 API 의 것인지는 헤더에만 적혀 있다.
        코드 펜스 안의 '#' 은 헤더가 아니다. API 가이드에는 셸 예제가 흔하다.
        """
        sections: list[tuple[str, list[str]]] = []
        stack = ['' for _ in range(MAX_HEADER_LEVEL)]
        buffer: list[str] = []
        fence: str | None = None

        for line in content.split('\n'):
            marker = FENCE_PATTERN.match(line)
            if marker:
                if fence is None:
                    fence = marker.group(1)
                elif marker.group(1) == fence:
                    fence = None

            header = HEADER_PATTERN.match(line) if fence is None else None
            if header:
                if any(line_.strip() for line_ in buffer):
                    sections.append(('\n'.join(buffer), [h for h in stack if h]))
                buffer = []

                level = len(header.group(1))
                stack[level - 1] = header.group(2)
                for lower in range(level, MAX_HEADER_LEVEL):
                    stack[lower] = ''

            buffer.append(line)

        if any(line_.strip() for line_ in buffer):
            sections.append(('\n'.join(buffer), [h for h in stack if h]))

        return sections

    @staticmethod
    def _merge_small(
            sections: list[tuple[str, list[str]]],
            chunk_size: int,
    ) -> list[tuple[str, list[str]]]:
        """
        상한에 못 미치는 인접 섹션을 이어 붙인다. 헤더 경로는 묶음의 첫 섹션 것을 쓴다.

        헤더를 h6 까지 경계로 삼으면 'URL' 한 줄짜리 섹션이 쏟아진다. 같은 API 에 딸린
        조각들이라 따로 떼면 파라미터 표만 덩그러니 남고, 청크마다 LLM 을 부르므로
        비용도 그만큼 늘어난다.
        """
        merged: list[tuple[str, list[str]]] = []
        buffer = ''
        headings: list[str] = []

        for text, path in sections:
            if buffer and len(buffer) + len(text) + 1 > chunk_size:
                merged.append((buffer, headings))
                buffer, headings = '', []

            if not buffer:
                headings = path
            buffer = f"{buffer}\n{text}" if buffer else text

        if buffer:
            merged.append((buffer, headings))

        return merged

    @staticmethod
    def _find_table_header(section: str) -> str | None:
        """섹션 안 첫 표의 헤더 2행(컬럼명 + 구분선)을 찾는다."""
        lines = section.split('\n')
        for index in range(len(lines) - 1):
            if TABLE_ROW_PATTERN.match(lines[index]) and TABLE_DIVIDER_PATTERN.match(lines[index + 1]):
                return f"{lines[index]}\n{lines[index + 1]}"
        return None

    @staticmethod
    def _restore_table_header(piece: str, table_header: str | None, chunk_size: int) -> str:
        """
        표 중간부터 잘린 조각에 컬럼 헤더를 다시 붙인다.

        헤더가 없으면 '이 열이 타입인지 설명인지'를 알 수 없어 파라미터 추출이 통째로 어긋난다.
        """
        if not table_header:
            return piece

        # 컬럼명 줄은 짧다. 상한의 절반을 넘으면 변환기가 큰 데이터 셀을 헤더 행으로
        # 잡은 것이라, 붙여 봐야 청크만 부풀고 얻는 정보가 없다.
        if len(table_header) > chunk_size // 2:
            return piece

        lines = piece.lstrip('\n').split('\n')
        if not lines or not TABLE_ROW_PATTERN.match(lines[0]):
            return piece
        if any(TABLE_DIVIDER_PATTERN.match(line) for line in lines):
            return piece

        # 조각이 헤더 행 자체로 시작하면 붙이지 않는다. 셀 하나가 상한을 넘을 만큼 길면
        # 헤더 행과 구분선이 서로 다른 조각으로 갈라지는데, 그때 헤더를 또 붙이면
        # 같은 내용이 두 번 들어간다. 변환기가 JSON 예시 블록을 1컬럼 표로 잡을 때 그렇다.
        if lines[0].strip() == table_header.split('\n')[0].strip():
            return piece

        return f"{table_header}\n{piece}"


if __name__ == '__main__':
    sample = (
        '# 제목\n\n'
        '1페이지 본문입니다.\n\n'
        '<!-- PAGE_BREAK: 2 -->\n\n'
        '## 소제목\n\n'
        '2페이지 본문입니다.\n\n'
        '<!-- PAGE_BREAK: 3 -->\n\n'
        '3페이지 본문입니다.\n'
    )

    result = MarkdownChunker.chunk(sample, chunk_size=50, chunk_overlap=0)

    assert result, 'No chunks produced'
    assert [c['chunk_id'] for c in result] == list(range(len(result))), 'chunk_id not contiguous'

    for c in result:
        assert 'PAGE_BREAK' not in c['text'], f"Marker left in text: {c['text']!r}"
        assert c['text'].strip(), 'Empty chunk included'

    pages = [c['metadata']['page_number'] for c in result]
    assert pages == sorted(pages), f"Page numbers out of order: {pages}"
    assert pages[0] == 1, f"First chunk must be page 1: {pages[0]}"
    assert max(pages) == 3, f"Last page not tracked: {pages}"

    assert any('제목' in c['metadata']['headings'] for c in result), 'Missing h1 in headings'
    assert any('소제목' in c['metadata']['headings'] for c in result), 'Missing h2 in headings'

    assert MarkdownChunker.chunk('', chunk_size=50, chunk_overlap=0) == [], 'Empty input must yield []'

    # 헤더 경로는 상위부터 순서대로 쌓이고, 같은 레벨을 만나면 하위가 지워진다.
    nested = (
        '# 4. System\n\n' + 'a' * 60 + '\n\n'
        '### 4.1 시스템 정보\n\n' + 'b' * 60 + '\n\n'
        '##### 4.1.1 시스템 정보 조회\n\n' + 'c' * 60 + '\n\n'
        '###### Query parameters\n\n' + 'd' * 60 + '\n\n'
        '### 4.2 라이선스 정보\n\n' + 'e' * 60 + '\n'
    )
    deep = MarkdownChunker.chunk(nested, chunk_size=80, chunk_overlap=0)
    paths = [c['metadata']['headings'] for c in deep]
    assert ['4. System', '4.1 시스템 정보', '4.1.1 시스템 정보 조회', 'Query parameters'] in paths, \
        f"Deep heading path missing: {paths}"
    assert ['4. System', '4.2 라이선스 정보'] in paths, f"Sibling did not reset deeper levels: {paths}"

    # 코드 펜스 안의 '#' 은 헤더가 아니다.
    fenced = (
        '# 실제 헤더\n\n'
        '```bash\n'
        '# curl 예제입니다\n'
        'curl -X GET /api/v1/devices\n'
        '```\n'
    )
    fenced_chunks = MarkdownChunker.chunk(fenced, chunk_size=500, chunk_overlap=0)
    assert len(fenced_chunks) == 1, f"Fenced '#' split the section: {len(fenced_chunks)} chunks"
    assert fenced_chunks[0]['metadata']['headings'] == ['실제 헤더'], 'Fenced comment became a heading'

    # 상한에 못 미치는 인접 섹션은 한 청크로 묶인다.
    tiny_sections = ''.join(f"###### 항목 {i}\n\n짧은 줄 {i}\n\n" for i in range(10))
    merged_chunks = MarkdownChunker.chunk(tiny_sections, chunk_size=1000, chunk_overlap=0)
    assert len(merged_chunks) == 1, f"Small sections were not merged: {len(merged_chunks)} chunks"
    assert merged_chunks[0]['metadata']['headings'] == ['항목 0'], 'Merged chunk must keep the first path'

    # 상한을 넘는 표는 잘리되, 뒷조각에 컬럼 헤더가 다시 붙는다.
    rows = '\n'.join(f"| param{i} | string | 설명이 제법 길게 붙는 파라미터입니다 |" for i in range(12))
    table = (
        '## 요청 파라미터\n\n'
        '| 이름 | 타입 | 설명 |\n'
        '| --- | --- | --- |\n'
        f"{rows}\n"
    )
    table_chunks = MarkdownChunker.chunk(table, chunk_size=300, chunk_overlap=0)
    assert len(table_chunks) > 1, 'Table was not split; raise the row count'
    for c in table_chunks[1:]:
        assert '| 이름 | 타입 | 설명 |' in c['text'], f"Table header not restored: {c['text'][:60]!r}"

    # 셀 하나가 상한보다 길면 헤더 행과 구분선이 서로 다른 조각으로 갈라진다.
    # 그때 헤더를 또 붙이면 같은 내용이 두 번 들어간다.
    huge_cell = '| ' + 'x' * 400 + ' |'
    wide_table = f"## 예시\n\n{huge_cell}\n| --- |\n{huge_cell}\n"
    wide_chunks = MarkdownChunker.chunk(wide_table, chunk_size=200, chunk_overlap=0)
    for c in wide_chunks:
        assert c['text'].count('x' * 400) <= 1, 'Table header was duplicated onto itself'

    # 헤더 행이 상한의 절반을 넘으면 컬럼명이 아니라 데이터다. 붙이지 않는다.
    fat_header = '| ' + 'H' * 300 + ' |'
    fat_rows = '\n'.join(f"| 데이터 행 {i} 입니다 |" for i in range(30))
    fat_table = f"## 예시\n\n{fat_header}\n| --- |\n{fat_rows}\n"
    fat_chunks = MarkdownChunker.chunk(fat_table, chunk_size=400, chunk_overlap=0)
    data_chunks = [c for c in fat_chunks if c['text'].startswith('| 데이터')]
    assert data_chunks, 'Table was not split; raise the row count'
    assert not any('H' * 300 in c['text'] for c in data_chunks), \
        'Oversized header row must not be restored'

    print(f"OK: {len(result)} chunks, pages={pages}, deep path {len(paths[-1])} levels, "
          f"table split into {len(table_chunks)}")
