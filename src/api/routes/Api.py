"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import asyncio

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Response

from src.api.models.ApiModels import ApiDetail
from src.api.models.ApiModels import ApiSummary
from src.common.Constants import Constants

router = APIRouter(prefix=Constants.API_ENDPOINT, tags=['APIs'])


@router.get('', response_model=list[ApiSummary])
async def list_apis(
        request: Request,
        response: Response,
        vendor: str | None = Query(default=None, max_length=100),
        device: str | None = Query(default=None, max_length=100),
        method: str | None = Query(default=None, max_length=20),
        q: str | None = Query(default=None, max_length=200),
        document: str | None = Query(default=None, max_length=64),
        limit: int = Query(default=50, gt=0, le=500),
        offset: int = Query(default=0, ge=0),
):
    """
    검토를 통과해 적재된 운영 API 목록을 조회합니다.

    - **vendor / device / method / document**: 정확 일치 필터
    - **q**: 제목·엔드포인트 부분 일치 검색
    - 필터 적용 후 전체 건수는 `X-Total-Count` 헤더로 줍니다

    응답의 `document_name` 은 그 API 를 만든 출처 문서입니다.
    스키마 본문은 목록에 싣지 않습니다 — 단건 조회로 받으세요.
    """
    repository = request.app.state.repository

    try:
        # 동기 DB 호출이라 그대로 부르면 이벤트 루프가 멈춘다.
        rows, total = await asyncio.to_thread(
            repository.list_apis, vendor, device, method, q, document, limit, offset,
        )
    except Exception as e:
        repository.logger.error(f"API listing failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')

    response.headers['X-Total-Count'] = str(total)
    return rows


@router.get('/{api_def_id}', response_model=ApiDetail)
async def get_api(request: Request, api_def_id: int):
    """운영 API 하나를 요청/응답 스키마와 함께 조회합니다."""
    repository = request.app.state.repository

    try:
        row = await asyncio.to_thread(repository.get_api, api_def_id)
    except Exception as e:
        repository.logger.error(f"API lookup failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')

    if row is None:
        raise HTTPException(status_code=404, detail=f"API not found: {api_def_id}")

    return row
