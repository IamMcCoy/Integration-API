# API 설계

## 엔드포인트

| 메서드 | 경로 | 응답 | 설명 |
|---|---|---|---|
| `POST` | `/documents` | `202 TaskCreated` | 문서 업로드 → 추출 작업 접수 |
| `DELETE` | `/documents/{task_id}/data` | `200 PurgeResult` | 문서 단위 데이터 삭제 (추출 + 미참조 정의 + 보관 원본). 멱등 |
| `GET` | `/tasks` | `200 list[TaskDetail]` | 작업 목록 (최신순, `status` 필터) |
| `GET` | `/tasks/{task_id}` | `200 TaskDetail` | 진행 상황 조회 |
| `DELETE` | `/tasks/{task_id}` | `200 TaskDetail` | 작업 취소. PENDING 즉시, RUNNING 은 단계 경계에서 협조적 중단 |
| `GET` | `/extractions` | `200 list[ExtractionDetail]` | 검토 대기열 조회. 문서/벤더/장비 필터, `offset`, 총건수는 `X-Total-Count` 헤더 |
| `GET` | `/extractions/stats` | `200 list[ExtractionStats]` | 문서별 검토 현황 집계 (대시보드용, 평균점수 오름차순) |
| `GET` | `/extractions/{id}` | `200 ExtractionDetail` | 추출 단건 조회 |
| `GET` | `/extractions/{id}/source` | `200 파일` | 근거가 된 원본 문서 |
| `POST` | `/extractions/{id}/approve` | `200 ReviewResult` | 승인 → 운영 테이블로 이관. 본문으로 수정본 전달 가능. 승인된 건 재승인 = 되돌린 뒤 재적재 |
| `POST` | `/extractions/{id}/reject` | `200 ReviewResult` | 반려. 승인된 건이면 승인 취소 — 운영 테이블에서도 되돌림 (미참조일 때만 삭제) |
| `GET` | `/apis` | `200 list[ApiSummary]` | 적재된 운영 API 목록. `vendor`/`device`/`method`/`document` 정확 일치, `q` 부분 일치, `offset`, 총건수는 `X-Total-Count` 헤더. 응답에 출처 `document_name` 포함 |
| `GET` | `/apis/{id}` | `200 ApiDetail` | 운영 API 단건 + 요청/응답 스키마 |
| `GET` | `/health` | `200` | 헬스 체크 |

접수와 처리를 분리했다. `POST` 는 파일을 착지시키고 작업을 등록한 뒤 **즉시 202** 를
반환한다. PDF 변환과 LLM 호출은 수 분이 걸릴 수 있어 요청을 붙잡아 두면 안 된다.

## 요청

```
POST /documents
Content-Type: multipart/form-data

file            업로드할 문서 (pdf, txt)
document_name   문서 식별용 이름   선택. 최대 64자. 비우면 파일명에서 확장자를 뗀다
vendor_name     적재 대상 제조사   선택. 비우면 문서에서 읽는다
device_name     적재 대상 장비     선택. 비우면 문서에서 읽는다
api_version     API/문서 버전      선택. 비우면 문서에서 읽는다
chunk_size      기본 1000
chunk_overlap   기본 200  (chunk_size 보다 작아야 함)
```

`vendor_name` / `device_name` / `api_version` 은 넣어 주는 쪽이 항상 정확하다.
비우면 문서 앞부분을 LLM 이 읽어 채우는데, 표기 요동(SECUI/시큐아이)으로 같은
벤더가 마스터에 둘로 갈릴 수 있다.

`document_name` 은 검토 화면의 출처 표시와 원문 내려받기 파일명에 쓰인다. 넣지 않으면
파일명에서 확장자를 뗀 값이 들어가므로, **같은 파일을 여러 번 올리면 이름이 같아져
구분되지 않는다.** 재업로드를 구분하려면 넣는다. 셋 다 비고 LLM 식별까지 실패하면
이 값이 `vendor_name` / `device_name` 으로 떨어져 마스터에 등록되니, 그 경우에는
파일명이 아니라 장비를 가리키는 이름을 넣는 쪽이 낫다.

```
GET /extractions?status=pending&limit=50

status   approved | pending | rejected   기본 pending
limit    1~500                           기본 50
```

## 응답

```jsonc
// 202 POST /documents
{ "task_id": "a9add8b8...", "status": "PENDING" }

// 200 GET /tasks/{task_id}
{
  "task_id": "a9add8b8...",
  "status": "SUCCEEDED",           // PENDING | RUNNING | SUCCEEDED | FAILED
  "stage": "done",                 // staged → parsing → chunking → filtering → extracting → persisting → done
  "document_name": "my_docs",
  "filename": "quarterly.pdf",
  "processed": 12,
  "total": 12,                     // 청킹이 끝나야 확정. 그전에는 null
  "result": {
    "document_name": "my_docs",
    "chunk_count": 12,
    "api_chunk_count": 7,          // 선별을 통과한 청크 수
    "spec_count": 9                // api_extraction 에 적재된 규격 수
  },
  "error": null,
  "created_at": "2026-08-05T07:43:32.717866+00:00",
  "updated_at": "2026-08-05T07:43:32.953231+00:00"
}

// 200 GET /extractions?status=pending
[
  {
    "id": 41,
    "task_id": "a9add8b8...",
    "document_name": "secui_mfd",
    "vendor_name": "SECUI",
    "device_name": "MFD",
    "api_version": "1.0",
    "chunk_id": 7,
    "page_number": 23,
    "source_text": "## 장비 목록 조회\n\n| 이름 | 타입 | ...",   // 대조용 원문
    "spec_json": {
      "title": "장비 목록 조회",
      "method": "GET",
      "endpoint": "/api/v1/devices",
      "request_parameters": [
        {
          "name": "deviceId",
          "type": "string",                // JSON 기본형으로 정규화된 값
          "raw_type": "varchar(64)",       // 문서에 적힌 표기 그대로
          "required": true,
          "description": "장비 식별자",
          "evidence": "| deviceId | varchar(64) | Y | 장비 식별자 |"
        }
      ],
      "response_parameters": []
    },
    "score": 83,
    "review_status": "pending",
    "api_def_id": null,
    "created_at": "2026-08-06T02:11:04",
    "reviewed_at": null,
    "source_url": "/extractions/41/source#page=23"   // 원본 문서의 해당 페이지
  }
]

// 200 POST /extractions/41/approve   (본문 없음 — 저장된 그대로 승인)
{ "id": 41, "review_status": "approved", "api_def_id": 128, "score": 83 }

// 200 POST /extractions/41/approve   (본문에 수정본 — 고친 규격으로 승인)
{ "id": 41, "review_status": "approved", "api_def_id": 128, "score": 100 }
```

## 사람이 고쳐서 승인하기

조회로 받은 `spec_json` 을 그대로 고쳐 `approve` 본문에 실으면 저장된 규격을 대체한다.
왕복 한 번이면 끝나고, 초안을 따로 저장하는 단계는 없다.

```bash
curl -s '/extractions?status=pending' | jq '.[0].spec_json' > spec.json
$EDITOR spec.json
curl -X POST /extractions/41/approve -H 'Content-Type: application/json' -d @spec.json
```

본문은 `ApiSpec` 으로 검증한다. 형식이 어긋나면 `422` 다 — 추출 단계에서 LLM 이
지켜야 했던 것과 같은 스키마이므로, 운영 테이블에 들어갈 수 없는 모양은 여기서 막힌다.

**고친 규격도 원문과 다시 대조해 채점한다.** 사람 손을 거쳤다고 점수를 면제하면
`score` 컬럼이 무슨 뜻인지 알 수 없게 된다. 응답의 `score` 로 수정이 근거에
맞아떨어졌는지 확인할 수 있다.

점수가 낮아도 **승인 자체는 막지 않는다.** 사람의 판단이 채점보다 위다 —
대신 그 점수가 `reviewed_at` 옆에 그대로 남아, 나중에 무엇이 근거 없이
들어갔는지 추적할 수 있다.

`source_url` 은 추출의 근거가 된 원본 문서를 가리킨다. `source_text` 는 청크
하나뿐이라 표가 잘려 있거나 앞뒤 맥락이 없을 수 있어서, 원문 페이지를 열어 볼
길을 함께 준다. `#page=` 는 브라우저 PDF 뷰어가 읽는 조각 식별자다.

원본은 작업이 끝날 때 검토용으로 따로 보관된다. 보관본이 없으면 `404` 다.

`total` 과 아직 지나지 않은 단계의 카운트는 **`null`** 이다. `0` 으로 채우면
"해봤는데 없었다" 와 "아직 안 한다" 가 구분되지 않는다.

`spec_json` 의 `type` 은 `string | integer | number | boolean | array | object`
여섯 값으로 고정된다. 구조화 출력에서 enum 으로 강제하므로 문서 표기가 무엇이든
이 밖의 값은 나오지 않는다. 원문 표기는 `raw_type` 에 남는다.
중첩 응답은 `data.items[].deviceId` 처럼 경로로 펴서 나열된다.

## 상태 코드

| 코드 | 언제 |
|---|---|
| `202` | 접수 성공 |
| `400` | 파라미터 검증 실패, 미지원 확장자, 잘못된 파일명, `chunk_overlap >= chunk_size`, 이미 끝난 작업 취소 |
| `404` | 없거나 만료된 `task_id` (기본 24시간 후 만료), 없는 `extraction_id`, 없는 API `id` |
| `413` | 업로드 크기 상한 초과 |
| `422` | 쿼리 파라미터 검증 실패 (`status` 가 허용값 밖, `limit` 범위 초과) |
| `500` | 그 외 |

작업이 **실패**한 것과 요청이 **거부**된 것은 다르다. 접수된 뒤 처리 중 실패하면
HTTP 는 이미 202 를 보냈으므로, `GET /tasks/{task_id}` 의 `status: FAILED` + `error` 로 알린다.

## 업로드 검증

신뢰 경계다. 생략 대상이 아니다.

| 위험 | 처리 | 위치 |
|---|---|---|
| path traversal (`../`, `C:\`, 절대경로) | basename 만 취함, `.`/`..`/NUL 거부 | `FileReader._safe_filename` |
| 확장자 위조 | `ALLOWED_EXTENSIONS` 허용 목록 | `FileReader` |
| 대용량 → 메모리 폭발 | 1MB 청크 스트리밍 복사 | `FileReader._copy` |
| 디스크 고갈 | `upload_max_bytes` 상한 (기본 20MB) | `FileReader._copy` |
| 동시 업로드 경로 충돌 | 작업별 격리 디렉토리 | `FileReader.staging_dir` |

`Content-Length` 헤더로 먼저 거르지만 그것만 믿지 않는다 — 클라이언트가 보내는 값이다.
실제 상한은 스트리밍 중에 강제한다.

## 라우터 구성

엔드포인트는 `src/api/routes/` 아래에 도메인별로 정의하고,
`src/api/Router.py` 가 그것들을 합치기만 한다.

```
src/api/routes/Document.py     POST /documents
src/api/routes/Task.py         GET  /tasks/{task_id}
src/api/routes/Extraction.py   GET/POST /extractions...
src/api/Router.py              앱에 붙는 단일 진입점
```

각 파일은 `router` 라는 이름의 `APIRouter` 를 하나 노출한다.

```python
# src/api/Router.py
router = APIRouter()
router.include_router(Document.router)
router.include_router(Task.router)
router.include_router(Extraction.router)
```

라우터를 추가하면 `routes/` 에 파일 하나, `Router.py` 에 import 와 include 각각
한 줄씩이다. `Application` 은 `app.include_router(Router.router)` 한 줄만 안다.

DB 조회는 동기(SQLAlchemy Core)라 엔드포인트에서 `asyncio.to_thread` 로 감싼다.
그냥 부르면 이벤트 루프가 통째로 멈춘다.
