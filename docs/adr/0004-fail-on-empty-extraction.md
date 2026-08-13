# ADR-0004: 추출 결과가 비면 실패로 처리한다

- **상태:** Accepted
- **날짜:** 2026-08-05

## 배경

end-to-end 검증 중 3페이지 PDF 를 올렸는데 청크가 **0개** 나왔다.
그런데 작업 상태는 `SUCCEEDED` 였고 `chunk_count: 0` 이었다.

원인 자체는 라이브러리 정상 동작이었다
(→ [실험: opendataloader 머리말/꼬리말 제거](../experiments/2026-08-05-opendataloader-header-footer.md)).
문제는 **그것이 성공으로 보고됐다**는 점이다.

호출자는 초록불을 받고, 아무것도 적재되지 않은 상태를 갖게 된다.
실패는 조용한 것보다 시끄러운 게 낫다.

## 선택지

**A. 워커에서 막는다**
`process_file` 결과가 비면 `ValueError` 를 던져 `FAILED` 로 기록.

**B. 컨버터에서 막는다**
`PDFConverter.to_markdown` 이 빈 마크다운이면 예외.

**C. 그대로 두고 응답에 경고 필드를 추가한다**
호출자가 그 필드를 볼 거라는 낙관에 기댄다.

## 결정

**A** 를 택했다.

```python
if not chunks:
    raise ValueError('No content extracted from file')
```

B 는 PDF 경로만 막는다. TXT·CSV·JSON 도 똑같이 빈 결과가 나올 수 있고,
그때마다 같은 가드를 복붙해야 한다.

**워커는 모든 포맷이 지나가는 한 지점**이다. 한 줄로 전 경로가 막힌다.
증상이 아니라 공통 지점을 고치는 쪽이 diff 도 작다.

## 결과

- 빈 결과가 `status: FAILED` + `error: "No content extracted from file"` 로 표면화된다.
- 포맷을 추가해도 이 가드는 자동으로 적용된다.
- **감수한 것:** 정말로 내용이 없는 파일(빈 CSV 등)도 실패로 처리된다.
  적재할 게 없으면 실패가 맞다고 판단했다.
- **되돌릴 신호:** "내용 없음" 이 정상 결과인 유스케이스가 생기면
  `status` 는 성공으로 두고 별도 필드로 구분한다.
