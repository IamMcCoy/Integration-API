# 코드 규칙

## 형식

- `flake8` — `max-line-length = 120`, `extend-ignore = E501` (`.flake8`)
- `pre-commit` 훅 사용 (`.pre-commit-config.yaml`)
- 모든 모듈 첫 줄은 `from __future__ import annotations`
- import 는 한 줄에 하나 (`from x import a` / `from x import b`)

## 파일 헤더

```python
"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
```

## 이름

- 파일명은 `PascalCase.py`, 그 안의 주 클래스명과 일치시킨다 (`FileReader.py` → `class FileReader`)
- 벤더 용어를 API 표면에 노출하지 않는다 → [ADR-0005](../adr/0005-vendor-neutral-naming.md)

## 언어

- **주석·docstring: 한국어**
- **에러 메시지·로그·assert 메시지: 영어, 간결하게**

```python
raise ValueError(f"Unsupported file extension: {extension or '(none)'}")
self.logger.info(f"Task queued: {task_id} {file_path}")
assert saved.parent == work, f"Saved outside workspace: {saved}"
```

테스트 데이터로 쓰이는 한글 문자열(`'제목'`, `'안녕하세요'`)은 메시지가 아니므로 예외다.

## 비동기

- 동기 블로킹 호출을 `async def` 안에서 그냥 부르지 않는다. 이벤트 루프가 통째로 멈춘다.

```python
# opendataloader_pdf.convert 는 동기이고 수 초가 걸린다
await asyncio.to_thread(opendataloader_pdf.convert, ...)
```

- 워커 루프는 예외로 죽지 않는다. `CancelledError` 만 다시 던지고 나머지는 로깅 후 계속한다.

## 검증 코드

사소하지 않은 로직(분기, 루프, 파서, 보안 경로)은 **돌아가는 검사 하나**를 남긴다.
프레임워크·픽스처 없이 `if __name__ == '__main__':` 블록의 `assert` 로 충분하다.

```python
if __name__ == '__main__':
    ...
    assert result, 'No chunks produced'
    print(f"OK: {len(result)} chunks")
```

- 자체 점검은 **운영 설정에 의존하지 않는다.** 설정 경로를 쓰는 코드라면
  점검 안에서 `tempfile.mkdtemp()` 등으로 덮어쓴다.
- 실행: `python -m src.core.chunker.MarkdownChunker`

## 주석에 쓰는 것과 안 쓰는 것

**쓰는 것** — 코드만 봐서는 모르는 이유. 알려진 한계와 그때의 대안.

```python
# BRPOP 은 ack 가 없다. 워커가 처리 도중 죽으면 그 태스크는
# RUNNING 인 채로 남는다. 유실이 문제가 되면 Streams + consumer group 으로.
```

**안 쓰는 것** — 실험 결과와 측정 수치. 그건 `docs/experiments/` 와 `docs/adr/` 에 있다.
코드에 옮겨 적으면 값이 바뀔 때 두 곳이 어긋난다.

```python
# 실측으로 3회 모두 적중이 16/24 에서 23/24 로 올랐다.   ← 이런 건 쓰지 않는다
```

## 하지 않는 것

- 구현체가 하나뿐인 인터페이스
- 값이 하나뿐인 설정
- 쓰지 않는 산출물 생성 (예: 읽지도 않는 `.json` 출력)
- 값이 안 변하는 필드 (예: 파이프라인이 하나인데 `TaskType`)
