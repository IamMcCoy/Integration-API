"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.

2026-08-07 실험 재현용. 프롬프트 변형을 정확도로 비교한다.

토큰만 보고 고르면 안 된다. 프롬프트 품질은 얼마나 온전히 뽑아내는가로 재야 하므로,
지표는 최종 적중 API 수와 적재 파라미터 수다.

    PYTHONPATH=. poetry run python scripts/2026-08-07-prompt-variants.py \
        '/tmp/mdcache/[SECUI] MFD REST API 가이드.md' [extract|filter] [반복횟수]

  extract  선별 결과를 고정하고 추출 프롬프트만 갈아 끼운다
  filter   선별 프롬프트를 바꿔 파이프라인 끝까지 돌린다 — 선별 정답 라벨이
           10개뿐이라 최종 지표로 재는 편이 정확하다

'current' 변형은 conf/ia-conf.xml 에서 읽으므로 설정을 고치면 그 값으로 비교된다.
OPENAI_API_KEY 가 필요하다.
→ docs/experiments/2026-08-07-prompt-language-and-rationale.md
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv('.env')

from openai import AsyncOpenAI                                  # noqa: E402
from src.common.Constants import Constants                      # noqa: E402
from src.core.chunker.MarkdownChunker import MarkdownChunker    # noqa: E402
from src.core.extractor.SpecExtractor import SpecExtractor      # noqa: E402
from src.core.extractor.SpecModels import ChunkVerdict          # noqa: E402
from src.core.extractor.SpecModels import ExtractedSpecs        # noqa: E402
from src.core.extractor.SpecRepository import SpecRepository    # noqa: E402
from src.core.extractor.SpecScorer import SpecScorer            # noqa: E402

#: 문서에 적힌 메서드+경로가 정답이다. API 마다 고유하므로 사람 라벨링이 필요 없다.
API_CALL = re.compile(r'\b(GET|POST|PUT|PATCH|DELETE)\s+(http\(s\)://\[[^\]]+\]\S+)')

CONCURRENCY = 8

# 채택된 프롬프트에서 이유 설명과 예시만 걷어낸 것. 언어가 아니라 이유가 변수임을
# 보이는 대조군이라 남겨 둔다 — 이쪽으로 줄이면 적중이 23에서 18로 떨어진다.
EXTRACT_NO_RATIONALE = """\
You extract REST API call specs from Korean vendor guides.

- Extract only what the excerpt states.
- `evidence`: copy the source line verbatim. For a table row, copy the entire row.
- Omit any parameter you cannot back with evidence.
- `raw_type`: the document's own notation. `type`: its JSON primitive.
- Flatten nested response fields into paths like data.items[].deviceId.
- A parameterless API is still a spec: leave the arrays empty, fill endpoint and method.
- `endpoint`: copy the path exactly. No path in the excerpt -> null.
- No API spec in the excerpt -> empty specs array.
"""

# 선별 프롬프트에 같은 처방(대상 언어 명시 + 이유 보강)을 한 것. 이쪽은 이득이 없었다.
FILTER_WITH_RATIONALE = """\
You read Korean vendor REST API guides for network security appliances.

True if the excerpt documents a specific API call: endpoint, request parameters,
response fields, or data types.

False: table of contents (lines of dots ending in a page number), preface,
install/setup guides, change logs, copyright notices, glossaries, hardware spec
sheets, error-code lists alone, GUI click-through instructions.

A missed spec is lost for good — nothing downstream recovers it. A false pass is
cheap: the next stage cannot ground it in the source and discards it. When
genuinely torn, pass it.
"""

EXTRACT_VARIANTS = {
    'current': Constants.EXTRACT_PROMPT,
    'no-rationale': EXTRACT_NO_RATIONALE.strip(),
}

FILTER_VARIANTS = {
    'current': Constants.FILTER_PROMPT,
    'with-rationale': FILTER_WITH_RATIONALE.strip(),
}


CLIENT = AsyncOpenAI()
LIMIT = asyncio.Semaphore(CONCURRENCY)


async def ask(chunk, prompt, schema, model):
    async with LIMIT:
        response = await CLIENT.chat.completions.parse(
            model=model,
            messages=[
                {'role': 'system', 'content': prompt},
                {'role': 'user', 'content': SpecExtractor._as_context(chunk)},
            ],
            response_format=schema,
            temperature=0,
        )
        return response.choices[0].message.parsed, response.usage


def evaluate(pairs, truth) -> tuple[int, int]:
    """자동 적재될 것만 세어 (적중 API 수, 파라미터 수) 를 돌려준다."""
    endpoints, params = set(), 0
    for chunks, spec in SpecRepository._merge_by_api(pairs):
        source = '\n\n'.join(c['text'] for c in chunks)
        if SpecScorer.score(spec, source) < Constants.SCORE_AUTO_THRESHOLD:
            continue
        params += len(spec.get('request_parameters') or []) + len(spec.get('response_parameters') or [])
        path = SpecScorer.endpoint_path(spec.get('endpoint'))
        if path:
            endpoints.add((spec.get('method'), path))
    return len(endpoints & truth), params


async def extract_mode(chunks, truth, rounds: int) -> None:
    """선별을 한 번만 돌려 고정하고, 추출 프롬프트만 바꿔 비교한다."""
    verdicts = await asyncio.gather(
        *(
            ask(c, Constants.FILTER_PROMPT, ChunkVerdict, Constants.OPENAI_FILTER_MODEL)
            for c in chunks
        ),
    )
    selected = [c for c, (v, _) in zip(chunks, verdicts) if v and v.is_api_spec]
    print(f"\n[extract] 선별 {len(selected)}개 고정, 정답 {len(truth)}개")
    print(f"{'변형':16} {'회차':>4} {'적중':>7} {'파라미터':>9} {'입력토큰':>10}")
    print('─' * 52)

    for name, prompt in EXTRACT_VARIANTS.items():
        for attempt in range(1, rounds + 1):
            results = await asyncio.gather(
                *(ask(c, prompt, ExtractedSpecs, Constants.OPENAI_EXTRACT_MODEL) for c in selected),
            )
            pairs = [
                (c, s.model_dump()) for c, (p, _) in zip(selected, results)
                for s in (p.specs if p else [])
            ]
            hit, params = evaluate(pairs, truth)
            tokens = sum(u.prompt_tokens for _, u in results)
            print(f"{name:16} {attempt:4} {hit:3}/{len(truth):<3} {params:9} {tokens:10,}")


async def filter_mode(chunks, truth, rounds: int) -> None:
    """선별 프롬프트를 바꿔 파이프라인 끝까지 돌린다."""
    print(f"\n[filter] 청크 {len(chunks)}개, 정답 {len(truth)}개")
    print(f"{'변형':16} {'회차':>4} {'선별':>5} {'적중':>7} {'파라미터':>9} {'선별토큰':>10}")
    print('─' * 58)

    for name, prompt in FILTER_VARIANTS.items():
        for attempt in range(1, rounds + 1):
            verdicts = await asyncio.gather(
                *(ask(c, prompt, ChunkVerdict, Constants.OPENAI_FILTER_MODEL) for c in chunks),
            )
            selected = [c for c, (v, _) in zip(chunks, verdicts) if v and v.is_api_spec]
            filter_tokens = sum(u.prompt_tokens for _, u in verdicts)

            results = await asyncio.gather(
                *(
                    ask(c, Constants.EXTRACT_PROMPT, ExtractedSpecs, Constants.OPENAI_EXTRACT_MODEL)
                    for c in selected
                ),
            )
            pairs = [
                (c, s.model_dump()) for c, (p, _) in zip(selected, results)
                for s in (p.specs if p else [])
            ]
            hit, params = evaluate(pairs, truth)
            print(f"{name:16} {attempt:4} {len(selected):5} {hit:3}/{len(truth):<3} "
                  f"{params:9} {filter_tokens:10,}")


async def main() -> None:
    path = Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else 'extract'
    rounds = int(sys.argv[3]) if len(sys.argv) > 3 else 2

    markdown = path.read_text(encoding='utf-8')
    chunks = MarkdownChunker.chunk(markdown, 1000, 200)
    truth = {
        (m.upper(), re.sub(r'^http\(s\)://\[[^\]]+\]', '', p))
        for m, p in API_CALL.findall(markdown)
    }

    print(f"문서 {path.name} — 청크 {len(chunks)}개")
    print(f"현재 filter_prompt  {len(Constants.FILTER_PROMPT)}자")
    print(f"현재 extract_prompt {len(Constants.EXTRACT_PROMPT)}자")

    if mode == 'extract':
        await extract_mode(chunks, truth, rounds)
    elif mode == 'filter':
        await filter_mode(chunks, truth, rounds)
    else:
        raise SystemExit(f"unknown mode: {mode}")


if __name__ == '__main__':
    asyncio.run(main())
