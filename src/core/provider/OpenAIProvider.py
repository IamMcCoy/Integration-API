"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import asyncio
from typing import Any

from openai import AsyncOpenAI

from src.common.Constants import Constants
from src.common.LoggerManager import LoggerManager
from src.core.provider.BaseProvider import BaseProvider


class OpenAIProvider(BaseProvider):
    """
    OpenAI 호환 엔드포인트에 구조화 출력을 요청한다.

    동시성 제한을 여기 둔다 — 레이트 리밋은 엔드포인트의 성질이지 호출하는 쪽 사정이
    아니다. 워커가 여럿이어도 이 인스턴스를 공유하면 전역으로 묶인다.
    """

    def __init__(self, client: AsyncOpenAI | None = None, max_concurrency: int | None = None):
        self.logger = LoggerManager.get()

        # API 키는 설정이 아니라 OPENAI_API_KEY 환경변수로 온다. SDK 가 직접 읽는다.
        # base_url 이 None 이면 SDK 기본값(공식 엔드포인트)을 쓴다.
        self.client = client or AsyncOpenAI(base_url=Constants.OPENAI_BASE_URL)
        self._limit = asyncio.Semaphore(max_concurrency or Constants.OPENAI_MAX_CONCURRENCY)

        # 같은 문서를 두 번 올리면 같은 규격이 나와야 해서 temperature 를 고정한다.
        # 추론 모델은 이 인자를 받지 않으므로, 설정이 비면 아예 보내지 않는다.
        self._options: dict[str, Any] = {}
        if Constants.OPENAI_TEMPERATURE is not None:
            self._options['temperature'] = Constants.OPENAI_TEMPERATURE

    async def parse(self, model: str, instruction: str, content: str, schema: type) -> Any | None:
        async with self._limit:
            try:
                response = await self.client.chat.completions.parse(
                    model=model,
                    messages=[
                        {'role': 'system', 'content': instruction},
                        {'role': 'user', 'content': content},
                    ],
                    response_format=schema,
                    **self._options,
                )
                return response.choices[0].message.parsed

            except asyncio.CancelledError:
                raise
            except Exception as e:
                self.logger.error(f"LLM call failed ({model}, {schema.__name__}): {str(e)}")
                return None

    @property
    def name(self) -> str:
        return f"openai({self.client.base_url})"
