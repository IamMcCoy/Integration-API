"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.

선별을 LLM 없이 룰로 할 수 있는지 잰다.

성공하면 청크마다 부르던 LLM 호출이 0 이 되고 판정이 완전히 결정적이 된다.
실패해도 하이브리드가 남는다 — 룰이 확신하는 것만 처리하고 나머지만 LLM 에 넘긴다.

    PYTHONPATH=. poetry run python scripts/2026-08-06-rule-based-filter.py \
        /tmp/mdcache [llm|sweep]

  llm    SECUI MFD 39청크에서 룰 판정과 LLM 판정을 대조 (OPENAI_API_KEY 필요)
  sweep  문서 18개 전체에 룰만 적용해 통과율 분포 (API 호출 없음)

→ docs/experiments/2026-08-06-filter-model-selection.md
"""
from __future__ import annotations

import asyncio
import re
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv('.env')

from src.core.chunker.MarkdownChunker import MarkdownChunker    # noqa: E402

#: HTTP 메서드 뒤에 경로나 URL 이 오는 형태. 'POST http(s)://[serverip]/mfd/api/token'
HTTP_CALL = re.compile(r'\b(GET|POST|PUT|PATCH|DELETE|HEAD|OPTIONS)\b\s+\(?(https?|/)', re.I)

#: 규격 문서의 관용 소제목. 벤더가 달라도 이 낱말은 거의 그대로 쓴다.
SPEC_SECTION = re.compile(
    r'(path|query)\s*parameter|request\s*body|^\s*#{1,6}\s*(url|returns|body|response|request)\b',
    re.I | re.M,
)

#: 타입/필수 컬럼이 있는 표 = 파라미터 정의표
PARAM_TABLE = re.compile(r'\|[^|\n]*\b(타입|type|필수|required|자료형|데이터\s*타입)\b[^|\n]*\|', re.I)

#: 목차 줄. 점선 뒤에 쪽번호.
TOC_LINE = re.compile(r'\.{4,}\s*\d+\s*$', re.M)


def rule_verdict(chunk: dict) -> bool:
    """룰만으로 API 규격 청크인지 판정한다."""
    text = chunk['text']
    lines = [x for x in text.split('\n') if x.strip()]

    # 목차가 절반을 넘으면 무조건 거짓. 목차 안에도 API 이름이 있어 신호가 섞인다.
    if lines and sum(1 for x in lines if TOC_LINE.search(x)) / len(lines) >= 0.5:
        return False

    haystack = f"{' > '.join(chunk['metadata'].get('headings') or [])}\n{text}"
    return bool(
        HTTP_CALL.search(haystack)
        or SPEC_SECTION.search(haystack)
        or PARAM_TABLE.search(text),
    )


#: SECUI MFD 첫 10청크 정답 (선별 실험과 동일)
TRUTH = [False, False, False, False, False, False, False, False, True, True]


def score(label: str, preds: list[bool], truth: list[bool]) -> None:
    marks, tp, fp, fn = [], 0, 0, 0
    for t, p in zip(truth, preds):
        if t and p:
            marks.append('O')
            tp += 1
        elif t and not p:
            marks.append('X')
            fn += 1
        elif not t and p:
            marks.append('+')
            fp += 1
        else:
            marks.append('.')
    recall = tp / (tp + fn) if tp + fn else 0
    precision = tp / (tp + fp) if tp + fp else 0
    print(f"{label:14} {' '.join(marks)}   재현율 {recall:.0%}  정밀도 {precision:.0%}  "
          f"놓침 {fn}  과다 {fp}")


async def compare_with_llm(cache: Path) -> None:
    """룰 판정과 LLM 판정을 같은 청크에 대고 비교한다."""
    import importlib.util

    from openai import AsyncOpenAI

    spec = importlib.util.spec_from_file_location(
        'fm', str(Path(__file__).with_name('2026-08-06-filter-models.py')),
    )
    fm = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fm)

    md = (cache / '[SECUI] MFD REST API 가이드.md').read_text(encoding='utf-8')
    chunks = MarkdownChunker.chunk(md, 1000, 200)
    client = AsyncOpenAI()

    llm = [
        r for r, _ in await asyncio.gather(
            *(fm.judge(client, 'gpt-4o-mini', None, c) for c in chunks),
        )
    ]
    rule = [rule_verdict(c) for c in chunks]

    print(f"\n[llm] SECUI MFD {len(chunks)}청크")
    print('정답(첫10)     ' + ' '.join('O' if t else '.' for t in TRUTH))
    print('─' * 62)
    score('gpt-4o-mini', llm[:len(TRUTH)], TRUTH)
    score('rule', rule[:len(TRUTH)], TRUTH)

    agree = sum(1 for a, b in zip(llm, rule) if a == b)
    print(f"\n전체 {len(chunks)}청크: LLM 통과 {sum(llm)}, 룰 통과 {sum(rule)}, "
          f"일치 {agree} ({agree / len(chunks):.0%})")

    print('\n--- 갈린 청크 ---')
    for c, a, b in zip(chunks, llm, rule):
        if a != b:
            head = ' > '.join(c['metadata']['headings'][-2:])[:52]
            body = ' / '.join(x.strip() for x in c['text'].split('\n') if x.strip())[:70]
            print(f"  [{c['chunk_id']:2}] LLM={'참' if a else '거짓'} 룰={'참' if b else '거짓'}  {head}")
            print(f"       {body}")


def sweep(cache: Path) -> None:
    """문서 18개에 룰만 적용한다. API 호출이 없어 즉시 끝난다."""
    print(f"\n[sweep] {'문서':36} {'청크':>5} {'통과':>5} {'비율':>6}")
    print('─' * 58)
    total = passed = 0
    for f in sorted(cache.glob('*.md')):
        chunks = MarkdownChunker.chunk(f.read_text(encoding='utf-8'), 1000, 200)
        n = sum(1 for c in chunks if rule_verdict(c))
        total += len(chunks)
        passed += n
        print(f"         {f.stem[:35]:36} {len(chunks):5} {n:5} {n / len(chunks):6.0%}")
    print('─' * 58)
    print(f"         {'합계':36} {total:5} {passed:5} {passed / total:6.0%}")


async def main() -> None:
    cache = Path(sys.argv[1])
    mode = sys.argv[2] if len(sys.argv) > 2 else 'sweep'
    if mode in ('sweep', 'all'):
        sweep(cache)
    if mode in ('llm', 'all'):
        await compare_with_llm(cache)


if __name__ == '__main__':
    asyncio.run(main())
