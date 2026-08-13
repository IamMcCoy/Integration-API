# ADR-0002: 업로드 파일 수명을 작업에 묶는다

- **상태:** Accepted
- **날짜:** 2026-08-05

## 배경

처음에는 업로드를 `tempfile.TemporaryDirectory()` 컨텍스트로 감쌌다.

```python
with FileReader.workspace() as work_dir:
    file_path = await FileReader.save_upload(upload, work_dir)
    return await self.process_file(file_path, ...)
# 여기서 원본·산출물 전부 소멸
```

동기 처리라면 이게 맞다. 그런데 API 를 **작업 큐** 방식으로 바꾸면서 깨졌다.
POST 핸들러가 `task_id` 만 주고 즉시 반환하므로, 그 시점에 파일이 지워지면
워커가 처리할 게 없다.

같은 이유로 이전 프로젝트 코드의 `finally: os.unlink(temp_file_path)` 패턴도 못 쓴다.

여기에 더해, Redis 큐 + 레플리카 N개 구성에서는 **POST 를 받은 파드와
작업을 꺼내는 워커 파드가 다를 수 있다.**

## 선택지

**A. 작업 수명에 묶고 위치를 설정으로 뺀다**
`staging_dir()` 로 격리 디렉토리를 만들고, 워커가 `finally` 에서 `cleanup()`.
경로는 `upload_staging_dir` 설정값.

**B. 파일 바이트를 Redis 에 넣는다**
파드 간 공유는 해결되지만 20MB 파일을 큐에 싣는 건 낭비다.

**C. 오브젝트 스토리지(S3/MinIO)**
가장 견고하지만 새 인프라가 필요하다.

**D. 작업을 받은 파드에 고정**
큐를 쓰는 의미가 사라진다.

## 결정

**A** 를 택했다.

```
Orchestrator.submit_upload()  →  FileReader.staging_dir()  생성
TaskWorker._handle()  finally  →  FileReader.cleanup()     삭제
```

접수 도중 실패하면 작업이 만들어지지 않아 정리할 주체가 없으므로,
`submit_upload` 이 직접 지운다.

`PDFConverter` 가 산출물을 원본 파일 옆(`parent/stem`)에 만들기 때문에,
격리 디렉토리 안에 원본을 두면 `.md`/`.json` 도 함께 정리된다.
**컨버터는 한 줄도 고치지 않았다.**

## 결과

- 동시 업로드 경로 충돌이 구조적으로 불가능해졌다. 이전에는 같은 이름의 파일 둘이
  동시에 오면 `output_dir` 이 겹쳐 한쪽이 다른 쪽을 덮어썼다.
- 산출물 누적(디스크 누수)도 사라졌다. 이전 코드는 아무도 지우지 않았다.
- **감수한 것:** 레플리카가 2개 이상이면 `upload_staging_dir` 이 반드시
  **ReadWriteMany 공유 볼륨**이어야 한다. 로컬 디스크면 워커가 파일을 못 찾는다.
  `conf/ia-conf.xml` 과 `FileReader.staging_dir` docstring 에 명시했다.
- **되돌릴 신호:** 공유 볼륨 운영이 부담이 되거나 파일이 커지면 C(오브젝트 스토리지)로.

## 함께 넣은 검증

업로드는 신뢰 경계다. `FileReader` 에 함께 넣었다.

| 위험 | 처리 |
|---|---|
| path traversal (`../`, `C:\`, 절대경로) | basename 만 취함, `.`/`..`/NUL 거부 |
| 확장자 위조 | 허용 목록 |
| 대용량 → 메모리 폭발 | 1MB 청크 스트리밍 복사 |
| 디스크 고갈 | `upload_max_bytes` 상한 |

posix 에서 백슬래시는 경로 구분자가 아니라 `Path().name` 이 못 걸러낸다.
`C:\windows\evil.pdf` 를 막으려면 먼저 `\` → `/` 로 정규화해야 한다.
