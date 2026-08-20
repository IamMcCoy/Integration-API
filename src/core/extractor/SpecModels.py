from __future__ import annotations

from typing import Literal

from pydantic import BaseModel
from pydantic import Field

#: JSON Schema 기본형. Literal 이라 구조화 출력에서 enum 으로 내려가고,
#: 모델이 이 목록 밖의 값을 낼 수 없다. 문서마다 다른 표기(문자열/str/varchar)를
#: 후처리 매핑표로 흡수하려 들면 문서가 늘 때마다 표도 늘어난다.
ParameterType = Literal['string', 'integer', 'number', 'boolean', 'array', 'object']


class Parameter(BaseModel):
    """API 요청/응답 파라미터 하나."""

    name: str = Field(
        description='파라미터 이름. 중첩 필드는 data.items[].deviceId 처럼 경로로 편다',
    )
    type: ParameterType = Field(description='문서의 자료형을 JSON 기본형으로 옮긴 것')

    #: 길이 제한이나 날짜 형식처럼 기본형으로 뭉개면 사라지는 정보를 남긴다.
    raw_type: str = Field(description='문서에 적힌 자료형 표기 그대로')

    required: bool = Field(description='필수 여부. 문서에 명시가 없으면 false')
    description: str = Field(description='설명')

    #: 점수 산출의 전제. 원문에 이 문자열이 없으면 환각으로 보고 점수를 깎는다.
    evidence: str = Field(description='이 파라미터가 등장하는 원문 줄을 글자 그대로 옮긴 것')


class ApiSpec(BaseModel):
    """청크 하나에서 뽑아낸 API 규격."""

    title: str = Field(description='API 이름')
    method: str | None = Field(description='HTTP 메서드. 문서에 없으면 null')
    endpoint: str | None = Field(description='엔드포인트 경로. 문서에 없으면 null')
    request_parameters: list[Parameter]
    response_parameters: list[Parameter]


class ExtractedSpecs(BaseModel):
    """
    청크 하나의 추출 결과.

    한 청크에 API 가 여러 개 실려 있을 수 있고, 하나도 없을 수도 있다.
    """

    specs: list[ApiSpec]


class ChunkVerdict(BaseModel):
    """청크가 API 규격을 설명하는지에 대한 판정."""

    is_api_spec: bool


class DocumentIdentity(BaseModel):
    """
    문서 앞부분에서 읽어낸 적재 대상.

    업로드 폼에 값이 없을 때만 쓴다. 표기 요동(SECUI/시큐아이, MF2/MFD)으로
    같은 벤더가 둘로 갈릴 수 있어 폼 입력이 있으면 그쪽이 항상 이긴다.
    """

    vendor_name: str = Field(description='제조사 이름. 알 수 없으면 빈 문자열')
    device_name: str = Field(description='장비/제품 이름. 알 수 없으면 빈 문자열')
    api_version: str = Field(description='API 또는 문서 버전. 알 수 없으면 빈 문자열')
