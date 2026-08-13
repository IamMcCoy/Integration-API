# ADR-0001: 파서를 읽기·변환·청킹 계층으로 분리

- **상태:** Accepted
- **날짜:** 2026-08-05

## 배경

`src/core/parser/PDFParser.py` 한 파일에 `FileParser` 클래스가 있었고,
JSON/JSONL/CSV/TXT/PDF 다섯 포맷 처리가 전부 들어 있었다.
파일명은 PDF 인데 클래스는 전체 포맷을 다뤘다 — 경계가 잘못 잡혔다는 신호였다.

PDF 흐름은 실제로 두 덩어리였다.

1. **추출** — `opendataloader_pdf.convert` (바이너리 PDF → 마크다운)
2. **가공** — 읽기 → 전처리 → 헤더/재귀 청킹 + 페이지 메타데이터

2번의 세 메서드(`_read_markdown_file`, `_preprocess_markdown`,
`_chunk_markdown_with_page`)는 PDF 를 전혀 몰랐다. 마크다운 문자열만 알면 됐다.

제약 하나가 설계를 결정했다: **`opendataloader_pdf.convert` 는 디스크 경로만 받는다.**
메모리 스트림을 넘길 수 없다.

## 선택지

**A. 계약을 결과물로 잡는다 — `to_markdown(path) -> str`**
읽기를 언제 어떻게 하든 구현체 내부 사정으로 둔다.

**B. 3단계 파이프라인을 강제한다 — read → convert → chunk**
일관돼 보이지만 PDF 가 어긋난다. PDF 는 변환이 먼저고 읽기가 나중이다.
TXT 는 반대로 읽기가 곧 변환이다.

**C. 청킹만 분리한다**
가장 작은 diff. 하지만 전처리가 PDF 와 무관하게 남아 경계가 애매해진다.

## 결정

**A** 를 택했다.

```
src/core/
  reader/FileReader.py         파일읽기
  converter/
    BaseConverter.py           계약: to_markdown(path) -> str + 공통 정제
    PDFConverter.py            PDF → 마크다운
  chunker/MarkdownChunker.py   마크다운 → 청크
  parser/PDFParser.py          포맷별 처리기 (오케스트레이션만 남음)
```

전처리(이미지 제거, 빈 줄 정리)는 **컨버터**에 뒀다. "마크다운 산출물을 깨끗한 상태로
내놓는 것"까지가 변환의 책임이라고 보면, 청커는 순수하게 문자열→청크만 하게 되어
재사용성이 올라간다.

페이지 정보는 마커로 옮긴다. `PDFConverter` 가 마크다운에
`<!-- PAGE_BREAK: n -->` 를 심고, 청커는 "마커가 있으면 읽는다" 만 안다.
청커가 PDF 를 알 필요가 없어지고, 마커를 심을 수 있는 포맷은 뭐든
페이지 메타데이터를 공짜로 얻는다.

## 결과

- `process_pdf_file` 이 "컨버터 부르고 청커 부르고 끝" 인 배선만 남았다.
- `MarkdownChunker` 가 `opendataloader_pdf` 도 파일 경로도 import 하지 않는다.
  langchain 만 설치된 빈 환경에서 단독 실행된다.
- 포맷 추가 비용: 컨버터 1파일 + `Orchestrator.process_file` 분기 1줄.
- **감수한 것:** `BaseConverter` 는 당분간 구현체가 하나(`PDFConverter`)다.
  확장이 명시적 요구사항이라 계약을 문서화하는 값이 있다고 판단했다.
- **되돌릴 신호:** 1년이 지나도 컨버터가 하나뿐이면 `BaseConverter` 는 지운다.

## 함께 고친 것

옮기는 과정에서 드러난 결함들.

- `opendataloader_pdf.convert` 가 `async def` 안에서 동기 호출되고 있었다.
  단일 이벤트 루프에서 PDF 한 건 변환하는 수 초 동안 **모든 요청이 멈춘다.**
  `asyncio.to_thread` 로 감쌌다.
- `md_files[0]` 이 빈 리스트면 `IndexError` 였다 → 명시적 `ValueError`.
- `glob` 순서는 OS 의존이라 `sorted` 로 고정했다.
- `chunk_id` 가 빈 청크를 건너뛰며 번호에 구멍이 생겼다 → 연속 번호로.
