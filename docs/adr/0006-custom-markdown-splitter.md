# ADR-0006: 마크다운 스플리터를 직접 구현한다

- **상태:** Accepted
- **날짜:** 2026-08-06

## 배경

청킹은 `langchain-text-splitters` 의 `MarkdownHeaderTextSplitter` 와
`RecursiveCharacterTextSplitter` 두 개를 썼다. 필요한 건 그 함수 둘뿐이었는데,
`poetry.lock` 을 교차 대조해 보니 그 둘 때문에 딸려 온 패키지가 **18개**였다.

```
langchain-text-splitters → langchain-core → langsmith → requests, requests-toolbelt,
                                                        zstandard, xxhash, orjson,
                                                        websockets, uuid-utils
                                        → jsonpatch → jsonpointer
                                        → tenacity, pyyaml, packaging
```

전체 48개 중 18개다. 문자열을 자르려고 LangSmith 텔레메트리 SDK 와 HTTP 클라이언트
스택(`requests`)이 들어와 있었다 — 이미 `httpx` 가 있는데도.

여기에 품질 문제가 겹쳤다. 대상 문서는 벤더 REST API 가이드이고 파라미터 정의가
대부분 마크다운 표인데, `RecursiveCharacterTextSplitter` 는 `chunk_size` 를 넘는 표를
`\n` 기준으로 자른다. 잘린 뒷조각에는 컬럼 헤더가 없어서, 그 조각을 받은 LLM 은
어느 열이 타입이고 어느 열이 설명인지 알 수 없다.

## 선택지

**A. 그대로 둔다.** 이미 동작하고 자체 점검도 붙어 있다. 표 분할 문제는 `chunk_size` 를
키워서 완화한다. 비용 0.

**B. 직접 구현한다.** 헤더 분할과 크기 분할을 각각 쓴다. 의존성 18개가 빠지고,
이 도메인에 필요한 규칙(코드 펜스 무시, 표 헤더 재부착)을 넣을 수 있다.
분할 품질이 회귀할 위험을 진다.

**C. 다른 경량 스플리터 라이브러리로 갈아탄다.** 의존성 수는 줄지만 여전히 남의 규칙이라
표 헤더 재부착 같은 도메인 규칙은 못 넣는다.

## 결정

**B.** 두 가지를 함께 얻기 때문이다.

의존성만 보면 A 도 방어 가능하다 — 동작하는 코드를 지우는 건 손실이다. 하지만
표 헤더 문제는 `chunk_size` 를 키워서 해결되지 않는다. 문서마다 표 크기가 다르고,
`chunk_size` 를 키우면 LLM 에 넘기는 토큰이 늘어 필터링/추출 비용이 그만큼 오른다.
도메인 규칙이 필요한 이상 어차피 우리 코드를 써야 하고, 그러면 라이브러리를 남길
이유가 없다.

구현은 두 파일이다.

- `src/core/chunker/TextSplitter.py` — 크기 기준 분할. 마크다운도 파일도 모른다.
  구분자 우선순위(`\n\n` → `\n` → ` ` → 문자)를 재귀로 시도하고 경계마다 오버랩을 남긴다.
- `src/core/chunker/MarkdownChunker.py` — 헤더 분할 + 페이지 마커 + 표 헤더 재부착.

공개 시그니처와 청크 dict 구조(`chunk_id` / `text` / `metadata`)는 바꾸지 않았다.

## 결과

**좋아진 것**

- 의존성 48개 → 30개. Docker 이미지가 가벼워진다.
- `TextSplitter` / `MarkdownChunker` 의 자체 점검이 **표준 라이브러리만으로 돈다.**
  프로젝트 의존성이 하나도 깔리지 않은 환경에서도 `python -m src.core.chunker.MarkdownChunker`
  가 통과한다. `code-style.md` 의 "자체 점검은 운영 설정에 의존하지 않는다" 가
  의존성까지 확장됐다.
- 코드 펜스 안의 `#` 을 헤더로 오인하지 않는다. API 가이드에는 `# curl ...` 예제가 흔하다.
- `chunk_size` 를 넘는 표가 잘려도 조각마다 컬럼 헤더가 다시 붙는다.

**나빠진 것 / 감수하기로 한 것**

- 분할 품질을 지키는 건 이제 우리 몫이다. 자체 점검 assert 말고는 안전망이 없다.
- langchain 의 오랜 예외 처리 경험이 사라졌다. 이상한 마크다운에서 우리 구현이
  어떻게 깨지는지는 실제 문서 18개를 돌려봐야 안다.
- 구분자를 다 소진해도 `chunk_size` 를 넘는 조각은 그대로 내보낸다
  (`TextSplitter._split` 의 주석). 상한 초과를 감수한다.

**되돌려야 할 신호**

- 분할 규칙이 계속 늘어나 `TextSplitter` 와 `MarkdownChunker` 합계가 200줄을 넘으면
  라이브러리로 돌아간다. 그 시점엔 우리가 유지하는 비용이 의존성 18개보다 비싸다.
- 마크다운이 아닌 포맷을 여럿 다루게 되면 마찬가지다.

## 부록: 함께 정리한 것

**`.json` / `.jsonl` / `.csv` 지원을 걷어냈다.** `Orchestrator.process_file` 의 분기
3개와 `FileParser` 의 대응 함수 3개를 지웠다. 남은 건 `.pdf` 와 `.txt` 다.

이 경로들은 청크가 아니라 **레코드 dict 를 그대로 반환**하고 있었다 — `text` 키도
`metadata` 키도 없다. 지금까지 드러나지 않은 건 `TaskWorker` 가 `len(chunks)` 만
썼기 때문이고, 선별 단계가 `chunk['text']` 를 읽는 순간 `KeyError` 가 된다.

고칠 수도 있었지만 되살릴 이유가 없었다. ADR-0005 가 "범용 문서 처리기가 아니다"
라고 정체성을 못박았고, `data/` 의 대상 문서 18개는 전부 PDF 다. CSV 에서 REST API
규격을 뽑는 유스케이스는 없다.

대신 `Orchestrator.process_file` docstring 에 계약을 명시했다 — 어떤 처리기든
`{chunk_id, text, metadata}` 를 돌려줘야 한다. `FileParser.process_txt_file` 의
`metadata` 도 빈 dict 에서 PDF 경로와 같은 `{h1, h2, h3, page_number}` 구조로 맞췄다.

**되돌릴 신호:** API 규격이 PDF 가 아닌 형태(OpenAPI JSON 등)로 오는 일이 생기면
다시 연다. 그때는 레코드 dict 를 그대로 흘리지 말고 청크 구조로 변환해서 넣는다.
