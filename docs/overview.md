# 개요

## 무엇을 하는가

PDF 문서를 업로드하면, 그 안에서 **API 규격**(요청 파라미터, 타입 등)을 뽑아
MariaDB 에 적재하는 단일 파이프라인이다.

```
PDF 업로드
  → 마크다운 변환
  → 청킹
  → API 관련 청크 선별       (LLM)
  → 규격 추출                (LLM, 근거 문자열 포함)
  → 신뢰도 채점 후 MariaDB 적재
```

문서 하나가 작업(task) 하나에 대응한다. 업로드는 즉시 `task_id` 를 돌려주고,
실제 처리는 큐에서 비동기로 진행된다. 진행 상황은 `GET /tasks/{task_id}` 로 본다.

추출 결과는 곧바로 운영 테이블로 가지 않는다. 파라미터마다 딸려 온 근거 문자열이
원문에 실재하는지 대조해 점수를 매기고, 높은 점수만 자동 적재한다. 나머지는
`GET /extractions?status=pending` 으로 사람이 보고 승인한다.
→ [ADR-0007](adr/0007-evidence-based-confidence-scoring.md)

## 무엇이 아닌가

- **벡터 검색 시스템이 아니다.** 초기 코드에 `collection`, `distance`,
  `target_field` 같은 Qdrant 용어가 남아 있었으나 전부 제거했다. 적재 대상은 MariaDB 다.
  → [ADR-0005](adr/0005-vendor-neutral-naming.md)
- **범용 문서 처리기가 아니다.** 목적은 API 규격 추출 하나다.

## 현재 상태 (2026-08-13)

| 단계 | 상태 |
|---|---|
| 업로드 접수 · 검증 · 착지 | 구현됨 |
| PDF → 마크다운 변환 | 구현됨 |
| 청킹 (헤더 h1~h6 경로, 작은 섹션 병합, 표 헤더 보존) | 구현됨 |
| 작업 큐 · 상태 조회 | 구현됨 |
| API 관련 청크 선별 (`filtering`) | 구현됨 |
| 규격 추출 (`extracting`) | 구현됨 |
| 신뢰도 채점 · MariaDB 적재 (`persisting`) | 구현됨 |
| 사람 검토 — 승인·수정 승인·반려·승인 취소 (`/extractions`) | 구현됨 |
| 검토 UI 조회 — 작업 목록·문서별 통계·운영 API 탐색 (`/tasks`, `/extractions/stats`, `/apis`) | 구현됨 |
| 작업 취소 · 문서 단위 데이터 삭제 (`DELETE /tasks/{id}`, `DELETE /documents/{id}/data`) | 구현됨 |
| 도커 이미지 · k8s 배포 (공개 베이스, 로컬 k3d 는 `make k3d-load`) | 구현됨 |

파이프라인 전 구간이 붙었고, 실제 문서 18개 전체 실측으로 임계값(90/70)의
타당성을 확인했다 — 분포가 이봉이라 유지한다.
→ [실험 기록](experiments/2026-08-10-full-corpus-threshold-validation.md)

endpoint 필수 채점을 넣은 뒤 같은 18개로 재실측해 무자격 조각 적재가 0건이
된 것을 확인했다. 검토 UI 를 위한 조회 API(작업 목록·검토 통계·운영 API 탐색)도 붙었다.
→ [재실측 기록](experiments/2026-08-13-endpoint-scoring-revalidation.md)

반려 원인도 확인했다 — WAPPLES 는 유실 없는 조각 반려, pan-os 는 PDF 글리프
손상(알려진 한계), 유해IP 는 JSON 예시의 표 뭉개짐.
→ [반려 원인 분석](experiments/2026-08-13-rejection-causes.md)

검토 UI 개발이 진행 중이다. 승인은 수정·재승인·승인 취소까지 왕복이 되고,
운영 API 조회는 출처 문서(`document_name`)로 구분·필터된다.

다음 일: 문서 18개를 다시 적재한 뒤, 승인 대기 건을 검토 UI 로 실제 검토해
임계값 하향(90→85) 근거 확인.

## 관련 문서

- [아키텍처](architecture/README.md)
- [파이프라인 설계](design/pipeline.md)
- [API 설계](design/api.md)
- [코드 규칙](design/code-style.md)
