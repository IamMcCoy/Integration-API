"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class ApiSummary(BaseModel):
    """운영 테이블(api_definition)의 API 한 건. 목록 화면용."""

    id: int
    vendor_name: str
    device_name: str
    api_version: str

    #: 이 API 를 만든 출처 문서. 여러 문서가 기여했으면 가장 최근 것.
    document_name: str | None = None

    title: str
    group_name: str | None = None
    method: str | None = None
    endpoint: str | None = None
    created_at: datetime | None = None
    updated_at: datetime | None = None


class ApiDetail(ApiSummary):
    """단건 상세. api_schema 의 요청/응답 파라미터가 붙는다."""

    request: list | dict | None = None
    response: list | dict | None = None
