"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import re

WHITESPACE_PATTERN = re.compile(r'\s+')

#: 'http(s)://[serverip]' 처럼 문서마다 다른 호스트 표기. 경로만 남기고 떼어낸다.
ENDPOINT_HOST_PATTERN = re.compile(r'^https?(\(s\))?://[^/]*', re.I)
ENDPOINT_BRACKET_PATTERN = re.compile(r'^\[[^\]]*\]')

#: 모델이 값 없음을 문자열로 뱉는 경우. 그대로 두면 endpoint 컬럼에 'None' 이 저장된다.
PLACEHOLDER_VALUES = frozenset({'none', 'null', 'n/a', 'na', '-', ''})

APPROVED = 'approved'
PENDING = 'pending'
REJECTED = 'rejected'


class SpecScorer:
    """
    추출된 규격이 원문에 실제로 근거를 두는지 대조해 0~100 점을 매긴다.

    LLM 을 다시 부르지 않는다. 파라미터마다 딸려 온 evidence 가 원문 청크 안에
    실재하는지만 본다 — 환각한 파라미터는 근거를 만들어낼 수 없으므로 자동으로 깎인다.
    같은 입력에 항상 같은 점수가 나오고 비용이 들지 않는다.
    """

    @staticmethod
    def score(spec: dict, source_text: str) -> int:
        source = SpecScorer._normalize(source_text)

        # 근거 있는 엔드포인트가 없으면 그 규격은 API 로 성립하지 않는다. 원문에 없는
        # 경로는 지어낸 것이고, 아예 없는 경로는 무엇을 호출하는지 모른다 — 공통 응답
        # 포맷·부록 표 같은 조각이 파라미터 근거만으로 만점을 받아 적재되는 통로였다.
        # 파라미터 오류는 인자 하나가 틀리는 것이지만 엔드포인트가 없으면 호출 자체가
        # 불가능하다.
        path = SpecScorer.endpoint_path(spec.get('endpoint'))
        if not path or SpecScorer._normalize(path) not in source:
            return 0

        parameters = list(spec.get('request_parameters') or []) + list(spec.get('response_parameters') or [])

        # 파라미터가 없어도 엔드포인트에 근거가 있으면 그 API 는 실재한다.
        # 'PUT .../apply' 처럼 인자 없는 액션 API 가 문서에 실제로 있고, 문서도
        # 'Path parameters None / Query parameters None / Body None' 이라고 적는다.
        if not parameters:
            return 100

        grounded = sum(1 for p in parameters if SpecScorer._is_grounded(p, source))

        return round(grounded / len(parameters) * 100)

    @staticmethod
    def endpoint_path(endpoint: str | None) -> str | None:
        """
        엔드포인트에서 경로만 남긴다. 없는 값이면 None.

        문서는 'http(s)://[serverip]/mfd/api/token' 처럼 쓰는데 모델은 경로만 내기도 하고
        호스트를 붙이기도 한다. 원문과 대조하려면 같은 모양으로 맞춰야 한다.
        """
        if not endpoint:
            return None

        value = endpoint.strip()
        if value.lower() in PLACEHOLDER_VALUES:
            return None

        value = ENDPOINT_HOST_PATTERN.sub('', value)
        value = ENDPOINT_BRACKET_PATTERN.sub('', value)

        return value.rstrip('/ .,') or None

    @staticmethod
    def classify(score: int, auto_threshold: int, review_threshold: int) -> str:
        if score >= auto_threshold:
            return APPROVED
        if score >= review_threshold:
            return PENDING
        return REJECTED

    @staticmethod
    def _is_grounded(parameter: dict, source: str) -> bool:
        evidence = SpecScorer._normalize(parameter.get('evidence') or '')
        if not evidence:
            return False

        # 이름만 그대로 옮겨 적은 근거는 인정하지 않는다. 이름은 환각한 파라미터라도
        # 원문 어딘가에 우연히 걸리기 쉬워서 점수를 부풀린다.
        if evidence == SpecScorer._normalize(parameter.get('name') or ''):
            return False

        return evidence in source

    @staticmethod
    def _normalize(text: str) -> str:
        return WHITESPACE_PATTERN.sub(' ', text).strip().lower()


if __name__ == '__main__':
    source = (
        '## 장비 목록 조회\n\n'
        'GET http(s)://[serverip]/api/devices\n\n'
        '| 이름 | 타입 | 필수 | 설명 |\n'
        '| --- | --- | --- | --- |\n'
        '| deviceId | string | Y | 장비 식별자 |\n'
        '| limit | integer | N | 조회 개수 |\n'
    )

    def _param(name: str, evidence: str) -> dict:
        return {'name': name, 'type': 'string', 'required': True, 'description': '', 'evidence': evidence}

    perfect = {
        'endpoint': '/api/devices',
        'request_parameters': [
            _param('deviceId', '| deviceId | string | Y | 장비 식별자 |'),
            _param('limit', '| limit | integer | N | 조회 개수 |'),
        ],
        'response_parameters': [],
    }
    assert SpecScorer.score(perfect, source) == 100, 'Grounded spec must score 100'

    # 엔드포인트가 아예 없으면 파라미터 근거가 전부 맞아도 0 점이다. 공통 응답 포맷이나
    # 부록 표에서 뽑힌 조각은 무엇을 호출하는지 알 수 없다.
    endpointless = {k: v for k, v in perfect.items() if k != 'endpoint'}
    assert SpecScorer.score(endpointless, source) == 0, 'Spec without an endpoint must score 0'

    hallucinated = {
        'endpoint': '/api/devices',
        'request_parameters': [
            _param('deviceId', '| deviceId | string | Y | 장비 식별자 |'),
            _param('offset', '| offset | integer | N | 시작 위치 |'),
        ],
        'response_parameters': [],
    }
    assert SpecScorer.score(hallucinated, source) == 50, 'Hallucinated parameter must be discounted'

    # 공백 표기가 달라도 같은 줄이면 인정한다.
    spaced = {
        'endpoint': '/api/devices',
        'request_parameters': [_param('deviceId', '|  deviceId  |  string  |  Y  |  장비 식별자  |')],
        'response_parameters': [],
    }
    assert SpecScorer.score(spaced, source) == 100, 'Whitespace differences must not fail matching'

    # 이름만 옮긴 근거는 인정하지 않는다.
    name_only = {
        'endpoint': '/api/devices',
        'request_parameters': [_param('deviceId', 'deviceId')],
        'response_parameters': [],
    }
    assert SpecScorer.score(name_only, source) == 0, 'Name-only evidence must not count'

    empty: dict[str, list] = {'request_parameters': [], 'response_parameters': []}
    assert SpecScorer.score(empty, source) == 0, 'Spec with no evidence at all must score 0'

    # 인자 없는 액션 API. 문서가 'Path parameters None' 이라고 적는 종류다.
    action_source = 'PUT http(s)://[serverip]/mfd/api/policy/user-black-list/apply\nBody None\n'
    action = dict(empty, method='PUT', endpoint='/mfd/api/policy/user-black-list/apply')
    assert SpecScorer.score(action, action_source) == 100, \
        'Parameterless API with a grounded endpoint must pass'
    assert SpecScorer.score(dict(empty, endpoint='/invented/path'), action_source) == 0, \
        'Parameterless API with an invented endpoint must score 0'

    # 엔드포인트 검증 — 원문에 없는 경로는 지어낸 것이다.
    with_endpoint = dict(perfect, endpoint='/mfd/api/system/4/acls')
    grounded_source = f"{source}\nGET http(s)://[serverip]/mfd/api/system/4/acls\n"
    assert SpecScorer.score(with_endpoint, grounded_source) == 100, 'Grounded endpoint must pass'
    assert SpecScorer.score(with_endpoint, source) == 0, 'Hallucinated endpoint must score 0'

    hallucinated_path = dict(perfect, endpoint='/acl/view')
    assert SpecScorer.score(hallucinated_path, grounded_source) == 0, 'Invented path must score 0'

    # 호스트 표기가 달라도 경로가 맞으면 인정한다.
    with_host = dict(perfect, endpoint='http(s)://[serverip]/mfd/api/system/4/acls')
    assert SpecScorer.score(with_host, grounded_source) == 100, 'Host prefix must be stripped'

    # 모델이 값 없음을 문자열로 뱉는 경우. 없는 것과 똑같이 취급한다.
    for placeholder in ('None', 'null', 'N/A', '-', '  '):
        assert SpecScorer.endpoint_path(placeholder) is None, f"Placeholder leaked: {placeholder!r}"
        assert SpecScorer.score(dict(perfect, endpoint=placeholder), source) == 0, \
            f"Placeholder endpoint must reject the spec: {placeholder!r}"

    assert SpecScorer.endpoint_path(None) is None, 'None endpoint must stay None'
    assert SpecScorer.endpoint_path('/api/v1/devices/') == '/api/v1/devices', 'Trailing slash must go'

    assert SpecScorer.classify(95, 90, 70) == APPROVED, 'High score must auto-approve'
    assert SpecScorer.classify(90, 90, 70) == APPROVED, 'Threshold is inclusive'
    assert SpecScorer.classify(75, 90, 70) == PENDING, 'Mid score must wait for review'
    assert SpecScorer.classify(69, 90, 70) == REJECTED, 'Low score must be rejected'

    print('OK: scoring and classification')
