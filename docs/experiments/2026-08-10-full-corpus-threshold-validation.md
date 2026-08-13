# 임계값은 둔감했고, 문제는 다른 곳에 있었다

- **날짜:** 2026-08-10
- **결론:** 90/70 을 유지한다. 점수 분포가 양 끝에 몰려 있어(승인군 평균 99.9,
  반려군 대부분 0~49) 임계값을 70~90 사이 어디에 놓든 결과가 거의 같다.
  대신 임계값 밖의 문제가 나왔다: **엔드포인트 없는 조각이 무사통과해 적재의
  10%를 차지했고**(채점을 조여 막았다), 문서별 편차·마스터 테이블 오염·저청크
  문서 무통과도 확인됐다.

## 배경

[개요](../overview.md)의 다음 일이 "실제 문서 18개를 돌려 임계값(기본 90/70)이
타당한지 확인"이었다. 지금까지의 실측([2026-08-06](2026-08-06-extraction-quality.md),
[2026-08-07](2026-08-07-prompt-language-and-rationale.md))은 SECUI MFD 한 문서로만
쟀다 — 형식이 다른 문서에서도 같은 결론이 나오는지 확인하지 않았다.

## 방법

DB 를 스키마부터 재생성하고 `data/` 의 문서 18개를 전부 업로드했다.
`document_name` 은 비워서 파일명 폴백 경로도 함께 검증했다 (한글·대괄호
파일명 18개 전부 통과). 정답 라벨 없이 파이프라인 자체의 세 갈래
(자동 적재 / 승인 대기 / 반려) 분포와 마스터 테이블 상태를 봤다.

검증 환경: `gpt-4o-mini` (선별·추출 동일), temperature 0, chunk_size 1,000,
워커 1개, 임계값 90/70.

## 결과 1 — 분포가 이봉이라 임계값 위치가 거의 무의미하다

18개 문서 전부 SUCCEEDED, 추출 2,780건.

| 구간 | 건수 | 비율 | 처리 |
|---|---|---|---|
| 90~100 | 2,156 | 77.6% | 자동 적재 → 병합 후 `api_definition` 1,628건 |
| 70~89 | 89 | 3.2% | 승인 대기 |
| 50~69 | 76 | 2.7% | 반려 |
| 0~49 | 459 | 16.5% | 반려 |

승인군 평균이 99.9점, 반려군의 86%가 0~49점이다. 근거 대조 채점은
"원문에 있었다(≈100)"와 "지어냈다(≈0)"로 갈리지 어중간한 점수를 잘 만들지
않는다. **중간(50~89)이 전체의 6%뿐이므로 임계값을 70~90 사이 어디에 두든
갈래가 거의 안 바뀐다.** 임계값 튜닝보다 반려 원인 개선이 수확이 크다.

승인 대기 89건은 문서 18개 분량으로는 감당 가능한 검토량이다.

## 결과 2 — 문서별 편차가 크고, 언어 탓이 아니다

| 문서 | 추출 | 자동 | 대기 | 반려 | 평균점수 |
|---|---|---|---|---|---|
| WAPPLES 5.0 Web API | 330 | 189 | 6 | 135 | 59.6 |
| pan-os-panorama-api-v9.1 | 262 | 138 | 20 | 104 | 61.2 |
| WEBFRONT-K r6 | 68 | 41 | 4 | 23 | 66.9 |
| 유해IP차단시스템 v1.11 | 117 | 68 | 11 | 38 | 68.8 |
| (중간 10개 생략, 81~89점대) | | | | | |
| FortiOS-5.6.11 | 517 | 473 | 6 | 38 | 93.3 |
| FortiOS-6-0-4 | 610 | 566 | 4 | 40 | 94.6 |

최하위(WAPPLES 59.6)와 최상위(FortiOS 94.6)가 35점 차이다. WAPPLES 와
FortiOS 는 둘 다 영어 문서인데 갈리므로 **언어가 아니라 문서 형식(표 레이아웃)
차이로 보인다.** 반려 샘플을 열어 원인을 확인하는 게 다음 일이다.

## 결과 3 — 마스터 테이블이 오염된다

`vendor` / `device` 마스터에 [api 설계](../design/api.md)에서 경고한 두 경로가
실제로 나타났다.

- **같은 장비가 두 벤더로 갈렸다.** `WAPPLES` 가 Penta Security 와 SECUI
  양쪽 아래 등록됐다 (WAPPLES 는 Penta Security 제품). LLM 식별의 표기 요동이다.
- **파일명이 장비명으로 들어갔다.** 식별 실패 문서 4건은
  `유해IP차단시스템 API 명세서_v1.11` 같은 파일명 유래 값이 그대로
  `device.name` 이 됐다.

둘 다 파이프라인 결함이 아니라 **업로드 시 `vendor_name` / `device_name` 을
넣지 않은 운영 문제**다. 실제 운영에서는 채워서 올리는 것을 전제로 한다.

## 결과 4 — "청크는 있는데 비정상적으로 적은" 문서가 조용히 통과한다

| 문서 | 청크 | API 청크 | 규격 | 판정 |
|---|---|---|---|---|
| TnD_MIBS_User_Manual | 51 | 0 | 0 | SNMP MIB 매뉴얼. 0건이 정답 |
| Webkeeper정책연동_RestAPI | **6** | 0 | 0 | REST API 문서인데 0건. 의심 |

Webkeeper 는 1.2MB PDF 인데 청크가 6개뿐이다. 변환을 재현해 원인을 확정했다 —
스캔본도, [머리말/꼬리말 제거](2026-08-05-opendataloader-header-footer.md)도 아니다.
**API 명세가 전부 스크린샷 안에 있다.** 텍스트 레이어는 설치 가이드 7KB 뿐이라
청크 6개가 정확하고(7KB ÷ 1,000), 나머지는 이미지 19개(6.7MB)다. 4.5절
"WebKeeperPolicy API 설명"은 Swagger UI 화면 캡처이고, 실제 엔드포인트 목록
(`GET /policylist/{policyType}` 등 8개)이 그 이미지 안에만 존재한다.

변환·청킹·선별은 전부 옳게 동작했다 — 텍스트에 규격이 없으니 API 청크 0개가 정답이다.
**텍스트 전용 파이프라인이 구조적으로 못 뽑는 문서 유형**이며, 알려진 한계로 둔다.
18개 중 1개뿐인 유형에 비전 OCR 단계를 붙이는 건 과하다. 되돌릴 신호: 이미지 위주
문서가 대상 코퍼스에 늘어나면 추출 단계에 비전 모델(이미지 → 규격) 분기를 검토한다.

## 결과 5 — 엔드포인트 없는 조각이 만점으로 적재되고 있었다

적재된 `api_definition` 1,628건 중 **162건(10%)이 `endpoint` NULL** 이었다.
표본을 열어 보니 "Response Body", "application/json", "5. 부록", 튜토리얼 절차문 같은
**공통 응답 포맷·부록·가이드 산문 조각**이 대부분이다 — API 로 호출할 수 없는 행들이다.

경로는 세 단계다.

1. API 규격이 아닌(또는 URL 조각을 잃은) 청크에서 모델이 `endpoint: null` 규격을 낸다.
2. 병합은 그 조각을 붙일 그룹이 없어 단독으로 둔다 — [ADR-0010](../adr/0010-extraction-verification-and-merge.md)이
   예견한 동작이다.
3. 채점 `SpecScorer.score` 가 `if path and ...` 로 **엔드포인트가 None 이면 대조를
   통째로 건너뛴다.** 표에서 옮긴 파라미터 근거는 전부 실재하므로 100점 → 자동 적재.

[2026-08-06](2026-08-06-extraction-quality.md)에서 "지어낸 엔드포인트"는 0점으로
막았지만 "없는 엔드포인트"는 다른 분기로 빠져나가고 있었다. 검증 로직의
`if 값 and 검사` 패턴은 "값이 틀림"과 "값이 없음"을 다르게 취급하는데, 이 도메인에선
둘 다 정체 불명 API 로 같아야 한다.

→ 근거 있는 엔드포인트가 **없어도** 0점으로 바꿨다. 검토 대기가 아니라 반려다 —
표본상 대부분이 API 가 아니므로 검토 큐에 넣으면 그게 공해다. 기존 162건은
`scripts/2026-08-10-purge-null-endpoint-defs.sql` 로 정리한다.

## 남은 것

- WAPPLES / pan-os 반려 샘플을 열어 표 레이아웃 가설 확인
- 승인 대기 89건을 실제로 검토해 보면 70~89 구간의 실제 품질을 알 수 있다 —
  대부분 승인된다면 자동 임계값을 낮출 근거가 된다

## 재현

```bash
# DB 초기화 (k3d 클러스터의 MariaDB)
kubectl exec -n integration-api <mariadb-pod> -- \
    mariadb -utest -p'<pw>' -e "DROP DATABASE integration_api; CREATE DATABASE integration_api CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
kubectl exec -i -n integration-api <mariadb-pod> -- \
    mariadb -utest -p'<pw>' integration_api < sql/schema.sql

# 앱 기동 후 전체 업로드
poetry run python src/Application.py &
cd data && for f in *.pdf; do curl -s -X POST http://localhost:8880/documents -F "file=@${f}"; done

# 완료 후 분포 집계
kubectl exec -n integration-api <mariadb-pod> -- mariadb -utest -p'<pw>' integration_api -e "
SELECT review_status, COUNT(*), ROUND(AVG(score),1) FROM api_extraction GROUP BY review_status;
SELECT document_name, COUNT(*), SUM(score>=90), SUM(score<90 AND score>=70), SUM(score<70), ROUND(AVG(score),1)
  FROM api_extraction GROUP BY document_name ORDER BY AVG(score);"
```

`data/` 는 gitignore 라 저장소에 없다 — 대상 문서 18개를 넣고 돌려야 한다.

검증 환경: `openai 2.53.0`, `gpt-4o-mini`, 문서 18개 / 추출 2,780건.
