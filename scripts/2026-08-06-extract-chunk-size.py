"""
2026-08-06 실험 재현용. chunk_size 가 추출 정확도에 미치는 영향을 잰다.

청킹 지표(파편·군집·쪼개진 API)는 chunk_size 를 키울수록 전부 좋아졌다. 하지만 큰 청크에는
API 가 여러 개 들어가므로, LLM 이 그걸 정확히 분리해 각각의 파라미터를 제대로 귀속시키는지는
별개 문제다. 정확도를 재기 전에는 바꿀 수 없다.

정답은 문서에서 기계적으로 뽑는다 — 'POST http(s)://[serverip]/mfd/api/token' 형태의
메서드+경로는 API 마다 고유하므로 사람 라벨링 없이 재현율을 잴 수 있다.

    PYTHONPATH=. poetry run python scripts/2026-08-06-extract-chunk-size.py \
        '/tmp/mdcache/[SECUI] MFD REST API 가이드.md' 1000 4000 8000

OPENAI_API_KEY 가 필요하다. 설정한 chunk_size 마다 선별 + 추출을 한 번씩 돈다.
→ docs/experiments/2026-08-06-extraction-quality.md
"""
from __future__ import annotations

import asyncio
import re
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv('.env')

from src.core.chunker.MarkdownChunker import MarkdownChunker    # noqa: E402
from src.core.extractor.SpecExtractor import SpecExtractor      # noqa: E402
from src.core.extractor.SpecScorer import SpecScorer            # noqa: E402

#: 'POST http(s)://[serverip]/mfd/api/token' 또는 'GET /api/v1/devices'
API_CALL = re.compile(r'\b(GET|POST|PUT|PATCH|DELETE)\s+(http\(s\)://\[[^\]]+\]\S+|/\S+)')


def normalize_endpoint(path: str) -> str:
    """호스트 자리표시자를 떼고 경로만 남긴다. 모델이 어떻게 쓰든 같은 키로 맞춘다."""
    path = re.sub(r'^https?(\(s\))?://[^/]+', '', path.strip())
    path = re.sub(r'^\[[^\]]+\]', '', path)
    return path.rstrip('/ .,)')


def truth_calls(markdown: str) -> set[tuple[str, str]]:
    """문서에 적힌 메서드+경로 집합. 이게 정답이다."""
    return {(m.upper(), normalize_endpoint(p)) for m, p in API_CALL.findall(markdown)}


async def run(markdown: str, chunk_size: int, overlap: int, truth: set) -> dict:
    chunks = MarkdownChunker.chunk(markdown, chunk_size, overlap)
    extractor = SpecExtractor()

    start = time.monotonic()
    selected = await extractor.select(chunks)
    pairs = await extractor.extract(selected)
    elapsed = time.monotonic() - start

    found: set[tuple[str, str]] = set()
    params: dict[tuple[str, str], set[str]] = {}
    grounded: dict[tuple[str, str], set[str]] = {}
    scores = []

    for chunk, spec in pairs:
        scores.append(SpecScorer.score(spec, chunk['text']))
        method, endpoint = spec.get('method'), spec.get('endpoint')
        if not method or not endpoint:
            continue

        key = (method.upper(), normalize_endpoint(endpoint))
        found.add(key)
        source = SpecScorer._normalize(chunk['text'])

        for p in (spec.get('request_parameters') or []) + (spec.get('response_parameters') or []):
            name = p.get('name')
            if not name:
                continue
            params.setdefault(key, set()).add(name)
            # 근거가 원문에 실재하는 파라미터만 따로 센다. 이게 실제로 확보한 정보량이다.
            if SpecScorer._is_grounded(p, source):
                grounded.setdefault(key, set()).add(name)

    hit = found & truth
    return {
        'chunks': len(chunks),
        'selected': len(selected),
        'specs': len(pairs),
        'apis': len(found),
        'hit': len(hit),
        'missed': sorted(truth - found),
        'extra': sorted(found - truth),
        'params': sum(len(v) for v in params.values()),
        'grounded': sum(len(v) for v in grounded.values()),
        'score_avg': sum(scores) / len(scores) if scores else 0,
        'score_90': sum(1 for s in scores if s >= 90),
        'elapsed': elapsed,
    }


async def main() -> None:
    path = Path(sys.argv[1])
    sizes = [int(x) for x in sys.argv[2:]] or [1000, 4000, 8000]

    markdown = path.read_text(encoding='utf-8')
    truth = truth_calls(markdown)
    print(f"문서 {path.name}")
    print(f"정답 API {len(truth)}개 (문서에 적힌 메서드+경로)\n")

    print(f"{'size':>6} {'청크':>5} {'선별':>5} {'API':>4} {'적중':>5} {'놓침':>5} {'오검':>5} "
          f"{'파라미터':>8} {'근거있음':>8} {'점수':>6} {'초':>6}")
    print('─' * 80)

    results = {}
    for size in sizes:
        r = await run(markdown, size, min(200, size // 5), truth)
        results[size] = r
        print(f"{size:6,} {r['chunks']:5} {r['selected']:5} {r['apis']:4} "
              f"{r['hit']:5} {len(r['missed']):5} {len(r['extra']):5} {r['params']:8} "
              f"{r['grounded']:8} {r['score_avg']:6.1f} {r['elapsed']:6.1f}")

    print(f"\n적중 = 정답 {len(truth)}개 중 찾은 수    근거있음 = evidence 가 원문에 실재하는 파라미터")
    for size, r in results.items():
        if r['missed']:
            print(f"\nchunk_size={size:,} 놓친 API {len(r['missed'])}개")
            for m, p in r['missed'][:8]:
                print(f"    {m:7} {p}")
        if r['extra']:
            print(f"chunk_size={size:,} 정답에 없는 endpoint {len(r['extra'])}개")
            for m, p in r['extra'][:8]:
                print(f"    {m:7} {p}")


if __name__ == '__main__':
    asyncio.run(main())
