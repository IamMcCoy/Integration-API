"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

from abc import ABC
from abc import abstractmethod
from typing import Any


class BaseProvider(ABC):
    """
    LLM 호출 계약. 이 파이프라인이 LLM 에 요구하는 건 하나뿐이다 —
    스키마를 주면 그 스키마대로 채워서 돌려주는 것.

    자유 형식 응답도, 스트리밍도, 토큰 계산도 쓰지 않는다. 선별은 참/거짓,
    추출은 규격 객체를 받으므로 전부 구조화 출력이다.

    구현체가 당분간 OpenAIProvider 하나다. 계약을 따로 둔 이유는 사내 vLLM 으로
    갈아탈 여지 때문이다 — 그때 이 파일을 상속한 파일 하나만 더하면 된다.
    """

    @abstractmethod
    async def parse(self, model: str, instruction: str, content: str, schema: type) -> Any | None:
        """
        구조화된 응답 하나를 받는다. 실패하면 None.

        예외를 던지지 않는 이유: 청크 수백 개 중 하나가 레이트 리밋에 걸렸다고
        문서 전체를 실패로 만들 이유가 없다. 실패는 로그로 남기고 호출자는 계속 간다.

        - `model`: 단계마다 다른 모델을 쓴다 (선별은 싼 것, 추출은 정확한 것)
        - `schema`: Pydantic 모델. JSON Schema 로 변환돼 모델이 그 밖을 못 낸다
        """
        raise NotImplementedError

    @property
    @abstractmethod
    def name(self) -> str:
        """로그에 찍을 이름. 어느 엔드포인트로 나갔는지 알아야 장애를 짚는다."""
        raise NotImplementedError
