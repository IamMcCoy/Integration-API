"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

#: 우선순위 순서. 앞쪽 구분자로 잘라서 크기가 맞으면 뒤쪽은 쓰지 않는다.
DEFAULT_SEPARATORS = ['\n\n', '\n', ' ', '']


class TextSplitter:
    """
    문자열을 크기 기준으로 자른다. 마크다운도 파일도 알지 못한다.

    구분자를 우선순위대로 시도해서 의미 단위를 최대한 보존한다 — 문단이 통째로
    들어가면 문단으로 두고, 넘칠 때만 더 잘게 쪼갠다.
    """

    @staticmethod
    def split(
            text: str,
            chunk_size: int,
            chunk_overlap: int,
            separators: list[str] | None = None,
    ) -> list[str]:
        if chunk_overlap >= chunk_size:
            raise ValueError('chunk_overlap must be smaller than chunk_size')

        if not text.strip():
            return []

        return TextSplitter._split(
            text, chunk_size, chunk_overlap,
            list(separators) if separators else list(DEFAULT_SEPARATORS),
        )

    @staticmethod
    def _split(text: str, size: int, overlap: int, separators: list[str]) -> list[str]:
        separator, remaining = TextSplitter._pick_separator(text, separators)
        pieces = list(text) if separator == '' else text.split(separator)

        chunks: list[str] = []
        buffer: list[str] = []

        for piece in pieces:
            if not piece:
                continue

            if len(piece) <= size:
                buffer.append(piece)
                continue

            # 조각 하나가 이미 상한을 넘는다. 모아둔 것을 먼저 내보내고 더 잘게 쪼갠다.
            chunks.extend(TextSplitter._merge(buffer, separator, size, overlap))
            buffer = []

            if remaining:
                chunks.extend(TextSplitter._split(piece, size, overlap, remaining))
            else:
                # 구분자를 다 써도 안 줄어드는 경우다. 문자 단위('')까지 갔다는
                # 뜻이라 여기 도달하면 그대로 내보낸다. 상한 초과를 감수한다.
                chunks.append(piece)

        chunks.extend(TextSplitter._merge(buffer, separator, size, overlap))
        return chunks

    @staticmethod
    def _pick_separator(text: str, separators: list[str]) -> tuple[str, list[str]]:
        """텍스트에 실제로 들어 있는 첫 구분자를 고른다. 없으면 마지막 것으로 떨어진다."""
        for index, separator in enumerate(separators):
            if separator == '' or separator in text:
                return separator, separators[index + 1:]
        return separators[-1], []

    @staticmethod
    def _merge(pieces: list[str], separator: str, size: int, overlap: int) -> list[str]:
        """조각들을 상한을 넘지 않게 이어 붙이고, 경계마다 overlap 만큼 겹쳐 둔다."""
        sep_len = len(separator)
        merged: list[str] = []
        buffer: list[str] = []
        total = 0

        for piece in pieces:
            piece_len = len(piece)

            if total + piece_len + (sep_len if buffer else 0) > size and buffer:
                text = separator.join(buffer).strip()
                if text:
                    merged.append(text)

                # 다음 청크가 앞 청크와 overlap 만큼 겹치도록 앞에서부터 덜어낸다.
                while buffer and (
                    total > overlap
                    or (total + piece_len + (sep_len if buffer else 0) > size and total > 0)
                ):
                    total -= len(buffer[0]) + (sep_len if len(buffer) > 1 else 0)
                    buffer.pop(0)

            buffer.append(piece)
            total += piece_len + (sep_len if len(buffer) > 1 else 0)

        text = separator.join(buffer).strip()
        if text:
            merged.append(text)

        return merged


if __name__ == '__main__':
    paragraphs = '\n\n'.join(f"문단 {i} 입니다." for i in range(10))
    result = TextSplitter.split(paragraphs, chunk_size=40, chunk_overlap=0)

    assert result, 'No chunks produced'
    for chunk in result:
        assert chunk.strip(), 'Empty chunk included'
    assert ''.join(result).replace('\n', '') == paragraphs.replace('\n', ''), 'Content lost or duplicated'

    # 상한을 넘는 조각은 다음 구분자로 더 잘게 쪼개진다.
    long_line = 'a' * 100
    pieces = TextSplitter.split(long_line, chunk_size=30, chunk_overlap=0)
    assert len(pieces) > 1, 'Oversized piece was not split'
    assert all(len(p) <= 30 for p in pieces), f"Chunk exceeds size: {[len(p) for p in pieces]}"

    # 오버랩이 있으면 인접 청크가 실제로 겹친다.
    sentences = ' '.join(f"word{i}" for i in range(60))
    overlapped = TextSplitter.split(sentences, chunk_size=60, chunk_overlap=20)
    assert len(overlapped) > 1, 'Expected multiple chunks'
    assert any(
        overlapped[i].split()[-1] in overlapped[i + 1]
        for i in range(len(overlapped) - 1)
    ), 'No overlap between adjacent chunks'

    assert TextSplitter.split('', chunk_size=10, chunk_overlap=0) == [], 'Empty input must yield []'
    assert TextSplitter.split('   \n  ', chunk_size=10, chunk_overlap=0) == [], 'Blank input must yield []'

    try:
        TextSplitter.split('abc', chunk_size=10, chunk_overlap=10)
        raise AssertionError('Expected ValueError for overlap >= size')
    except ValueError:
        pass

    print(f"OK: {len(result)} chunks, {len(pieces)} split pieces, {len(overlapped)} overlapped")
