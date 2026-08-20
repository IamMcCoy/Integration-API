from __future__ import annotations

import aiofiles

from src.common.LoggerManager import LoggerManager
from src.core.chunker.MarkdownChunker import MarkdownChunker
from src.core.chunker.TextSplitter import TextSplitter
from src.core.converter.PDFConverter import PDFConverter


class FileParser:
    """
    포맷별 처리기. 어떤 포맷이든 {chunk_id, text, metadata} 구조를 돌려준다.

    이 구조가 뒤따르는 선별/추출 단계의 입력 계약이다.
    """

    def __init__(self):
        self.logger = LoggerManager.get()
        self.pdf_converter = PDFConverter()

    async def process_txt_file(
            self,
            file_path: str,
            chunk_size: int,
            chunk_overlap: int,
    ) -> list[dict]:
        """TXT 파일을 청크 단위로 처리"""
        try:
            async with aiofiles.open(file_path, 'r', encoding='utf-8') as f:
                text: str = await f.read()

            if not text or not text.strip():
                self.logger.warning(f"Empty TXT file: {file_path}")
                return []

            chunks: list[str] = TextSplitter.split(text, chunk_size, chunk_overlap)

            # 평문에는 헤더도 페이지 경계도 없다. 구조는 맞추되 값은 비워 둔다.
            chunks_with_metadata: list[dict] = [
                {
                    'chunk_id': idx,
                    'text': chunk,
                    'metadata': {'headings': [], 'page_number': 1},
                }
                for idx, chunk in enumerate(chunks)
            ]

            self.logger.info(
                f"Processed TXT file: {len(chunks_with_metadata)} chunks "
                f"(chunk_size={chunk_size}, overlap={chunk_overlap})",
            )
            return chunks_with_metadata

        except Exception as e:
            self.logger.error(f"TXT processing error: {str(e)}")
            raise ValueError(f"Invalid TXT format: {str(e)}")

    async def process_pdf_file(self, file_path: str, chunk_size: int, chunk_overlap: int) -> list[dict]:
        """PDF 파일을 마크다운으로 변환 후 청크 단위로 처리"""
        try:
            markdown_content: str = await self.pdf_converter.to_markdown(file_path)
            chunks: list[dict] = MarkdownChunker.chunk(
                content=markdown_content,
                chunk_size=chunk_size,
                chunk_overlap=chunk_overlap,
            )

            self.logger.info(
                f"Successfully processed {file_path}: {len(chunks)} chunks created",
            )
            return chunks

        except Exception as e:
            self.logger.error(f"PDF processing error: {str(e)}")
            raise ValueError(f"Invalid PDF format: {str(e)}")
