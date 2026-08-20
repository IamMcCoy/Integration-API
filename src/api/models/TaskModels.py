from __future__ import annotations

from enum import Enum

from pydantic import BaseModel
from pydantic import Field


class TaskStatus(str, Enum):
    PENDING = 'PENDING'
    RUNNING = 'RUNNING'
    SUCCEEDED = 'SUCCEEDED'
    FAILED = 'FAILED'
    CANCELLED = 'CANCELLED'


class TaskStage(str, Enum):
    """
    추출 파이프라인의 단계. 진행률을 퍼센트로 꾸미지 않고 단계로 알린다.

    문서 하나가 지나가는 실제 경로와 1:1 로 맞춘다.
    """

    STAGED = 'staged'            # 업로드 착지 완료, 처리 대기
    PARSING = 'parsing'          # PDF -> 마크다운
    CHUNKING = 'chunking'        # 마크다운 -> 청크
    FILTERING = 'filtering'      # API 관련 청크 선별
    EXTRACTING = 'extracting'    # 청크 -> API 규격(요청 파라미터, 타입 등)
    PERSISTING = 'persisting'    # 규격 -> MariaDB 적재
    DONE = 'done'


class ExtractionParams(BaseModel):
    """문서 업로드 시 함께 받는 파라미터."""

    #: 문서 식별용 이름. 표시와 폴백에만 쓰이고 경로에는 쓰이지 않으므로 문자 집합을
    #: 제한하지 않는다 — 한글 파일명을 그대로 받기 위해서다. 라우트가 비면 채운다.
    document_name: str = Field(min_length=1, max_length=64)

    #: 적재 대상을 가리키는 값들. 비워 두면 문서 앞부분을 보고 LLM 이 채운다.
    #: 사람이 파일을 고를 때 이미 아는 정보이므로 넣어 주는 쪽이 항상 정확하다.
    vendor_name: str = Field(default='', max_length=100)
    device_name: str = Field(default='', max_length=100)
    api_version: str = Field(default='', max_length=50)

    chunk_size: int = Field(default=1000, gt=0)
    chunk_overlap: int = Field(default=200, ge=0)

    def validated(self) -> ExtractionParams:
        """chunk_overlap 이 chunk_size 이상이면 스플리터가 무한 루프에 빠진다."""
        if self.chunk_overlap >= self.chunk_size:
            raise ValueError('chunk_overlap must be smaller than chunk_size')
        return self


class ExtractionResult(BaseModel):
    """
    성공한 작업의 산출 요약.

    아직 구현되지 않은 단계의 값은 null 이다. 0 으로 채우면
    '해봤는데 없었다' 와 '아직 안 한다' 가 구분되지 않는다.
    """

    document_name: str
    chunk_count: int
    api_chunk_count: int | None = None   # 선별 단계가 붙으면 채워진다
    spec_count: int | None = None        # 추출/적재 단계가 붙으면 채워진다


class TaskCreated(BaseModel):
    """POST 응답. 접수만 됐고 처리는 아직 시작 전이다."""

    task_id: str
    status: TaskStatus = TaskStatus.PENDING


class PurgeResult(BaseModel):
    """문서 단위 데이터 삭제 결과."""

    task_id: str
    extractions_deleted: int

    #: 이 문서에서 나왔고 다른 문서가 참조하지 않아 함께 지워진 운영 API 수.
    definitions_deleted: int


class TaskDetail(BaseModel):
    """GET 응답. 진행 상황 조회용."""

    task_id: str
    status: TaskStatus
    stage: TaskStage | None = None
    document_name: str | None = None
    filename: str | None = None

    #: total 은 청킹이 끝나야 확정된다. 그전에는 null 이다.
    processed: int = 0
    total: int | None = None

    result: ExtractionResult | None = None
    error: str | None = None

    created_at: str | None = None
    updated_at: str | None = None


if __name__ == '__main__':
    from pathlib import Path

    from pydantic import ValidationError

    def _params(name: str) -> ExtractionParams:
        return ExtractionParams(document_name=name)

    # 문자 집합 제한을 없앴다. 한글·공백·괄호가 든 파일명을 그대로 받아야 한다.
    assert _params('[SECUI] MFD REST API 가이드').document_name == '[SECUI] MFD REST API 가이드'

    # 라우트가 채워 주지 못한 경우. 빈 이름으로 적재하면 NOT NULL 에서 터지므로 여기서 막는다.
    try:
        _params('')
        raise AssertionError('Empty document_name must be rejected')
    except ValidationError:
        pass

    # 컬럼이 VARCHAR(64) 다. 라우트의 [:64] 가 빠지면 긴 파일명이 400 으로 떨어진다.
    assert _params('a' * 64).document_name == 'a' * 64
    try:
        _params('a' * 65)
        raise AssertionError('document_name longer than the column must be rejected')
    except ValidationError:
        pass

    # 라우트가 쓰는 폴백 규칙. 확장자만 떼고 상한까지 자른다.
    assert Path('[SECUI] MFD 가이드.pdf').stem[:64] == '[SECUI] MFD 가이드'
    assert Path(('x' * 70) + '.pdf').stem[:64] == 'x' * 64
    assert Path('' or '').stem[:64] == ''

    # chunk_overlap 은 스플리터를 무한 루프에 빠뜨릴 수 있어 별도로 막는다.
    try:
        ExtractionParams(document_name='doc', chunk_size=100, chunk_overlap=100).validated()
        raise AssertionError('chunk_overlap >= chunk_size must be rejected')
    except ValueError:
        pass

    print('TaskModels self-check passed')
