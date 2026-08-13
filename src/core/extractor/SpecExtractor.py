"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import asyncio

from src.common.Constants import Constants
from src.common.LoggerManager import LoggerManager
from src.core.extractor.SpecModels import ChunkVerdict
from src.core.extractor.SpecModels import DocumentIdentity
from src.core.extractor.SpecModels import ExtractedSpecs
from src.core.provider.BaseProvider import BaseProvider
from src.core.provider.OpenAIProvider import OpenAIProvider


class SpecExtractor:
    """
    청크를 LLM 에 넘겨 API 규격 청크를 선별하고(select), 규격을 뽑는다(extract).

    무엇을 물을지(프롬프트)와 어떻게 부를지(엔드포인트·동시성·재시도)를 나눈다.
    뒤쪽은 Provider 가 안다.
    """

    def __init__(self, provider: BaseProvider | None = None):
        self.logger = LoggerManager.get()
        self.provider = provider or OpenAIProvider()

        # 단계마다 모델이 다르다. 선별은 청크 수만큼 부르고 판정이 참/거짓 하나뿐이라
        # 싼 모델로 충분하고, 추출은 근거를 원문 그대로 옮겨야 해서 정확도가 곧 점수다.
        self.filter_model = Constants.OPENAI_FILTER_MODEL
        self.extract_model = Constants.OPENAI_EXTRACT_MODEL

    async def select(self, chunks: list[dict]) -> list[dict]:
        """API 규격을 설명하는 청크만 남긴다."""
        verdicts = await asyncio.gather(*(self._is_api_spec(c) for c in chunks))
        selected = [chunk for chunk, keep in zip(chunks, verdicts) if keep]

        self.logger.info(f"Filtered chunks: {len(selected)}/{len(chunks)} kept")
        return selected

    async def extract(self, chunks: list[dict]) -> list[tuple[dict, dict]]:
        """
        청크마다 규격을 뽑아 (청크, 규격 dict) 쌍으로 돌려준다.

        청크 하나에서 API 가 여러 개 나올 수 있어 쌍의 개수는 청크 수보다 많을 수 있다.
        """
        results = await asyncio.gather(*(self._extract_one(c) for c in chunks))

        pairs = [(chunk, spec) for chunk, specs in zip(chunks, results) for spec in specs]
        self.logger.info(f"Extracted specs: {len(pairs)} from {len(chunks)} chunks")
        return pairs

    async def identify(self, chunks: list[dict], head: int = 5) -> DocumentIdentity:
        """
        문서 앞부분에서 벤더/장비/버전을 읽어낸다. 폼 입력이 비었을 때만 부른다.

        표지와 개요가 앞쪽에 있으므로 문서 전체를 볼 이유가 없다.
        """
        context = '\n\n---\n\n'.join(self._as_context(c) for c in chunks[:head])

        # 문서당 한 번뿐이고 마스터 테이블에 들어갈 값이라 추출용 모델을 쓴다.
        identity = await self.provider.parse(
            self.extract_model, Constants.IDENTIFY_PROMPT, context, DocumentIdentity,
        )

        return identity or DocumentIdentity(vendor_name='', device_name='', api_version='')

    async def _is_api_spec(self, chunk: dict) -> bool:
        # 판별 근거는 받지 않는다. 오분류가 잦으면 reason 필드를 붙여 로그로 본다.
        verdict = await self.provider.parse(
            self.filter_model, Constants.FILTER_PROMPT, self._as_context(chunk), ChunkVerdict,
        )
        return bool(verdict and verdict.is_api_spec)

    async def _extract_one(self, chunk: dict) -> list[dict]:
        extracted = await self.provider.parse(
            self.extract_model, Constants.EXTRACT_PROMPT, self._as_context(chunk), ExtractedSpecs,
        )
        return [spec.model_dump() for spec in extracted.specs] if extracted else []

    @staticmethod
    def _as_context(chunk: dict) -> str:
        """헤더 경로를 본문 앞에 붙인다. 표만 담긴 청크는 헤더가 있어야 어느 API 인지 안다."""
        path = ' > '.join(chunk.get('metadata', {}).get('headings') or [])

        return f"[문서 위치] {path}\n\n{chunk['text']}" if path else chunk['text']


if __name__ == '__main__':
    from src.core.extractor.SpecModels import ApiSpec
    from src.core.extractor.SpecModels import Parameter

    class _StubProvider(BaseProvider):
        """네트워크 없이 배선만 검사한다. 무엇을 물었는지 기록해 둔다."""

        def __init__(self, answers: dict):
            self.answers = answers
            self.asked: list[tuple[str, str]] = []

        async def parse(self, model, instruction, content, schema):
            self.asked.append((model, content))
            return self.answers.get(schema.__name__)

        @property
        def name(self) -> str:
            return 'stub'

    def _chunk(chunk_id, headings, text):
        return {
            'chunk_id': chunk_id, 'text': text,
            'metadata': {'headings': headings, 'page_number': 1},
        }

    chunks = [
        _chunk(0, ['목 차'], '1. REST API 설정 ..... 3'),
        _chunk(1, ['4. System', '4.1.1 시스템 정보 조회'], 'GET /mfd/api/sytem/info'),
    ]

    # 헤더 경로가 본문 앞에 붙어야 LLM 이 어느 API 인지 안다.
    context = SpecExtractor._as_context(chunks[1])
    assert context.startswith('[문서 위치] 4. System > 4.1.1 시스템 정보 조회'), context[:60]
    assert SpecExtractor._as_context(_chunk(0, [], 'body')) == 'body', 'No headings must add no prefix'

    parameter = Parameter(
        name='deviceId', type='string', raw_type='varchar(64)',
        required=True, description='식별자', evidence='| deviceId |',
    )
    spec = ApiSpec(
        title='시스템 정보 조회', method='GET', endpoint='/mfd/api/sytem/info',
        request_parameters=[parameter], response_parameters=[],
    )

    stub = _StubProvider({
        'ChunkVerdict': ChunkVerdict(is_api_spec=True),
        'ExtractedSpecs': ExtractedSpecs(specs=[spec]),
    })
    extractor = SpecExtractor(provider=stub)

    selected = asyncio.run(extractor.select(chunks))
    assert len(selected) == 2, f"Stub says true for both: {len(selected)}"
    assert all(model == extractor.filter_model for model, _ in stub.asked), \
        'Selection must use the filter model'

    stub.asked.clear()
    pairs = asyncio.run(extractor.extract(selected))
    assert len(pairs) == 2, f"One spec per chunk: {len(pairs)}"
    assert pairs[0][1]['title'] == '시스템 정보 조회', 'Spec must come back as a dict'
    assert pairs[0][1]['request_parameters'][0]['type'] == 'string', 'Parameter must survive model_dump'
    assert all(model == extractor.extract_model for model, _ in stub.asked), \
        'Extraction must use the extract model'

    # 선별이 거짓이면 아무것도 안 남는다.
    empty = SpecExtractor(provider=_StubProvider({'ChunkVerdict': ChunkVerdict(is_api_spec=False)}))
    assert asyncio.run(empty.select(chunks)) == [], 'Rejected chunks must not pass'

    # 호출이 실패해 None 이 와도 태스크는 계속 간다.
    broken = SpecExtractor(provider=_StubProvider({}))
    assert asyncio.run(broken.select(chunks)) == [], 'None verdict must drop the chunk'
    assert asyncio.run(broken.extract(chunks)) == [], 'None extraction must yield no pairs'

    identity = asyncio.run(broken.identify(chunks))
    assert identity.vendor_name == '', 'Failed identify must fall back to empty strings'

    print(f"OK: select/extract wiring, {len(pairs)} pairs, model routing")
