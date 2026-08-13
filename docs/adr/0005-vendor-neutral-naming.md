# ADR-0005: API 표면에서 벤더 용어를 제거한다

- **상태:** Accepted
- **날짜:** 2026-08-05

## 배경

API 설계를 이전 프로젝트(Qdrant 기반 벡터 검색)의 라우터 코드를 참고해 시작했다.
그 결과 Qdrant 어휘가 그대로 따라 들어왔다.

```
POST /collections/file
POST /collections/{collection_name}/append

collection_name, target_field, meta_field, distance='Cosine'
TaskType.CREATE_COLLECTION / APPEND_COLLECTION
```

그런데 이 프로젝트의 실제 정체는 다르다.
**PDF 에서 API 규격을 추출해 MariaDB 에 적재하는 파이프라인**이다.
벡터 저장소가 없다.

- `collection` — Qdrant 의 저장 단위 이름. 여기엔 그런 개념이 없다.
- `distance` (Cosine/Euclidean/Dot) — 벡터 유사도 측정 방식. MariaDB 적재에서 무의미하다.
- `target_field` / `meta_field` — Qdrant 페이로드 매핑용.

의미 없는 파라미터를 남겨두면 나중에 누가 "이거 왜 안 먹지" 하고 시간을 쓴다.

## 선택지

**A. `dataset`** — 문서 묶음이라는 뜻에 가깝고 저장소 중립적.
**B. `index`** — 검색 인덱스 의미가 직관적이나 Elasticsearch 색깔이 있다.
**C. `document` → `spec`** — 입력(문서)과 산출물(규격)을 각각 이름 붙인다.

## 결정

**C** 를 택했다. 이 파이프라인은 "묶음을 만드는" 게 아니라
**문서 하나를 넣어 규격을 뽑는** 것이므로, 입력과 산출물을 나눠 부르는 쪽이 정확하다.

| 이전 | 이후 |
|---|---|
| `POST /collections/file` | `POST /documents` |
| `POST /collections/{n}/append` | *(삭제)* |
| `collection_name` | `document_name` |
| `UploadParams` | `ExtractionParams` |
| `distance`, `target_field`, `meta_field` | *(삭제)* |
| `TaskType.CREATE_COLLECTION` / `APPEND_COLLECTION` | *(삭제)* |
| `Constants.COLLECTION_ENDPOINT` | `Constants.DOCUMENT_ENDPOINT` |
| `result: dict` | `ExtractionResult` 모델 |

`TaskType` 을 지운 이유는 별개다. 파이프라인이 **하나뿐**이면
그 필드는 값이 하나뿐이다. 값이 안 변하는 필드는 두지 않는다.

`TaskStage` 도 실제 파이프라인에 맞춰 다시 정의했다.
`indexing` 같은 뭉뚱그린 이름 대신:

```
staged → parsing → chunking → filtering → extracting → persisting → done
```

## 결과

- `GET /tasks/{id}` 응답만 봐도 문서가 어느 단계에 있는지 알 수 있다.
- 적재 대상을 바꿔도(MariaDB → 다른 것) API 표면은 그대로다.
- 라우터를 `src/api/Router.py` 하나로 통합하면서 함께 반영했다.
  `src/api/routes/` 디렉토리는 제거했다.
- **감수한 것:** `append` 엔드포인트가 사라졌다. 기존 문서에 내용을 덧붙이는
  유스케이스가 생기면 다시 설계한다 — 그때는 Qdrant 의 append 가 아니라
  이 도메인의 의미로 정의해야 한다.
- **되돌릴 신호:** 없음. 벤더 용어를 API 표면에 다시 노출할 이유는 없다.
