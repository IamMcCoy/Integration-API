# opendataloader 가 본문을 통째로 지운 원인

- **날짜:** 2026-08-05
- **결론:** 라이브러리의 반복 머리말/꼬리말 제거 동작. `include_header_footer` 로 제어된다.

## 증상

end-to-end 검증 중 3페이지 PDF 를 올렸는데 청크가 0개 나왔다.

```
Successfully processed .../quarterly.pdf: 0 chunks created
Task succeeded: 245b894a... (0 chunks)
```

직전에 돌린 2페이지 PDF 는 정상이었다(2 청크). 코드는 그 사이 바뀐 게 없었다.

## 가설

1. ~~내 변환/청킹 코드의 버그~~
2. ~~PDF 가 깨졌다~~
3. 라이브러리가 특정 조건에서 텍스트를 버린다

## 방법

변환 결과를 그대로 덤프했다.

```
markdown len=70  chunks=0
--- markdown ---
<!-- PAGE_BREAK: 1 -->

<!-- PAGE_BREAK: 2 -->

<!-- PAGE_BREAK: 3 -->
--- /markdown ---
```

동반 생성된 `.json` 을 보니 `"kids": []` — 라이브러리가 **콘텐츠를 하나도 인식하지 못했다.**
변환기가 실패한 게 아니라, 인식한 뒤 버린 것이다.

정상 케이스와 실패 케이스의 PDF 생성 코드를 나란히 비교했다.

| 케이스 | 페이지별 레이아웃 | 결과 |
|---|---|---|
| `original` | 폰트 크기가 페이지마다 다름 (24 / 18) | 텍스트 추출됨 |
| `loop` | 모든 페이지 동일 (24, 같은 y좌표) | **텍스트 없음** |
| `1page_only` | 반복 자체가 없음 | 텍스트 추출됨 |

→ **모든 페이지에 같은 위치·크기로 반복되는 텍스트가 제거된다**는 가설.

`convert()` 시그니처를 확인하니 해당 플래그가 있었다.

```python
include_header_footer: bool = False
```

직접 검증:

```
include_header_footer=False -> text=NO  ''
include_header_footer=True  -> text=YES '# Section 1\n\nBody text for page 1...'
```

## 결론

라이브러리는 페이지마다 반복되는 텍스트를 머리말/꼬리말로 판정해 기본적으로 제거한다.
실험용 PDF 가 모든 페이지 동일 레이아웃이라 **본문 전체가 머리말로 오인**됐다.

내 코드의 버그가 아니다. 다만 두 가지를 남겼다.

1. **기본값을 유지한다.** 실제 문서에서 "기밀 — 3/20페이지" 같은 반복 잡음이
   제거되는 건 검색 품질에 유리하다. 코드에 주석으로 함정과
   해제 방법을 적어뒀다 (`src/core/converter/PDFConverter.py`).
2. **0청크를 성공으로 처리하던 것을 실패로 바꿨다.** 이게 이 실험의 실질적 수확이다.
   → [ADR-0004](../adr/0004-fail-on-empty-extraction.md)

부수적으로 `format='markdown,json'` 에서 `.json` 을 어디서도 읽지 않는다는 걸
발견해 `format='markdown'` 으로 줄였다.

## 재현

```python
import io, re, opendataloader_pdf
from pathlib import Path
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

buf = io.BytesIO()
c = canvas.Canvas(buf, pagesize=letter)
for i in range(1, 4):                      # 모든 페이지 동일 레이아웃
    c.setFont('Helvetica-Bold', 24); c.drawString(72, 720, f"Section {i}")
    c.setFont('Helvetica', 12);      c.drawString(72, 690, f"Body text for page {i}.")
    c.showPage()
c.save()

for flag in (False, True):
    d = Path(f"/tmp/hf_{flag}"); (d / 'out').mkdir(parents=True, exist_ok=True)
    (d / 'doc.pdf').write_bytes(buf.getvalue())
    opendataloader_pdf.convert(
        input_path=[str(d / 'doc.pdf')], output_dir=str(d / 'out'),
        format='markdown', include_header_footer=flag,
        markdown_page_separator='<!-- PAGE_BREAK: %page-number% -->')
    md = sorted((d / 'out').glob('*.md'))[0].read_text()
    body = re.sub(r'<!--\s*PAGE_BREAK:\s*\d+\s*-->', '', md).strip()
    print(f"include_header_footer={flag} -> {'YES' if body else 'NO'}")
```

검증 환경: `opendataloader-pdf 2.5.0`, JVM 필요.
