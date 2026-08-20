from __future__ import annotations

import asyncio
from pathlib import Path

from fastapi import APIRouter
from fastapi import File
from fastapi import Form
from fastapi import HTTPException
from fastapi import Request
from fastapi import UploadFile
from pydantic import ValidationError

from src.api.models.TaskModels import ExtractionParams
from src.api.models.TaskModels import PurgeResult
from src.api.models.TaskModels import TaskCreated
from src.common.Constants import Constants
from src.core.reader.FileReader import FileReader
from src.core.reader.FileReader import UploadTooLarge

router = APIRouter(prefix=Constants.DOCUMENT_ENDPOINT, tags=['Documents'])


@router.post('', status_code=202, response_model=TaskCreated)
async def submit_document(
        request: Request,
        file: UploadFile = File(...),
        document_name: str = Form(default=''),
        vendor_name: str = Form(default=''),
        device_name: str = Form(default=''),
        api_version: str = Form(default=''),
        chunk_size: int = Form(default=1000),
        chunk_overlap: int = Form(default=200),
):
    """
    문서를 업로드하고 API 규격 추출 작업을 접수합니다.

    처리는 큐에서 비동기로 진행됩니다. 응답의 task_id 로
    `GET /tasks/{task_id}` 를 조회해 진행 상황을 확인하세요.

    - **file**: 업로드할 문서 (pdf, txt)
    - **document_name**: 문서 식별용 이름. 비우면 업로드 파일명에서 확장자를 뗀 값
    - **vendor_name / device_name / api_version**: 적재 대상. 비우면 문서를 보고 채웁니다
    - **chunk_size**: 텍스트 청크 크기
    - **chunk_overlap**: 청크 오버랩 크기 (chunk_size 보다 작아야 함)
    """
    orchestrator = request.app.state.orchestrator

    # 본문을 다 읽기 전에 헤더로 먼저 거른다. 헤더는 클라이언트가 보내는 값이라
    # 이것만 믿을 수는 없고, 실제 상한은 FileReader 가 스트리밍 중에 강제한다.
    content_length = request.headers.get('content-length')
    if content_length and content_length.isdigit():
        if int(content_length) > Constants.UPLOAD_MAX_BYTES:
            raise HTTPException(status_code=413, detail='File too large')

    try:
        params = ExtractionParams(
            # 안 주면 파일명에서 확장자만 뗀다. 컬럼이 VARCHAR(64) 라 잘라서 넣는다.
            # 파일명마저 없으면 빈 문자열이 되고 min_length 가 400 으로 막는다 —
            # 어차피 FileReader 가 이름 없는 업로드를 거부하므로 결과가 같다.
            document_name=document_name or Path(file.filename or '').stem[:64],
            vendor_name=vendor_name,
            device_name=device_name,
            api_version=api_version,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
        )
        return await orchestrator.submit_upload(file, params, file.filename)

    except UploadTooLarge as e:
        raise HTTPException(status_code=413, detail=str(e))
    except ValidationError as e:
        raise HTTPException(status_code=400, detail=e.errors(include_url=False))
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        orchestrator.logger.error(f"Document submit failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')


@router.delete('/{task_id}/data', response_model=PurgeResult)
async def purge_document_data(request: Request, task_id: str):
    """
    한 문서(task)가 만든 데이터를 지웁니다 — 잘못 올린 문서를 되돌리는 통로입니다.

    추출 전부와, 그 추출이 만든 운영 API 중 다른 문서가 참조하지 않는 것을 지웁니다.
    보관 중인 업로드 원본도 함께 제거합니다. 같은 task_id 로 다시 불러도 안전합니다
    (지울 게 없으면 0 을 돌려줍니다). 처리 중인 작업을 멈추지는 않습니다 —
    그건 `DELETE /tasks/{task_id}` 입니다.
    """
    repository = request.app.state.repository

    try:
        extractions, definitions = await asyncio.to_thread(repository.purge_task, task_id)
    except Exception as e:
        repository.logger.error(f"Document purge failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')

    retained = FileReader.retained(task_id)
    if retained:
        Path(retained).unlink(missing_ok=True)

    return PurgeResult(
        task_id=task_id, extractions_deleted=extractions, definitions_deleted=definitions,
    )
