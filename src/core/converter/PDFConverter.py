"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

import opendataloader_pdf

from src.common.LoggerManager import LoggerManager
from src.core.converter.BaseConverter import BaseConverter
from src.core.reader.FileReader import FileReader


class PDFConverter(BaseConverter):
    """PDF 를 마크다운으로 변환한다. 페이지 경계는 PAGE_BREAK 마커로 남긴다."""

    def __init__(self):
        self.logger = LoggerManager.get()

    async def to_markdown(self, file_path: str) -> str:
        output_dir = Path(file_path).parent / Path(file_path).stem
        output_dir.mkdir(exist_ok=True)

        # opendataloader_pdf 는 동기 호출이고 PDF 크기에 따라 수 초가 걸린다.
        # 그대로 await 없이 부르면 이벤트 루프가 통째로 멈춘다.
        #
        # include_header_footer 는 기본값(False)을 쓴다. 페이지마다 반복되는
        # 머리말/꼬리말이 제거되므로 검색 품질에는 대체로 유리하다. 다만 모든 페이지가
        # 같은 레이아웃인 문서는 본문까지 반복으로 판정돼 통째로 사라진다.
        # 그런 문서를 다루게 되면 이 값을 True 로 (필요하면 설정으로 빼서) 넘길 것.
        await asyncio.to_thread(
            opendataloader_pdf.convert,
            input_path=[file_path],
            output_dir=str(output_dir),
            format='markdown',
            markdown_page_separator=self.PAGE_BREAK_TEMPLATE,
        )

        md_files = sorted(output_dir.glob('*.md'))
        if not md_files:
            raise ValueError(f"No markdown output: {file_path}")

        md_file_path = str(md_files[0])
        self.logger.info(f"PDF converted to markdown: {md_file_path}")

        content: str = await FileReader.read_text(md_file_path)
        return self.clean(content)
