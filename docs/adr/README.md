# ADR (Architecture Decision Records)

되돌리기 비싼 결정과 **왜 그렇게 했는지**를 남긴다.
대안을 놓고 하나를 골랐을 때만 쓴다. 자명한 선택은 쓰지 않는다.

새 ADR 은 [`template.md`](template.md) 를 복사하고 다음 번호를 붙인다.
한 번 쓴 ADR 은 고치지 않는다 — 결정이 바뀌면 새 ADR 을 쓰고
이전 것의 상태를 `Superseded by ADR-XXXX` 로 바꾼다.

## 목록

| 번호 | 제목 | 상태 | 날짜 |
|---|---|---|---|
| [0001](0001-layered-parser-separation.md) | 파서를 읽기·변환·청킹 계층으로 분리 | Accepted | 2026-08-05 |
| [0002](0002-task-scoped-staging-directory.md) | 업로드 파일 수명을 작업에 묶는다 | Accepted | 2026-08-05 |
| [0003](0003-redis-task-queue.md) | 작업 큐와 상태를 Redis 에 둔다 | Accepted | 2026-08-05 |
| [0004](0004-fail-on-empty-extraction.md) | 추출 결과가 비면 실패로 처리한다 | Accepted | 2026-08-05 |
| [0005](0005-vendor-neutral-naming.md) | API 표면에서 벤더 용어를 제거한다 | Accepted | 2026-08-05 |
| [0006](0006-custom-markdown-splitter.md) | 마크다운 스플리터를 직접 구현한다 | Accepted | 2026-08-06 |
| [0007](0007-evidence-based-confidence-scoring.md) | 근거 검증으로 신뢰도를 매기고 스테이징에 먼저 적재한다 | Accepted | 2026-08-06 |
| [0008](0008-header-level-and-section-merge.md) | 헤더는 h6 까지 경계로 삼고, 작은 섹션은 다시 합친다 | Accepted | 2026-08-06 |
| [0009](0009-filter-model-and-prompt.md) | 선별에 비추론 모델과 영어 프롬프트를 쓴다 | Accepted | 2026-08-06 |
| [0010](0010-extraction-verification-and-merge.md) | 엔드포인트도 근거를 대조하고, 같은 API 의 조각은 적재 전에 합친다 | Accepted | 2026-08-06 |
| [0011](0011-prompt-rationale.md) | 프롬프트에 지시와 함께 이유를 적는다 | Accepted | 2026-08-07 |
| [0012](0012-api-identity-is-the-endpoint.md) | API 의 정체는 제목이 아니라 호출 주소다 | Accepted | 2026-08-07 |
