# ADR-0012: API 의 정체는 제목이 아니라 호출 주소다

- **상태:** Accepted
- **날짜:** 2026-08-07

## 배경

[ADR-0010](0010-extraction-verification-and-merge.md) 에서 한 API 가 여러 청크에 걸치는
문제를 풀었다. 헤더 경로로 조각을 묶고, 한 헤더에 API 가 둘이면 **근거가 확인된
엔드포인트로 다시 쪼갠다.** `5.2.4 적용/취소` 아래 apply 와 cancel 이 따로 남는 이유다.

그 쪼개기가 DB 앞에서 되돌려지고 있었다.

```sql
CONSTRAINT uk_api_definition UNIQUE (device_id, api_version, title)
```

`title` 은 청크의 헤더 경로에서 온다. apply 와 cancel 은 **제목이 같다.** 그래서 둘 다
같은 행으로 upsert 되고, `api_schema` 의 `UNIQUE(api_def_id)` 때문에 나중 것이 앞의
파라미터를 통째로 덮어썼다.

실측(SECUI MFD, 승인 25건)에서 제목이 겹치는 그룹이 넷이었다.

| 제목 | 추출 건수 | 실제 경로 |
|---|---|---|
| 3.2 로그아웃 | 2 | `POST /logout`, `GET /sytem/info` |
| 4.3.4 ACL 삭제 | 3 | `DELETE /acls/{id}`, `PUT /acls/apply`, `PUT /acls/cancel` |
| 5.1.5 보호 도메인 설정 삭제 | 3 | `DELETE /pdomains/{id}`, `PUT /pdomains/apply`, `PUT /pdomains/cancel` |
| 5.2.4.1 사용자 정의 차단 목록 적용 | 2 | `PUT /user-black-list/apply`, `PUT /user-black-list/cancel` |

**승인 25건이 정의 18개로 줄었다. API 7개(28%)가 조용히 사라졌다.**

파이프라인 지표로는 안 보였다. 적중 계산이 DB 적재 *전* 값으로 이뤄져 25개를 다 세고
있었다 — 추출은 맞았고 저장에서 잃었다.

## 선택지

**A. 제목을 유일하게 만든다.** 한 헤더에 API 가 둘이면 제목에 엔드포인트를 붙인다.
표시용 이름을 식별자로 쓰려고 이름을 왜곡하는 것이라 기각.

**B. 유니크 키에 method 와 endpoint 를 더한다.**
`UNIQUE (device_id, api_version, title, method, endpoint)`.
DDL 한 줄로 끝나지만 `endpoint` 가 NULL 인 행을 못 잡는다 — SQL 은 NULL 끼리 다르다고
보므로 같은 문서를 다시 올릴 때마다 행이 하나씩 늘어난다.

**C. 파생 컬럼으로 정체를 명시한다.** ← 채택

## 결정

`method` 와 `endpoint` 로 식별자를 만들고, 그걸 유니크 키에 쓴다.

```sql
api_key VARCHAR(540)
        AS (CONCAT(COALESCE(method, '-'), ' ',
                   COALESCE(endpoint, CONCAT('#', title)))) STORED,
CONSTRAINT uk_api_definition UNIQUE (device_id, api_version, api_key),
```

`endpoint` 가 NULL 이면 제목으로 물러선다. B 가 못 잡던 자리다.

DB 가 계산하므로 애플리케이션이 채워 넣을 게 없고, `endpoint` 와 어긋날 수가 없다.

upsert 의 갱신 목록도 바꿨다.

```sql
ON DUPLICATE KEY UPDATE
    id = LAST_INSERT_ID(id),
    title = VALUES(title),          -- 표시용 이름이라 다시 뽑으면 나아질 수 있다
    group_name = VALUES(group_name)
    -- method / endpoint 는 갱신하지 않는다. 키가 그 둘에서 나오므로
    -- 충돌했다는 건 이미 같은 값이라는 뜻이다.
```

## 결과

**좋아진 것**

같은 실행(SECUI MFD, 승인 25건) 기준.

| | 전 | 후 |
|---|---|---|
| 적재된 정의 | 18 | **25** |
| 유실된 API | 7 (28%) | **0** |
| 적재 파라미터 | — | 156 |

- 마이그레이션 도구가 없으므로 이미 데이터가 있는 DB 는 아래로 옮긴다.
  기존 행이 새 키를 위반하지 않아 그대로 통과했다.

  ```sql
  ALTER TABLE api_definition
      DROP INDEX uk_api_definition,
      ADD COLUMN api_key VARCHAR(540)
          AS (CONCAT(COALESCE(method, '-'), ' ',
                     COALESCE(endpoint, CONCAT('#', title)))) STORED,
      ADD CONSTRAINT uk_api_definition UNIQUE (device_id, api_version, api_key);
  ```

**나빠진 것 / 감수하기로 한 것**

- **같은 제목의 API 가 목록에서 여럿 보인다.** `4.3.4 ACL 삭제` 가 세 줄이 된다.
  제목이 API 를 특정하지 못한다는 사실이 드러난 것이지 새로 생긴 문제가 아니다.
- **한 추출에서 엔드포인트가 바뀌면 옛 행이 남는다.** 다시 뽑았을 때 NULL 이던
  경로가 채워지면 키가 달라져 새 행이 생기고 앞의 것이 고아로 남는다.
  정체가 바뀌는 것이라 어떤 방식으로도 자동 연결은 안 된다.
- **파라미터 병합은 여전히 없다.** `method` 와 `endpoint` 가 **똑같은** 추출 두 건을
  따로 승인하면 나중 것이 앞의 스키마를 덮어쓴다. 실측 25건은 전부
  `(method, endpoint)` 가 달라 해당 사례가 없었다. 생기면 승인 시점에도
  `_merge_specs` 로 합치는 것이 답이다.
- **파생 컬럼은 순수 파이썬 자체 점검으로 확인할 수 없다.** 정체 규칙이 SQL 로 갔다.
  실제 DB 에 적재해서 확인하는 수밖에 없다.

**되돌릴 신호**

- 같은 엔드포인트를 여러 번 승인하는 일이 실제로 생기면, 그때는 키가 아니라
  **승인 시점 병합**을 붙인다. 키를 되돌리는 게 아니다.
- 엔드포인트가 없는 규격이 다수가 되면 제목 폴백이 사실상의 키가 된다.
  그 지경이면 추출이 잘못된 것이므로 채점 쪽을 먼저 본다.
