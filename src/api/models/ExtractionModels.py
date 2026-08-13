"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel
from pydantic import computed_field

from src.common.Constants import Constants


class ExtractionDetail(BaseModel):
    """
    검토 대기열에 올라온 추출 결과 하나.

    source_text 를 함께 내보낸다 — 사람이 승인하려면 규격과 원문을 나란히 봐야 한다.
    청크 하나뿐이라 표가 잘려 있을 수 있어, 원본 문서의 해당 페이지를 여는
    source_url 도 같이 준다.
    """

    id: int
    task_id: str
    document_name: str
    vendor_name: str
    device_name: str
    api_version: str
    chunk_id: int
    page_number: int
    source_text: str
    spec_json: dict
    score: int
    review_status: str
    api_def_id: int | None = None
    created_at: datetime | None = None
    reviewed_at: datetime | None = None

    @computed_field  # type: ignore[prop-decorator]
    @property
    def source_url(self) -> str:
        """
        원본 문서의 해당 페이지를 여는 주소.

        #page= 는 브라우저 PDF 뷰어가 읽는 조각 식별자다. 서버로는 전송되지 않으므로
        엔드포인트는 문서 전체를 내려주고 페이지 이동은 뷰어가 한다.
        """
        return f"{Constants.EXTRACTION_ENDPOINT}/{self.id}/source#page={self.page_number}"


class ExtractionStats(BaseModel):
    """
    문서별 검토 현황 한 줄. UI 대시보드의 문서 목록 행이다.

    approved + pending + rejected = total 이다. avg_score 가 낮은 문서부터
    내려온다 — 사람 손이 먼저 가야 할 문서가 위에 오도록.
    """

    document_name: str
    total: int
    approved: int
    pending: int
    rejected: int
    avg_score: float


class ReviewResult(BaseModel):
    """승인/반려 처리 결과."""

    id: int
    review_status: str
    api_def_id: int | None = None

    #: 승인 시점의 근거 대조 점수. 사람이 규격을 고쳐 보냈으면 그것을 다시 채점한 값이라,
    #: 수정으로 근거가 맞아떨어졌는지 응답만 보고 알 수 있다.
    score: int | None = None
