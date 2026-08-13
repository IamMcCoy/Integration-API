# 파이프라인 설계

## 단계

`TaskStage` 의 값은 문서 하나가 실제로 지나가는 경로와 1:1 로 맞춘다.
진행률을 퍼센트로 꾸미지 않는다 — 단계로 알린다.

| 단계 | 하는 일 | 담당 | 상태 |
|---|---|---|---|
| `staged` | 업로드가 작업 디렉토리에 착지, 처리 대기 | `FileReader` | 구현됨 |
| `parsing` | PDF → 마크다운 | `PDFConverter` | 구현됨 |
| `chunking` | 마크다운 → 청크 (+ 헤더 경로 · 페이지) | `MarkdownChunker` | 구현됨 |
| `filtering` | API 관련 청크만 선별 | `SpecExtractor.select` | 구현됨 |
| `extracting` | 청크 → API 규격(요청 파라미터, 타입) | `SpecExtractor.extract` | 구현됨 |
| `persisting` | 규격 채점 → MariaDB 적재 | `SpecScorer` + `SpecRepository` | 구현됨 |
| `done` | 완료 | | |

각 단계에서 `stage` 를 바꾸고 `processed` 를 올리면 진행률이 그대로 노출된다.
배선은 전부 `TaskWorker._handle` 의 `try` 블록 안에 있다 — `finally` 의
`cleanup` 이 걸려 있어 여기를 벗어나면 작업 디렉토리가 먼저 지워진다.

## 신뢰도 채점과 사람 승인

LLM 추출에는 환각이 섞인다. 그래서 추출 결과는 곧바로 운영 테이블로 가지 않고
`api_extraction` 스테이징에 먼저 쌓인다.

파라미터마다 `evidence` — 원문에서 글자 그대로 오려낸 줄 — 를 함께 받고,
그 문자열이 실제 청크 안에 있는지 대조한다. LLM 을 다시 부르지 않는다.

```
엔드포인트 경로가 원문에 없다        →   0점 (지어낸 것)
파라미터가 없고 엔드포인트는 근거 있음 → 100점 (인자 없는 액션 API)
그 외                              →   evidence 가 실재하는 파라미터 수 / 전체 × 100
```

**엔드포인트를 먼저 본다.** 파라미터가 틀리면 인자 하나가 잘못되지만 엔드포인트가
틀리면 API 자체가 존재하지 않는다. 실측에서 모델이 `/acl/view`, `/api/protectDomain`
같은 경로를 지어냈고, 파라미터 근거만 맞으면 100점으로 자동 적재되고 있었다.

`'None'` / `'null'` 처럼 값 없음을 문자열로 뱉는 것도 여기서 걸러 컬럼에 안 넣는다.

## 같은 API 의 조각을 합친다

한 API 가 여러 청크에 걸치면(실측 31%) 추출이 청크마다 돌아 조각난 규격이 여러 개 나온다.
`api_schema` 는 `UNIQUE(api_def_id)` 라 나중 것이 앞의 것을 덮어써 파라미터가 사라진다.

그래서 `SpecRepository.save` 가 적재 직전에 합친다.

```
청크의 헤더 경로로 묶는다        ← LLM 이 낸 title 이 아니라 문서 구조
  └ 근거 있는 엔드포인트별로 다시 나눈다
      └ 파라미터를 이름 기준으로 합집합
```

병합 키를 `title` 로 하지 않는 이유: 같은 API 라도 조각마다 `ACL 추가` /
`ACL 추가 응답` 처럼 다르게 나온다. 헤더 경로는 문서 구조에서 오므로 반드시 일치한다.

엔드포인트별로 한 번 더 나누는 이유: 문서가 `5.2.4 적용/취소` 처럼 한 헤더에 API 둘을
묶어 쓴다. 그룹당 엔드포인트를 하나만 고르면 나머지를 잃는다.
→ [ADR-0010](../adr/0010-extraction-verification-and-merge.md)

### 나눠 놓은 것을 DB 가 다시 붙이지 않게

여기서 갈라놓은 apply 와 cancel 은 **제목이 같다.** 헤더 경로에서 온 이름이라 그렇다.
`api_definition` 의 유니크 키가 제목을 쓰면 둘이 한 행으로 뭉개져 병합 작업이 무효가 된다.

그래서 정체는 `method` + `endpoint` 로 만든다. `endpoint` 가 NULL 이면 제목으로 물러선다.

```sql
api_key VARCHAR(540)
        AS (CONCAT(COALESCE(method, '-'), ' ',
                   COALESCE(endpoint, CONCAT('#', title)))) STORED
```

→ [ADR-0012](../adr/0012-api-identity-is-the-endpoint.md)

| 점수 | `review_status` | 동작 |
|---|---|---|
| ≥ `score_auto_threshold` (기본 90) | `approved` | `api_definition` + `api_schema` 즉시 삽입 |
| ~ `score_review_threshold` (기본 70) | `pending` | 스테이징에만 적재, 사람 승인 대기 |
| 미만 | `rejected` | 스테이징에만 적재, 사람이 보고 판단 |

대기열은 `GET /extractions?status=pending` 으로 보고
`POST /extractions/{id}/approve` 로 넘긴다.
→ [ADR-0007](../adr/0007-evidence-based-confidence-scoring.md)

`api_extraction.source_text` 에 청크 원문을 복사해 두는 이유는 아래
[작업 디렉토리 수명](#작업-디렉토리-수명) 때문이다 — 작업이 끝나면 원본이 지워져서
승인 화면에서 대조할 근거가 남지 않는다.

## 적재 대상은 누가 정하나

`vendor` / `device` / `api_version` 은 업로드 폼에서 받는다. 사람이 파일을 고를 때
이미 아는 정보이고, LLM 이 틀릴 여지가 없다.

비워 두면 문서 앞부분 5개 청크를 LLM 에 넘겨 채운다(`SpecExtractor.identify`).
표기 요동(SECUI/시큐아이, MF2/MFD)으로 같은 벤더가 둘로 갈릴 수 있으므로
폼 입력이 있으면 항상 그쪽이 이긴다. 둘 다 비면 `document_name` 으로 떨어뜨린다 —
DB 가 NOT NULL 이라 빈 문자열이면 적재 자체가 실패한다.

## 동시 처리

워커 하나는 태스크 하나를 잡고 끝까지 간다. LLM 호출이 붙어 태스크당 수 분이
걸리므로, 워커가 하나면 문서 하나가 뒤를 전부 막는다. `worker_count`(기본 4) 만큼
같은 프로세스에서 띄운다. 큐가 Redis 라 같은 태스크를 둘이 집는 일은 없다.

`SpecExtractor` 와 `SpecRepository` 는 **워커들이 공유한다.** 각자 만들면 OpenAI
동시 요청 제한(`openai_max_concurrency`)과 DB 커넥션 풀이 워커 수만큼 배로 풀려
레이트 리밋에 걸린다.

태스크 하나 안에서도 청크들은 `asyncio.gather` 로 동시에 나간다. 즉 동시성이
두 층이고, 바깥층은 `worker_count`, 안쪽층은 `openai_max_concurrency` 가 묶는다.

## 단계별 모델

| 단계 | 설정 | 왜 |
|---|---|---|
| `filtering` | `openai_filter_model` | 청크마다 부르므로 호출 수가 가장 많다. 판정은 참/거짓 하나뿐이라 싼 모델로 충분하다 |
| `extracting` | `openai_extract_model` | 근거 문자열을 원문 그대로 옮겨야 한다. 여기서 아끼면 채점에서 깎여 사람 검토가 늘어난다 |
| 대상 식별 | `openai_extract_model` | 문서당 한 번뿐이고 마스터 테이블에 들어갈 값이다 |

선별이 틀리면 두 방향으로 손해가 다르다. 규격 청크를 버리면(false negative) 그 API 는
아예 적재되지 않고 아무도 모른다. 규격이 아닌 청크를 통과시키면(false positive)
추출 단계에서 근거를 못 만들어 점수가 깎이고 반려된다 — 비용만 조금 더 쓴다.
그래서 프롬프트는 애매하면 통과시키는 쪽으로 기울여 두었다.

## 청크를 어디서 끊는가

헤더 h1~h6 를 **전부** 경계로 본다. 대상 문서는 개별 API 이름이 h5 에, 요청/응답 구분
(`URL`, `Query parameters`, `Body`)이 h6 에 있어서, 상위 세 레벨만 보면 청크가 어느 API
것인지 알 수 없다. 실측으로 AhnLab DPX 는 124,772자가 h1~h3 기준 7개 섹션밖에 안 된다.

다만 경계마다 끊지는 않는다. `chunk_size` 에 못 미치는 인접 섹션은 다시 이어 붙인다 —
`URL` 한 줄짜리 섹션이 따로 청크가 되면 파라미터 표만 맥락 없이 남고, 선별 단계가
청크마다 LLM 을 부르므로 비용도 그만큼 늘어난다.

```
헤더로 분할(h1~h6)  →  작은 섹션 병합  →  남은 큰 덩어리를 TextSplitter 로 분할
```

청크 하나는 이렇게 생겼다.

```python
{
    'chunk_id': 7,
    'text': '...',
    'metadata': {
        'headings': ['4. System', '4.1 시스템 정보', '4.1.1 시스템 정보 조회', 'Query parameters'],
        'page_number': 23,
    },
}
```

`headings` 는 h1 부터 순서대로 쌓인 실제 경로다 — 빈 레벨은 들어 있지 않다.
추출 단계가 이걸 `[문서 위치] 4. System > 4.1.1 시스템 정보 조회 > Query parameters` 로
본문 앞에 붙여 LLM 에 넘긴다.
→ [ADR-0008](../adr/0008-header-level-and-section-merge.md)

`chunk_size` 를 넘는 마크다운 표가 잘리면 조각마다 컬럼 헤더 2행을 다시 붙인다.
헤더가 없으면 어느 열이 타입이고 어느 열이 설명인지 알 수 없다.

## 왜 `to_markdown(path)` 인가

세 단계(읽기 → 변환 → 청킹)를 파이프라인으로 **강제하면 PDF 가 어긋난다.**
`opendataloader_pdf.convert` 는 경로를 받아 `.md` 파일을 뱉으므로,
읽기가 변환 *뒤에* 온다. TXT 는 반대로 읽기가 곧 변환이다.

그래서 계약을 "단계 순서" 가 아니라 **결과물**로 잡았다:

```python
async def to_markdown(self, file_path: str) -> str
```

읽기를 언제 어떻게 하든 구현체 내부 사정으로 둔다.
→ [ADR-0001](../adr/0001-layered-parser-separation.md)

## 페이지 번호를 옮기는 방법

청커가 PDF 를 알지 않으면서 페이지 정보를 유지해야 한다. 마커로 푼다.

1. `PDFConverter` 가 변환 시 `markdown_page_separator='<!-- PAGE_BREAK: %page-number% -->'` 를 넘긴다
2. `MarkdownChunker` 는 "마커가 있으면 읽는다" 만 안다
3. 본문에서는 마커를 지우고 `metadata.page_number` 로 옮긴다

마커를 심을 수 있는 포맷은 뭐든 페이지 메타데이터를 공짜로 얻는다.
마커가 없는 포맷은 전부 1페이지로 취급된다.

**알려진 한계:** 한 청크가 페이지 경계를 걸치면 뒷부분이 이전 페이지로 기록된다
(청크 시작 시점의 페이지를 그 청크의 페이지로 본다). 코드에 주석으로
표시돼 있고, 정확한 페이지별 분할이 필요해지면 마커 기준으로 먼저 자르면 된다.

## 빈 결과는 실패다

추출 결과가 0인데 `SUCCEEDED` 로 끝나면 호출자는 초록불을 받고
아무것도 적재되지 않은 상태를 갖게 된다. `TaskWorker` 에서 막는다.

가드를 컨버터가 아니라 **워커**에 둔 이유는, 거기가 모든 포맷(PDF/TXT)이
지나가는 한 지점이기 때문이다. 한 줄로 전 경로가 막힌다.
→ [ADR-0004](../adr/0004-fail-on-empty-extraction.md)

## 작업 디렉토리 수명

업로드 파일의 수명은 *요청* 이 아니라 **작업**에 묶인다.
POST 핸들러는 `task_id` 만 주고 즉시 반환하므로, 그 시점에 파일이 지워지면
워커가 처리할 게 없다.

```
Orchestrator.submit_upload()  →  FileReader.staging_dir()  생성
TaskWorker._handle()  적재 후  →  FileReader.retain()      원본만 반출
TaskWorker._handle()  finally  →  FileReader.cleanup()     나머지 삭제
```

접수 도중 실패하면 작업이 만들어지지 않아 정리할 주체가 없으므로,
`submit_upload` 이 직접 지운다.

### 원본 보관

규격이 하나라도 적재됐으면 업로드 원본을 `{staging}/review/{task_id}{확장자}` 로 옮긴다.
`cleanup()` 이 지우는 작업 디렉토리 바깥이라 살아남는다.

승인 화면이 `GET /extractions/{id}/source#page=n` 으로 이 파일을 연다. 검토자는
`source_text` 만으로는 부족할 때가 있다 — 청크 하나뿐이라 표의 머리말이 잘려 있거나
앞 절의 전제가 빠져 있다.

복사가 아니라 이동이다. 어차피 지워질 파일을 두 벌 들고 있을 이유가 없다.

보관에 실패해도 작업은 성공으로 끝난다 — 링크가 없을 뿐 적재된 규격은 멀쩡하고,
`source_text` 로 대조는 여전히 가능하다.

### 오래된 보관본 정리

`FileReader.sweep_retained()` 가 **작업이 끝날 때마다** 한 번 돈다. 파일이 늘어나는
순간이 곧 정리할 순간이라 별도의 스케줄러가 필요 없다.

```
review_retention_days 가 지났다        ┐
그 태스크에 pending 인 추출이 없다      ┘ 둘 다 만족해야 지운다
```

`pending` 이 남아 있으면 나이와 무관하게 보존한다 — 승인 화면이 대조해 보여 줄 근거가
그 파일뿐이다. 보호 목록은 `SpecRepository.pending_task_ids()` 가 준다.

`review_retention_days` 를 `0` 으로 두면 아무것도 지우지 않는다. 정리에 실패해도
작업은 성공으로 끝난다 — 다음 작업이 또 턴다.

**작업 디렉토리 쪽은 정리 장치가 없다.** `cleanup()` 이 `finally` 에 있어 프로세스가
통째로 죽으면 실행되지 않고, 기동 시 잔여물을 쓸어내는 코드도 없다. 크래시가 반복되면
`{staging}/{uuid}/` 가 쌓인다.
