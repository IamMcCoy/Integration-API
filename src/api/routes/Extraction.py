"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import asyncio
import mimetypes
from pathlib import Path

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request
from fastapi import Response
from fastapi.responses import FileResponse

from src.api.models.ExtractionModels import ExtractionDetail
from src.api.models.ExtractionModels import ExtractionStats
from src.api.models.ExtractionModels import ReviewResult
from src.common.Constants import Constants
from src.core.extractor.SpecModels import ApiSpec
from src.core.extractor.SpecRepository import ExtractionNotFound
from src.core.extractor.SpecScorer import APPROVED
from src.core.extractor.SpecScorer import PENDING
from src.core.extractor.SpecScorer import REJECTED
from src.core.reader.FileReader import FileReader

router = APIRouter(prefix=Constants.EXTRACTION_ENDPOINT, tags=['Extractions'])


@router.get('', response_model=list[ExtractionDetail])
async def list_extractions(
        request: Request,
        response: Response,
        status: str = Query(default=PENDING, pattern=f"^({APPROVED}|{PENDING}|{REJECTED})$"),
        limit: int = Query(default=50, gt=0, le=500),
        offset: int = Query(default=0, ge=0),
        document_name: str | None = Query(default=None, max_length=64),
        vendor_name: str | None = Query(default=None, max_length=100),
        device_name: str | None = Query(default=None, max_length=100),
):
    """
    검토 대기열을 조회합니다.

    신뢰도 점수가 높은 추출은 자동으로 적재되고, 중간 점수는 `pending` 으로 남아
    사람의 승인을 기다립니다. 낮은 점수는 `rejected` 로 쌓입니다.

    문서/벤더/장비 필터와 `offset` 페이지네이션을 지원하고, 필터 적용 후 전체
    건수를 `X-Total-Count` 헤더로 줍니다. 응답에는 추출된 규격(`spec_json`)과
    근거가 된 원문(`source_text`)이 함께 담깁니다.
    """
    repository = request.app.state.repository

    try:
        # 동기 DB 호출이라 그대로 부르면 이벤트 루프가 멈춘다.
        rows, total = await asyncio.to_thread(
            repository.list_by_status, status, limit, offset,
            document_name, vendor_name, device_name,
        )
    except Exception as e:
        repository.logger.error(f"Extraction listing failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')

    response.headers['X-Total-Count'] = str(total)
    return rows


@router.get('/stats', response_model=list[ExtractionStats])
async def extraction_stats(request: Request):
    """
    문서별 검토 현황을 집계합니다. 평균 점수가 낮은 문서부터 옵니다.

    UI 대시보드용입니다 — 문서마다 자동 적재/대기/반려 건수와 평균 점수를
    한 번의 호출로 받아 목록·뱃지·진행률을 그립니다.
    """
    repository = request.app.state.repository

    try:
        return await asyncio.to_thread(repository.stats)
    except Exception as e:
        repository.logger.error(f"Extraction stats failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')


@router.get('/{extraction_id}', response_model=ExtractionDetail)
async def get_extraction(request: Request, extraction_id: int):
    """
    추출 결과 하나를 조회합니다. UI 검토 상세 화면·새로고침용입니다.

    승인/반려 처리 후 이 주소로 다시 읽으면 갱신된 상태가 보입니다.
    """
    repository = request.app.state.repository

    try:
        row = await asyncio.to_thread(repository.find, extraction_id)
    except Exception as e:
        repository.logger.error(f"Extraction lookup failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')

    if row is None:
        raise HTTPException(status_code=404, detail=f"Extraction not found: {extraction_id}")

    return row


@router.get('/{extraction_id}/source')
async def get_extraction_source(request: Request, extraction_id: int):
    """
    추출의 근거가 된 원본 문서를 내려줍니다. 응답의 `source_url` 이 가리키는 곳입니다.

    브라우저에서 열리도록 inline 으로 보냅니다 — 뷰어가 `#page=` 를 읽어 해당
    페이지로 이동합니다. 원본이 이미 정리됐으면 404 입니다.
    """
    repository = request.app.state.repository

    row = await asyncio.to_thread(repository.find, extraction_id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"Extraction not found: {extraction_id}")

    path = FileReader.retained(row['task_id'])
    if path is None:
        raise HTTPException(status_code=404, detail='Source document is no longer available')

    # 타입은 저장된 파일에서 알아낸다. document_name 에는 확장자가 없어서 그걸로
    # 추론하면 text/plain 이 되고, 브라우저가 PDF 뷰어를 안 띄워 #page= 가 무시된다.
    suffix = Path(path).suffix
    return FileResponse(
        path,
        media_type=mimetypes.guess_type(path)[0] or 'application/octet-stream',
        filename=f"{row['document_name']}{suffix}",
        content_disposition_type='inline',
    )


@router.post('/{extraction_id}/approve', response_model=ReviewResult)
async def approve_extraction(request: Request, extraction_id: int, spec: ApiSpec | None = None):
    """
    추출 결과를 승인해 api_definition / api_schema 로 옮깁니다.

    본문에 규격을 실으면 저장된 것을 대체합니다 — 조회에서 받은 `spec_json` 을 고쳐
    그대로 돌려보내면 됩니다. 고친 규격도 원문과 다시 대조해 채점하므로, 응답의
    `score` 로 수정이 근거에 맞아떨어졌는지 확인할 수 있습니다.

    점수가 낮아도 승인은 막지 않습니다. 사람의 판단이 채점보다 위입니다.

    이미 승인된 건도 다시 승인할 수 있습니다 — 기존 적재를 되돌리고 수정본으로
    재적재합니다. 없는 건이면 404 입니다.
    """
    repository = request.app.state.repository

    try:
        api_def_id, score = await asyncio.to_thread(
            repository.promote, extraction_id, spec.model_dump() if spec else None,
        )
    except ExtractionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        repository.logger.error(f"Extraction approval failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')

    return ReviewResult(
        id=extraction_id, review_status=APPROVED, api_def_id=api_def_id, score=score,
    )


@router.post('/{extraction_id}/reject', response_model=ReviewResult)
async def reject_extraction(request: Request, extraction_id: int):
    """
    추출 결과를 반려합니다. 행은 남고 운영 테이블로는 옮겨지지 않습니다.

    이미 승인된 건이면 승인 취소입니다 — 운영 테이블에 올라간 것도 되돌립니다
    (다른 문서가 같은 API 를 참조하면 그쪽은 남습니다).
    """
    repository = request.app.state.repository

    try:
        await asyncio.to_thread(repository.reject, extraction_id)
    except ExtractionNotFound as e:
        raise HTTPException(status_code=404, detail=str(e))
    except Exception as e:
        repository.logger.error(f"Extraction rejection failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')

    return ReviewResult(id=extraction_id, review_status=REJECTED)
