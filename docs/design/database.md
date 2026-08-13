# DB 설계

DDL 의 유일한 진실은 [`sql/schema.sql`](../../sql/schema.sql) 이다. 이 문서는
**왜 그렇게 생겼는지**를 적는다. 컬럼 목록을 여기 옮겨 적지 않는다 — 두 곳이 어긋난다.

## 테이블 다섯 개

```
vendor ─┬─ device ─┬─ api_definition ─── api_schema      운영 (검증된 것만)
        │          │        │
        └ 이름만 참조 ┘        └──────── api_extraction    스테이징 (전부)
```

| 테이블 | 무엇 | 언제 행이 생기나 |
|---|---|---|
| `vendor` | 제조사 | 승인 시 upsert |
| `device` | 장비/제품 | 승인 시 upsert |
| `api_definition` | API 하나 (주소, 메서드, 이름) | 승인 시 upsert |
| `api_schema` | 그 API 의 요청/응답 파라미터 | 승인 시 upsert (1:1) |
| `api_extraction` | 추출 결과 + 검토 상태 | **추출될 때 전부** |

핵심은 **`api_extraction` 만 무조건 쌓이고, 나머지 넷은 승인을 통과해야 생긴다**는 것이다.

## 왜 스테이징을 따로 두나

LLM 이 뽑은 것을 곧바로 운영 테이블에 넣으면 환각이 섞인다. 그래서 전부
`api_extraction` 에 먼저 넣고, 근거 대조 점수가 90 이상인 것만 자동으로 옮긴다.

| 점수 | `review_status` | 운영 테이블 |
|---|---|---|
| ≥ 90 | `approved` | 즉시 이관 |
| 70 ~ 89 | `pending` | 사람 승인 후 |
| < 70 | `rejected` | 안 감 |

→ [ADR-0007](../adr/0007-evidence-based-confidence-scoring.md)

이 구조 때문에 컬럼 두 개가 얼핏 중복으로 보인다. 둘 다 필요하다.

**`source_text`** — 추출의 근거가 된 청크 원문. 작업이 끝나면 변환 산출물이 지워지므로,
여기 복사해 두지 않으면 채점이 대조할 문자열이 없고 승인 화면도 보여 줄 게 없다.

**`vendor_name` / `device_name`** (id 가 아니라 이름) — 반려된 추출은 `vendor` /
`device` 에 행을 만들지 않는다. FK 를 걸면 아직 없는 마스터를 참조해야 한다.
마스터 upsert 는 승인 시점에 일어난다.

## 식별자

### 기본키는 정수 자동증가

`vendor` / `device` / `api_definition` / `api_schema` 는 `INT`,
`api_extraction` 은 행이 훨씬 많아 `BIGINT` 다.

**UUID 를 쓰지 않는 이유:** UUID 가 값을 하는 자리는 여러 노드가 각자 ID 를 만들거나,
DB 를 나중에 합치거나, ID 를 외부에 노출해 열거를 막아야 할 때다. 여기는 셋 다 아니다 —
MariaDB 한 대, 내부 도구, 조인이 잦은 좁은 FK 그래프. 정수 쪽이 인덱스가 작고 조인이 싸다.

**날짜순으로 정렬되는 UUID 를 찾는다면 그건 UUIDv7 이다** (RFC 9562, 2024).
앞 48비트가 Unix 밀리초라 문자열 정렬이 곧 생성 시각 정렬이고, uuid4 와 달리 B-tree
끝에 순차 삽입돼 페이지 분할이 적다.

- Python 3.14 표준 `uuid` 모듈에 `uuid7()` 이 있다. 이 프로젝트는 3.14 라 바로 쓴다.
- **지금 쓰는 MariaDB 11.4 에는 `UUID_v7()` 함수가 없다** (직접 확인). DB 기본값으로는
  못 만들고 애플리케이션이 생성해야 한다.

지금 스키마에서 UUIDv7 이 의미 있을 후보는 `api_extraction.task_id` 하나다
(현재 `uuid.uuid4().hex`, `TaskStore.create`). 바꾸면 태스크 ID 만 보고 실행 순서를
알 수 있다. **아직 안 바꿨다** — 같은 정보가 `created_at` 에 이미 있고, 한 줄 교체지만
이유 없이 값의 형식을 바꾸면 기존 ID 와 섞인다.

### API 의 정체는 `method` + `endpoint`

`api_definition` 의 유니크 키는 파생 컬럼 `api_key` 위에 있다.

```sql
api_key VARCHAR(540)
        AS (CONCAT(COALESCE(method, '-'), ' ',
                   COALESCE(endpoint, CONCAT('#', title)))) STORED
```

`title` 을 쓰면 안 된다. 제목은 청크의 헤더 경로에서 오는데 한 절에 API 가 여럿인
문서가 흔해서(`5.2.4 적용/취소` 아래 apply 와 cancel) 서로 다른 API 가 한 행으로
뭉개진다. 실측에서 승인 25건이 정의 18개로 줄었다.

`endpoint` 가 NULL 이면 제목으로 물러선다. NULL 을 그대로 키에 넣으면 SQL 이 NULL 끼리
다르다고 봐서 같은 문서를 다시 올릴 때마다 행이 늘어난다.

→ [ADR-0012](../adr/0012-api-identity-is-the-endpoint.md)

### `task_id` 는 FK 가 아니다

`api_extraction.task_id` 는 Redis 에 있는 작업 상태를 가리키는 값이고, 그 상태는 TTL 로
사라진다. DB 에는 참조할 대상 테이블이 없다. 한 실행에서 나온 추출을 묶어 보는 용도다.

## 파라미터를 JSON 으로 두는 이유

`api_schema.request` / `response` 는 파라미터 배열을 통째로 담은 JSON 이다.
파라미터마다 행을 만들지 않는다.

파라미터는 **항상 API 단위로 통째로 읽고 통째로 쓴다.** 개별 파라미터를 조건으로
검색하거나 하나만 갱신하는 요구가 없다. 행으로 펴면 조인이 하나 늘고 순서를 따로
관리해야 하는데 얻는 게 없다.

`type`(JSON 기본형)과 `raw_type`(문서 표기 그대로)을 둘 다 싣는다 — 호출부는 정규화된
쪽을, 사람은 `varchar(64)` / `YYYY-MM-DD` 같은 제약이 남아 있는 쪽을 본다.

**`evidence` 는 운영 스키마에 넣지 않는다.** 채점이 끝나면 쓸 일이 없고,
`api_extraction.spec_json` 에 원본 그대로 남아 있다.

`api_schema` 가 `api_definition` 과 1:1(`UNIQUE(api_def_id)`)이라 **나중 적재가 앞의
것을 통째로 덮어쓴다.** 그래서 한 API 가 여러 청크에 걸치면 적재 *전에* 합쳐야 한다.
→ [ADR-0010](../adr/0010-extraction-verification-and-merge.md)

## 인덱스

의도적으로 적다. 이 DB 는 읽기가 드물고, 대부분 승인 화면이 상태로 거르는 것뿐이다.

- `idx_api_extraction_review (review_status)` — 검토 대기열 조회
- 유니크 제약들이 곧 조회 인덱스다 (`vendor.name`, `uk_api_definition`, `uk_api_schema_def`)

`api_extraction.task_id` 에는 인덱스가 **없다.** 애플리케이션이 그걸로 조회하지 않는다.
한 실행의 추출을 자주 훑게 되면 그때 붙인다.

## 삭제와 보존

| 관계 | 동작 | 이유 |
|---|---|---|
| `vendor` → `device` | `RESTRICT` | 장비가 남은 제조사를 지우면 고아가 된다 |
| `device` → `api_definition` | `RESTRICT` | 정의가 남은 장비를 지우면 고아가 된다 |
| `api_definition` → `api_schema` | `CASCADE` | 스키마는 정의 없이 의미가 없다 |
| `api_definition` → `api_extraction` | `SET NULL` | 추출 기록은 정의가 사라져도 남아야 한다 |

`api_extraction` 은 **아무것도 지우지 않는다.** 반려된 것도 남는다 — 무엇이 왜 걸렸는지가
프롬프트를 고칠 유일한 근거다.

업로드 원본은 규격이 하나라도 적재되면 `{staging}/review/{task_id}{확장자}` 로 옮겨
남긴다. 승인 화면의 `source_url` 이 이 파일을 연다.

작업이 끝날 때마다 오래된 보관본을 턴다. 지우는 조건은 **둘 다** 만족할 때다.

- `review_retention_days`(기본 30일)가 지났다
- 그 태스크에 `pending` 인 추출이 하나도 없다

`pending` 이 남아 있으면 나이와 무관하게 보존한다 — 사람이 대조할 근거가 그것뿐이다.
`review_retention_days` 를 `0` 으로 두면 정리하지 않는다.
→ [pipeline.md](pipeline.md#원본-보관)

## 스키마를 바꿀 때

**마이그레이션 도구가 없다.** `sql/schema.sql` 을 고치는 것이 전부이고, 이미 데이터가
있는 DB 는 `ALTER` 를 직접 돌린다. 되돌리기 어려운 변경이면 그 `ALTER` 를 ADR 에 적어 둔다.
→ [ADR-0012 의 마이그레이션 절](../adr/0012-api-identity-is-the-endpoint.md#결과)

DDL 을 고쳤으면 빈 DB 에 통째로 적용해 파싱과 제약을 확인한다.

```bash
kubectl -n integration-api exec -i deploy/integration-api-mariadb -- \
    mariadb -uroot -p'<암호>' -e "CREATE DATABASE schema_check"
kubectl -n integration-api exec -i deploy/integration-api-mariadb -- \
    mariadb -uroot -p'<암호>' schema_check < sql/schema.sql
```

**MariaDB 계정은 데이터 디렉토리가 비어 있을 때만 만들어진다.** Secret 을 바꿔도 이미
데이터가 있으면 계정은 그대로다. → [k8s/README.md](../../k8s/README.md)

접속 정보는 `conf/ia-conf.xml` 이 아니라 `conf/db.properties` 에 있고 비밀번호는
AES256 으로 암호화돼 있다.

```bash
PYTHONPATH=. python -m src.utils.AES256 <계정> <비밀번호>
```
