"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.

2026-08-06 실험 재현용. 선별(filtering) 단계를 네 축으로 잰다.

  cost      모델 x reasoning_effort 별 토큰과 지연
  accuracy  사람이 매긴 정답 라벨과 대조 (재현율 / 정밀도)
  stability 같은 입력을 3회 돌려 판정이 흔들리는지
  prompt    프롬프트 변형별 정확도 (모델은 고정)

토큰만 보고 고르면 안 된다. 선별은 규격 청크를 놓치면(false negative) 그 API 가 영영
적재되지 않고 아무도 모르는 반면, 아닌 걸 통과시키면(false positive) 추출 단계에서
근거를 못 만들어 걸러진다. 두 오류의 대가가 다르므로 재현율을 먼저 본다.

여기에 재현성이 하나 더 걸린다. 판정이 실행마다 달라지면 같은 문서를 두 번 올렸을 때
적재되는 API 집합이 달라진다.

    # 마크다운 캐시는 2026-08-06-chunk-settings.py 가 만든 것을 그대로 쓴다
    PYTHONPATH=. poetry run python scripts/2026-08-06-filter-models.py \
        /tmp/mdcache/'[SECUI] MFD REST API 가이드.md' [cost|accuracy|stability|prompt|all]

실행하면 지금 쓰는 프롬프트를 먼저 찍는다. 프롬프트는 conf/ia-conf.xml 에서 오므로
설정을 고치면 여기에도 그대로 반영된다 — 사본을 두지 않는다.

OPENAI_API_KEY 가 필요하다. .env 에 넣어 두면 읽는다.
→ docs/experiments/2026-08-06-filter-model-selection.md
"""
from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv('.env')

from openai import AsyncOpenAI                                  # noqa: E402
from src.core.chunker.MarkdownChunker import MarkdownChunker    # noqa: E402
from src.common.Constants import Constants                      # noqa: E402
from src.core.extractor.SpecExtractor import SpecExtractor      # noqa: E402
from src.core.extractor.SpecModels import ChunkVerdict          # noqa: E402

#: (모델, reasoning_effort). effort 는 gpt-5 계열만 받는다.
CANDIDATES = [
    ('gpt-4.1-nano', None),
    ('gpt-4.1-mini', None),
    ('gpt-4o-mini', None),
    ('gpt-4o', None),
    ('gpt-5-nano', None),
    ('gpt-5-nano', 'minimal'),
    ('gpt-5-mini', 'minimal'),
]

#: SECUI MFD 첫 10청크를 직접 읽고 매긴 정답.
#: 0 표지 / 1~5 목차 / 6 전역 규칙 / 7 GUI 조작 안내 / 8~9 실제 API 규격
TRUTH = [False, False, False, False, False, False, False, False, True, True]

#: prompt 모드에서 비교할 변형. 'current' 는 conf 에 들어 있는 실제 프롬프트다.
PROMPT_VARIANTS = {
    'current': Constants.FILTER_PROMPT,

    # "애매하면 참" 을 뺐을 때 놓침이 늘어나는지
    'no-lean': """\
너는 네트워크 보안 장비 벤더의 REST API 가이드 문서를 읽는다.
아래 조각이 특정 API 의 호출 규격 — 엔드포인트, 요청 파라미터, 응답 필드, 자료형 —
을 설명하고 있으면 참이다.

거짓인 것: 목차, 서문, 설치/구성 안내, 변경 이력, 저작권 고지, 용어집,
장비 사양표, 오류 코드 목록만 있는 조각.
""",

    # 거짓 목록 없이 참 조건만 줬을 때
    'positive-only': """\
너는 네트워크 보안 장비 벤더의 REST API 가이드 문서를 읽는다.
아래 조각이 특정 API 의 호출 규격 — 엔드포인트, 요청 파라미터, 응답 필드, 자료형 —
을 설명하고 있으면 참이다.

판단이 애매하면 참으로 둔다.
""",

    # 판정 근거를 구체적 신호로 못박았을 때
    'signal-based': """\
너는 네트워크 보안 장비 벤더의 REST API 가이드 문서를 읽는다.

아래 중 하나라도 있으면 참이다.
- HTTP 메서드와 경로 (GET /api/v1/... 같은 것)
- 요청 파라미터나 응답 필드의 이름·자료형 목록
- Path parameters / Query parameters / Body / Returns 항목

거짓인 것: 목차(점선과 쪽번호만 있는 줄), 표지, 서문, 변경 이력, 저작권 고지,
GUI 화면 조작 안내.

판단이 애매하면 참으로 둔다. 규격을 버리면 그 API 는 영영 적재되지 않지만,
아닌 것을 통과시키면 다음 단계에서 근거를 못 만들어 걸러진다.
""",

    # 같은 내용을 영어로. 한글은 GPT 토크나이저에서 글자당 1~2토큰이라
    # 프롬프트가 청크마다 들어가는 이 단계에서는 차이가 누적된다.
    'en': """\
You read vendor REST API guides for network security appliances.

True if the excerpt describes a specific API's call spec: endpoint,
request parameters, response fields, or data types.

False: table of contents, preface, install/setup guides, change logs,
copyright notices, glossaries, hardware spec sheets, error-code lists alone,
GUI click-through instructions.
""",

    # 영어 최소판. 거짓 목록을 줄이면 정밀도가 떨어지는지 본다.
    'en-terse': """\
True if this excerpt documents a specific REST API call: endpoint, request
parameters, response fields, or data types.

False: contents pages, prefaces, change logs, copyright, GUI instructions.
""",

    # 아래 셋은 거짓 목록을 그대로 두고 문장만 압축한 것이다.
    # en-terse 가 목록을 줄여서 손해를 봤으므로, 줄일 곳은 문장 쪽이다.

    # 역할 문장만 뺐을 때
    'en-norole': """\
True if the excerpt describes a specific API's call spec: endpoint,
request parameters, response fields, or data types.

False: table of contents, preface, install/setup guides, change logs,
copyright notices, glossaries, hardware spec sheets, error-code lists alone,
GUI click-through instructions.
""",

    # 문장을 화살표 표기로 압축
    'en-compact': """\
Vendor REST API guide excerpt.

true: endpoint, request params, response fields, or data types of a specific API
false: table of contents, preface, setup guide, changelog, copyright, glossary,
hardware spec sheet, error-code list alone, GUI click-through steps
""",

    # 키워드만
    'en-minimal': """\
true = endpoint | request params | response fields | data types
false = TOC | preface | setup | changelog | copyright | glossary |
        spec sheet | error codes alone | GUI steps
""",
}

CONCURRENCY = 8
LIMIT = asyncio.Semaphore(CONCURRENCY)


async def judge(client, model, effort, chunk, temperature=None, prompt=Constants.FILTER_PROMPT):
    async with LIMIT:
        kwargs = {
            'model': model,
            'messages': [
                {'role': 'system', 'content': prompt or Constants.FILTER_PROMPT},
                {'role': 'user', 'content': SpecExtractor._as_context(chunk)},
            ],
            'response_format': ChunkVerdict,
        }
        if effort:
            kwargs['reasoning_effort'] = effort
        if temperature is not None:
            kwargs['temperature'] = temperature

        response = await client.chat.completions.parse(**kwargs)
        parsed = response.choices[0].message.parsed
        return (parsed.is_api_spec if parsed else None), response.usage


async def cost(client, chunks) -> None:
    """모델별 토큰과 지연. gpt-5 계열은 출력에 추론 토큰이 섞인다."""
    sample = chunks[:10]
    print(f"\n[cost] 청크 {len(sample)}개")
    print(f"{'모델':14} {'effort':8} {'통과':>5} {'입력':>7} {'출력':>7} {'추론':>7} {'초':>6}")
    print('─' * 60)

    for model, effort in CANDIDATES:
        start = time.monotonic()
        try:
            results = await asyncio.gather(*(judge(client, model, effort, c) for c in sample))
        except Exception as e:
            print(f"{model:14} {str(effort):8} FAIL {type(e).__name__}: {str(e)[:60]}")
            continue

        elapsed = time.monotonic() - start
        reasoning = sum(
            getattr(getattr(u, 'completion_tokens_details', None), 'reasoning_tokens', 0) or 0
            for _, u in results
        )
        print(f"{model:14} {str(effort):8} {sum(1 for r, _ in results if r):3}/{len(sample):<2} "
              f"{sum(u.prompt_tokens for _, u in results):7,} "
              f"{sum(u.completion_tokens for _, u in results):7,} {reasoning:7,} {elapsed:6.1f}")


def score_against_truth(label: str, predictions: list[bool | None]) -> None:
    """정답과 대조해 한 줄로 찍는다. 놓침(X)이 과다 통과(+)보다 훨씬 비싸다."""
    marks, tp, fp, fn = [], 0, 0, 0
    for truth, pred in zip(TRUTH, predictions):
        if truth and pred:
            marks.append('O')
            tp += 1
        elif truth and not pred:
            marks.append('X')
            fn += 1
        elif not truth and pred:
            marks.append('+')
            fp += 1
        else:
            marks.append('.')

    recall = tp / (tp + fn) if tp + fn else 0
    precision = tp / (tp + fp) if tp + fp else 0
    print(f"{label:24} {' '.join(marks)}   재현율 {recall:.0%}  정밀도 {precision:.0%}  "
          f"놓침 {fn}  과다 {fp}")


async def accuracy(client, chunks) -> None:
    """모델별 정확도. 프롬프트는 고정."""
    sample = chunks[:len(TRUTH)]
    print(f"\n[accuracy] 청크 {len(sample)}개, 정답 참 {sum(TRUTH)}개")
    print('정답      ' + ' '.join('O' if t else '.' for t in TRUTH))
    print('─' * 62)

    for model, effort in CANDIDATES:
        try:
            got = await asyncio.gather(*(judge(client, model, effort, c) for c in sample))
        except Exception as e:
            print(f"{model:24} FAIL {type(e).__name__}")
            continue
        score_against_truth(f"{model}{'/' + effort if effort else ''}", [r for r, _ in got])

    print('\nO 맞게 통과  X 놓침(치명)  + 과다 통과(비용만)  . 맞게 제외')


async def prompt(client, chunks, model: str = 'gpt-4o-mini') -> None:
    """
    프롬프트 변형별 정확도와 토큰. 모델은 고정한다.

    프롬프트는 청크마다 들어가므로 길이가 그대로 총비용이 된다. 정확도가 같다면
    짧은 쪽이 이긴다.
    """
    sample = chunks[:len(TRUTH)]
    print(f"\n[prompt] 모델 {model} 고정, 청크 {len(sample)}개")
    print('정답      ' + ' '.join('O' if t else '.' for t in TRUTH))
    print('─' * 78)

    # 같은 청크 하나로 재면 차이가 곧 프롬프트 토큰 차이다.
    baseline = None
    for name, text in PROMPT_VARIANTS.items():
        got = await asyncio.gather(
            *(judge(client, model, None, c, prompt=text) for c in sample),
        )
        tokens = got[0][1].prompt_tokens
        if baseline is None:
            baseline = tokens
        score_against_truth(name, [r for r, _ in got])
        print(f"{'':24} 프롬프트 {len(text):4}자 → 요청 {tokens:4}토큰 "
              f"({tokens - baseline:+d} vs current)")

    print('\n--- 변형별 프롬프트 ---')
    for name, text in PROMPT_VARIANTS.items():
        print(f"\n[{name}]")
        print('\n'.join('  ' + x for x in text.strip().split('\n')))


async def stability(client, chunks, rounds: int = 3) -> None:
    """같은 입력을 여러 번. 흔들리면 같은 문서를 두 번 올렸을 때 결과가 달라진다."""
    print(f"\n[stability] 청크 {len(chunks)}개 x {rounds}회")
    print(f"{'모델':14} {'effort':8} {'temp':>5} {'통과 횟수별':>16} {'불일치':>7}")
    print('─' * 56)

    for model, effort in CANDIDATES:
        # gpt-5 계열은 temperature 를 1.0 으로 고정해 조절할 수 없다.
        temps = (None,) if model.startswith('gpt-5') else (None, 0)
        for temperature in temps:
            try:
                runs = [
                    [
                        r for r, _ in await asyncio.gather(
                            *(judge(client, model, effort, c, temperature) for c in chunks),
                        )
                    ]
                    for _ in range(rounds)
                ]
            except Exception as e:
                print(f"{model:14} {str(effort):8} {str(temperature):>5} FAIL {type(e).__name__}")
                continue

            counts = ' '.join(f"{sum(r):3}" for r in runs)
            diff = sum(1 for i in range(len(chunks)) if len({r[i] for r in runs}) > 1)
            print(f"{model:14} {str(effort):8} {str(temperature):>5} {counts:>16} {diff:7}")


async def main() -> None:
    md = Path(sys.argv[1]).read_text(encoding='utf-8')
    mode = sys.argv[2] if len(sys.argv) > 2 else 'all'

    chunks = MarkdownChunker.chunk(md, 1000, 200)
    print(f"문서 {Path(sys.argv[1]).name} — 청크 {len(chunks)}개")

    # 지금 쓰는 프롬프트를 먼저 보여준다. 판정이 이상하면 여기부터 의심한다.
    print(f"\n=== FILTER_PROMPT ({len(Constants.FILTER_PROMPT)}자) ===")
    print('\n'.join('  ' + x for x in Constants.FILTER_PROMPT.strip().split('\n')))
    print('\n=== 청크 하나가 실제로 넘어가는 모양 ===')
    print('\n'.join('  ' + x for x in SpecExtractor._as_context(chunks[8]).split('\n')[:8]))
    print('  ...')

    client = AsyncOpenAI()
    if mode in ('cost', 'all'):
        await cost(client, chunks)
    if mode in ('accuracy', 'all'):
        await accuracy(client, chunks)
    if mode in ('stability', 'all'):
        await stability(client, chunks)
    if mode in ('prompt', 'all'):
        await prompt(client, chunks)


if __name__ == '__main__':
    asyncio.run(main())
