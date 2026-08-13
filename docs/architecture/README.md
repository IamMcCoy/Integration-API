# 아키텍처

## 계층

각 계층은 자기 아래만 안다. 위를 알지 않는다.

```
src/
  Application.py            앱 조립 · lifespan(워커 기동/종료)
  api/
    Router.py              라우터를 합치는 단일 진입점
    routes/                엔드포인트 정의 (Document / Task / Extraction)
    models/                요청·응답 스키마 (Pydantic)
  core/
    Orchestrator.py        배선. 접수(submit_upload) / 조회(get_task) / 라우팅(process_file)
    task/
      TaskStore.py         작업 상태 + 대기열 (Redis)
      TaskWorker.py        큐 소비 → 파이프라인 실행 → 상태 갱신 → 정리
    reader/FileReader.py   업로드 착지 · 텍스트 읽기 · 검토용 원본 보관/정리
    converter/
      BaseConverter.py     계약: to_markdown(path) -> str
      PDFConverter.py      PDF → 마크다운
    chunker/
      TextSplitter.py      크기 기준 분할 (마크다운도 파일도 모른다)
      MarkdownChunker.py   마크다운 → 청크 (헤더 경로 + 페이지)
    parser/PDFParser.py    포맷별 처리기 (FileParser)
    provider/
      BaseProvider.py      계약: parse(model, instruction, content, schema)
      OpenAIProvider.py    구조화 출력 호출 · 동시성 제한
    extractor/
      SpecModels.py        추출 스키마 (구조화 출력용 Pydantic)
      SpecExtractor.py     청크 선별 + 규격 추출 (무엇을 물을지만 안다)
      SpecScorer.py        근거 대조 채점 (LLM 없음)
      SpecRepository.py    API 단위 병합 · MariaDB 적재 · 승인 이관 (SQLAlchemy Core)
  common/                  ConfigManager(XML+IA_ env) · Constants · LoggerManager
  database/DBPool.py       SQLAlchemy 엔진 (conf/db.properties)
  utils/                   AES256 · Singleton
sql/schema.sql             DDL. 스키마의 유일한 진실
```

핵심은 `MarkdownChunker` 가 PDF 를 **모른다**는 점이다. `opendataloader_pdf` 도,
파일 경로도 import 하지 않는다. 마크다운 문자열만 안다.

`TextSplitter` / `MarkdownChunker` / `SpecScorer` 세 모듈은 **외부 패키지를 하나도
쓰지 않는다.** 프로젝트 의존성이 전혀 깔리지 않은 환경에서도 자체 점검이 통과한다 —
재사용 가능하다는 증거이자, 검증이 환경에 묶이지 않는다는 뜻이다.
→ [ADR-0006](../adr/0006-custom-markdown-splitter.md)

## 데이터 흐름

```
POST /documents
  │
  ├─ Router          크기(헤더) 사전 검사 → ExtractionParams 검증
  ├─ Orchestrator    staging_dir() 생성 → save_upload() → TaskStore.create()
  └─ 202 {task_id}   ◀── 여기서 응답. 처리는 아직 시작 안 함
                          │
                          ▼ (Redis LPUSH)
                     ┌─────────────┐
                     │ ia:task:queue│
                     └─────────────┘
                          │ (BRPOP)
                          ▼
  TaskWorker         payload() → status=RUNNING
    │                Orchestrator.process_file()
    │                  └─ PDFConverter.to_markdown() → MarkdownChunker.chunk()
    │                빈 결과면 FAILED
    │
    │                filtering    SpecExtractor.select()    청크마다 LLM 판정
    │                extracting   SpecExtractor.extract()   규격 + 근거 문자열
    │                persisting   SpecRepository.save()
    │                               ├─ _merge_by_api()      헤더 경로로 묶고
    │                               │                       엔드포인트로 다시 쪼갠다
    │                               ├─ SpecScorer.score()   근거를 원문과 대조
    │                               └─ api_extraction 적재
    │                                    └─ 90점 이상만 api_definition + api_schema
    │
    │                FileReader.retain()        업로드 원본을 review/ 로 반출
    │                FileReader.sweep_retained() 오래된 보관본 정리
    │                status=SUCCEEDED + ExtractionResult
    └─ finally       FileReader.cleanup(work_dir)

GET  /tasks/{task_id}            →  TaskStore.get()               →  TaskDetail
GET  /extractions                →  SpecRepository.list_by_status →  검토 대기열
GET  /extractions/{id}/source    →  FileReader.retained()         →  원본 문서 (inline)
POST /extractions/{id}/approve   →  SpecRepository.promote()      →  운영 테이블로 이관
POST /extractions/{id}/reject    →  SpecRepository.reject()       →  상태만 변경
```

승인은 본문에 고친 규격을 실을 수 있다. 그 경우 저장된 것을 대체하고 **원문과 다시
대조해 채점한다** — 사람 손을 거쳤다고 점수를 면제하지 않는다. 점수가 낮아도 승인은
막지 않되, 그 점수가 `reviewed_at` 옆에 남는다. → [api.md](../design/api.md)

워커는 `worker_count`(기본 4) 만큼 뜬다. `SpecExtractor` 와 `SpecRepository` 는
워커들이 **공유한다** — 각자 만들면 OpenAI 동시 요청 제한과 DB 커넥션 풀이
워커 수만큼 배로 풀린다.

상태가 두 종류인 것을 헷갈리지 말 것.

| | 무엇 | 어디 | 수명 |
|---|---|---|---|
| `TaskStatus` / `TaskStage` | 업로드 작업이 어디까지 갔나 | Redis | `task_ttl_seconds` |
| `review_status` | 이 추출 결과를 믿을 수 있나 | MariaDB | 영구 |

작업이 `SUCCEEDED` 여도 그 안의 추출은 `approved` / `pending` / `rejected` 로 갈린다.

**BRPOP 의 블록 시간보다 소켓 읽기 제한이 길어야 한다** (`TaskStore.socket_timeout()`).
같으면 서버가 응답하는 시점과 클라이언트가 포기하는 시점이 경합해, 블록이 끝나기 직전에
들어온 태스크가 큐에서 빠진 채 사라진다. redis-py 8 의 기본값이 5초라 기본 설정과
정확히 맞부딪힌다.

## 확장 지점

| 하고 싶은 것 | 손대는 곳 |
|---|---|
| 새 문서 포맷(DOCX 등) 추가 | `BaseConverter` 상속 1파일 + `Orchestrator.process_file` 분기 1줄 |
| 페이지 메타데이터가 있는 포맷 | 마크다운에 `PAGE_BREAK` 마커만 심으면 청커가 알아서 옮긴다 |
| 파이프라인 단계 추가 | `TaskStage` 에 값 추가 + `TaskWorker._handle` 에 단계 삽입 |
| 새 엔드포인트 | `api/routes/` 에 파일 1개 + `api/Router.py` 에 2줄 |
| 추출 스키마 변경 | `SpecModels.py` 수정 (구조화 출력 스키마가 그대로 따라간다) |
| 채점 규칙 변경 | `SpecScorer.py` — LLM 을 부르지 않으므로 자체 점검으로 바로 확인된다 |
| LLM 엔드포인트 교체 (사내 vLLM 등) | `BaseProvider` 상속 1파일 + `SpecExtractor(provider=...)` |

## 배포

- 워커는 API 와 **같은 프로세스**에서 `worker_count` 개 돈다
  (`lifespan` 에서 `asyncio.create_task`). 파드를 늘리면 워커도 같이 늘어난다.
  큐가 Redis 라 같은 작업을 둘이 집지 않는다.
- **`OPENAI_API_KEY` 는 환경변수로 준다.** `conf/ia-conf.xml` 은 ConfigMap 으로
  마운트되므로 비밀을 담지 않는다. 모델명·동시성·`base_url`·`temperature`·**프롬프트**가
  설정 파일에 있어서, 문구만 고칠 때는 ConfigMap 을 갈아끼우고 파드를 재시작하면 된다.
- **`IA_` 접두사 환경변수가 XML 설정을 덮어쓴다** (`ConfigManager.get`).
  `IA_REDIS_HOST` 는 `redis_host` 를 이긴다. 로컬은 `.env`(`.env.example` 참고),
  운영은 환경변수를 직접 주입한다 — ConfigMap 을 갈아끼우지 않고 값 하나만 바꿀 수 있다.
- **MariaDB 접속 정보는 `conf/db.properties` 에 따로 있다.** 설정 경로가 두 갈래로
  갈라져 있는데(신규 파이프라인은 `ia-conf.xml`), 자격증명 AES256 복호화가 얽혀 있어
  이번에는 통일하지 않았다.
- 스키마는 `sql/schema.sql` 을 직접 적용한다. 마이그레이션 도구는 없다.
- 매니페스트와 적용 순서는 [`k8s/README.md`](../../k8s/README.md). 네임스페이스는
  `integration-api` 하나이고, Service 의 `selector` 는 네임스페이스를 넘지 못하므로
  전부 같아야 한다.
- **`upload_staging_dir` 은 레플리카가 2개 이상이면 반드시 ReadWriteMany 공유 볼륨이어야 한다.**
  POST 를 받은 파드와 처리하는 워커 파드가 다를 수 있다. 로컬 디스크면 워커가 파일을 못 찾는다.
  → [ADR-0002](../adr/0002-task-scoped-staging-directory.md)
- **그 볼륨은 `review/` 하위에 업로드 원본을 계속 들고 있다.** 승인 화면이 원문 페이지를
  열어 보는 데 쓴다. `review_retention_days` 가 지났고 그 태스크에 `pending` 이 없으면
  작업이 끝날 때마다 정리된다. 컨테이너 안 마운트 경로(`upload_staging_dir`)와 그 볼륨이
  호스트 어디서 오는지(`ia-deploy.yaml` 의 `hostPath`)는 **다른 층이다** — 앱은 앞의 것만 안다.
- 설정은 `conf/ia-conf.xml` → `ConfigManager` → `Constants` 순으로 읽힌다.
