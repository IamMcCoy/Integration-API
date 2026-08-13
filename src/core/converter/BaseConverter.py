"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import re
from abc import ABC
from abc import abstractmethod


class BaseConverter(ABC):
    """
    파일을 마크다운 문자열로 변환하는 컨버터 계약.

    확장자를 추가하려면 이 클래스를 상속해 to_markdown 만 구현한다.
    읽기 시점은 구현체 자유다 (PDF 는 변환 후 읽고, 텍스트 계열은 읽어서 바로 반환).

    페이지 정보가 있는 포맷은 마크다운에 PAGE_BREAK 주석을 심어두면
    MarkdownChunker 가 page_number 메타데이터로 옮겨준다.
    """

    #: 페이지 경계 마커. 청커와 공유하는 계약이다.
    PAGE_BREAK_TEMPLATE = '<!-- PAGE_BREAK: %page-number% -->'

    @abstractmethod
    async def to_markdown(self, file_path: str) -> str:
        """파일 경로를 받아 정제된 마크다운 문자열을 반환"""
        raise NotImplementedError

    @staticmethod
    def clean(content: str) -> str:
        """변환 결과 마크다운 정제 (모든 컨버터 공통)"""
        # 모든 이미지 마크다운 제거 (빈 것 포함)
        content = re.sub(r'!\[.*?]\(.*?\)', '', content)

        # 여러 개의 연속된 빈 줄을 두 개로 제한
        content = re.sub(r'\n{3,}', '\n\n', content)

        return content.strip()
