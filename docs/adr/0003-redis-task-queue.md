# ADR-0003: 작업 큐와 상태를 Redis 에 둔다

- **상태:** Accepted
- **날짜:** 2026-08-05

## 배경

PDF 변환은 수 초가 걸린다. 요청을 붙잡아 두면 안 된다.
`POST` 는 `task_id` 만 돌려주고, `GET /tasks/{task_id}` 로 진행 상황을 조회하는
방식으로 정했다.

그러면 큐와 작업 상태를 **어디에 둘 것인가**가 남는다.
`k8s/ia-deploy.yaml` 이 아직 빈 껍데기라 레플리카 수가 정해지지 않았고,
여기서 갈렸다.

## 선택지

**A. 인메모리 (`asyncio.Queue` + dict)**
외부 의존 0, 코드 최소. 단 `replicas: 1` 고정이 필수다.
2개 이상이면 POST 를 받은 파드와 GET 을 받는 파드가 달라져 조회가 404 난다.
파드 재시작 시 진행 중 작업 유실.

**B. DB (이미 있는 sqlalchemy)**
`tasks` 테이블 + 폴링 워커. 새 인프라 불필요, 상태 영속.
폴링 주기만큼 지연.

**C. Redis**
큐와 상태 모두 Redis. 수평 확장·재시작 모두 강하고 폴링 없이 블로킹 대기 가능.
Redis 인프라와 `redis-py` 의존성이 필요.

## 결정

**C** 를 택했다.

```
상태:  ia:task:{task_id}  해시 (TTL 24시간)
대기열: ia:task:queue      리스트 (LPUSH / BRPOP)
```

레플리카가 몇 개든 같은 Redis 를 보므로 POST 를 받은 파드와 처리하는 워커 파드가
달라도 상태 조회가 된다.

워커는 API 와 **같은 프로세스**에서 돈다(`lifespan` 에서 `asyncio.create_task`).
파드를 늘리면 워커도 같이 늘어나고, 큐가 Redis 라 같은 작업을 둘이 집지 않는다.

내부 필드(`file_path`, `params`)는 같은 해시에 넣되 `TaskDetail` 로 나가지 않도록
`_INTERNAL_FIELDS` 로 걸러낸다.

## 결과

- 수평 확장과 재시작 모두 견딘다.
- 상태 조회가 폴링 없이 즉시 응답한다.
- **감수한 것 1:** Redis 인프라 운영 부담.
- **감수한 것 2:** `BRPOP` 은 ack 가 없다. 워커가 처리 도중 죽으면 그 작업은
  `RUNNING` 인 채로 남는다. 코드에 주석으로 표시했다.
  유실이 문제가 되면 Streams + consumer group 으로 간다.
- **감수한 것 3:** 완료된 작업은 TTL(기본 24시간) 후 만료되어 404 가 된다.
  영구 이력이 필요하면 B(DB) 를 병행해야 한다.
- **되돌릴 신호:** Redis 를 쓸 수 없는 환경이면 B 로. `TaskStore` 인터페이스
  (`create`/`get`/`update`/`payload`/`pop`)만 유지하면 그 파일 하나만 바꾸면 된다.
  `Orchestrator`·`TaskWorker`·라우터·모델은 그대로다.

## 진행률을 퍼센트로 만들지 않은 이유

`total` 은 청킹이 끝나야 알 수 있다. 그전에 0~100% 로 꾸미면 파싱 단계에서
거짓말을 하게 된다. `stage` + `processed`/`total` 이 정직하고,
`total` 은 확정 전까지 `null` 이다.
