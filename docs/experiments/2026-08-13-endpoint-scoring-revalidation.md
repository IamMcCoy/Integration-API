# endpoint 필수 채점 재실측 — 무자격 조각 적재 0건

- **날짜:** 2026-08-13
- **결론:** [08-10 실측](2026-08-10-full-corpus-threshold-validation.md)에서 발견한
  "엔드포인트 없는 조각의 만점 적재"가 채점 보강(근거 있는 endpoint 가 없으면 0점)
  이후 **재발하지 않는다.** `api_definition` 의 endpoint NULL 이 162건(10%) → **0건**.
  분포는 여전히 이봉이라 임계값 90/70 유지 근거도 그대로다.

## 방법

DB 를 스키마부터 재생성하고 같은 문서 18개를 다시 업로드했다. 검증 환경은
08-10 과 동일 (`gpt-4o-mini`, temperature 0, chunk_size 1,000, 워커 1개, 임계값 90/70).
달라진 것은 채점 로직 하나다 — endpoint 가 원문에 근거 없이 비어 있으면 0점.

## 결과

18개 전부 SUCCEEDED, 추출 2,797건 (08-10: 2,780건).

| 갈래 | 08-10 | 이번 | 변화 |
|---|---|---|---|
| 자동 적재 (90~100) | 2,156 (77.6%) | 1,973 (70.5%) | −183 |
| 승인 대기 (70~89) | 89 (3.2%) | 67 (2.4%) | −22 |
| 반려 (<70) | 535 (19.2%) | 757 (27.1%) | +222 |

- **`api_definition` 1,509건, endpoint NULL 0건.** 08-10 은 1,628건 중 162건(10%)이
  NULL 이었다 — "Response Body", 부록 표 조각 같은 호출 불가 행들이 전부 사라졌다.
- **반려 757건 중 354건이 endpoint 없는 규격이다.** 전에는 파라미터 근거만 맞으면
  100점으로 적재되던 유형이 의도대로 반려로 빠진다.
- 자동 적재 감소분(−183)은 무자격 조각이 빠진 것이지 정상 API 의 손실이 아니다 —
  승인군 평균은 99.9로 동일하다.
- 분포는 여전히 양 끝에 몰린다 (승인군 99.9, 반려군 6.9, 중간 50~89 구간 4.5%).
  임계값을 70~90 어디에 두든 갈래가 거의 안 바뀌는 성질도 유지된다.

문서별로는 WAPPLES 평균이 59.6 → 49.6 으로 내려갔다 — endpoint 없는 조각이
0점을 받게 된 효과가 저품질 문서에서 더 크게 나타난 것으로, 08-10 의
"표 레이아웃 가설" 확인은 여전히 남은 일이다.

## 남은 것

- WAPPLES / pan-os 반려 샘플을 열어 표 레이아웃 가설 확인 (08-10 에서 이월)
  → 같은 날 확인했다: [반려 원인 분석](2026-08-13-rejection-causes.md)
- 승인 대기 67건 실제 검토 — 70~89 구간의 실제 품질 확인

## 재현

[08-10 실험](2026-08-10-full-corpus-threshold-validation.md)의 재현 절차와 동일.
집계에 이번에 추가된 `GET /extractions/stats` 를 써도 된다.

```bash
kubectl exec -n integration-api <mariadb-pod> -- mariadb -utest -p'<pw>' integration_api -e "
SELECT COUNT(*) AS total_defs, SUM(endpoint IS NULL) AS null_endpoint FROM api_definition;
SELECT COUNT(*) FROM api_extraction
 WHERE review_status='rejected' AND JSON_VALUE(spec_json,'\$.endpoint') IS NULL;"
```

검증 환경: `openai 2.53.0`, `gpt-4o-mini`, 문서 18개 / 추출 2,797건.
