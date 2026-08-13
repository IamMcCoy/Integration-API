"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from datetime import timezone

from redis.asyncio import Redis

from src.api.models.TaskModels import ExtractionParams
from src.api.models.TaskModels import TaskDetail
from src.api.models.TaskModels import TaskStage
from src.api.models.TaskModels import TaskStatus
from src.common.Constants import Constants


class TaskStore:
    """
    태스크 상태와 대기열을 Redis 에 둔다.

    상태는 태스크당 해시 하나, 대기열은 리스트 하나다.
    레플리카가 몇 개든 같은 Redis 를 보므로 POST 를 받은 파드와
    처리하는 워커 파드가 달라도 상태 조회가 된다.
    """

    QUEUE_KEY = 'ia:task:queue'
    TASK_KEY = 'ia:task:{task_id}'

    #: TaskDetail 로 나가지 않는 내부 전용 필드 (워커만 본다)
    _INTERNAL_FIELDS = frozenset({'file_path', 'params', 'cancel'})

    #: 소켓 읽기 제한을 BRPOP 블록 시간보다 이만큼 길게 잡는다.
    #:
    #: 둘이 같으면 서버가 nil 을 돌려주는 시점과 클라이언트가 포기하는 시점이 경합한다.
    #: 유휴일 때는 로그만 더럽히지만, 블록이 끝나기 직전에 태스크가 들어오면 서버는
    #: 큐에서 꺼내 보냈는데 클라이언트는 안 받은 상태가 되어 그 태스크가 사라진다.
    #: redis-py 8 부터 socket_timeout 기본값이 None 이 아니라 5초라 기본 설정과 맞부딪힌다.
    SOCKET_TIMEOUT_MARGIN_SECONDS = 5

    def __init__(self, client: Redis):
        self.client = client

    @classmethod
    def socket_timeout(cls) -> int:
        """BRPOP 이 블록하는 동안 클라이언트가 먼저 포기하지 않을 만큼의 읽기 제한."""
        return Constants.TASK_QUEUE_BLOCK_SECONDS + cls.SOCKET_TIMEOUT_MARGIN_SECONDS

    @classmethod
    def from_config(cls) -> TaskStore:
        return cls(
            Redis(
                host=Constants.REDIS_HOST,
                port=Constants.REDIS_PORT,
                db=Constants.REDIS_DB,
                password=Constants.REDIS_PASSWORD,
                socket_timeout=cls.socket_timeout(),
                decode_responses=True,
            ),
        )

    async def create(self, params: ExtractionParams, filename: str, file_path: str) -> str:
        """태스크를 PENDING 으로 등록하고 대기열에 넣은 뒤 task_id 를 반환."""
        task_id = uuid.uuid4().hex
        now = self._now()

        record = {
            'task_id': task_id,
            'status': TaskStatus.PENDING.value,
            'stage': TaskStage.STAGED.value,
            'document_name': params.document_name,
            'filename': filename,
            'processed': '0',
            'created_at': now,
            'updated_at': now,
            'file_path': file_path,
            'params': params.model_dump_json(),
        }

        key = self.TASK_KEY.format(task_id=task_id)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=record)
            pipe.expire(key, Constants.TASK_TTL_SECONDS)
            pipe.lpush(self.QUEUE_KEY, task_id)
            await pipe.execute()

        return task_id

    async def update(self, task_id: str, **fields) -> None:
        """상태/진행 필드를 갱신. 모델과 dict 값은 JSON 으로 직렬화한다."""
        mapping = {'updated_at': self._now()}
        for name, value in fields.items():
            if value is None:
                continue
            if hasattr(value, 'model_dump_json'):
                mapping[name] = value.model_dump_json()
            elif isinstance(value, (dict, list)):
                mapping[name] = json.dumps(value, ensure_ascii=False)
            elif isinstance(value, (TaskStatus, TaskStage)):
                mapping[name] = value.value
            else:
                mapping[name] = str(value)

        key = self.TASK_KEY.format(task_id=task_id)
        async with self.client.pipeline(transaction=True) as pipe:
            pipe.hset(key, mapping=mapping)
            pipe.expire(key, Constants.TASK_TTL_SECONDS)
            await pipe.execute()

    async def get(self, task_id: str) -> TaskDetail | None:
        record = await self.client.hgetall(self.TASK_KEY.format(task_id=task_id))
        if not record:
            return None

        return self._detail(record)

    async def list(self) -> list[TaskDetail]:
        """
        전체 태스크를 최신 생성 순으로 돌려준다.

        태스크마다 해시 키 하나라 SCAN 으로 훑는다. TTL 이 걸려 있어 목록은
        자연히 최근 것만 남는다.
        """
        # ponytail: 전수 SCAN + 개별 HGETALL. 태스크가 수천 건을 넘어가면
        # created_at ZSET 인덱스를 따로 두는 걸로.
        details = []
        async for key in self.client.scan_iter(match=self.TASK_KEY.format(task_id='*'), count=200):
            # 큐 키(ia:task:queue)가 같은 패턴에 걸린다. list 타입이라 HGETALL 이 WRONGTYPE 으로 죽는다.
            if key == self.QUEUE_KEY:
                continue
            record = await self.client.hgetall(key)
            if record:
                details.append(self._detail(record))

        details.sort(key=lambda t: t.created_at or '', reverse=True)
        return details

    async def cancel(self, task_id: str) -> TaskDetail | None:
        """
        태스크를 취소한다. 없으면 None, 이미 끝난 태스크면 ValueError.

        PENDING 은 큐에서 빼는 것으로 즉시 끝난다. RUNNING(또는 큐 제거와 워커의
        pop 이 경합해 이미 집힌 것)은 플래그만 세운다 — 워커가 단계 경계에서 보고
        스스로 멈춘다. 강제 종료는 없다. 처리 중인 LLM 호출을 도중에 끊을 방법이
        없고, 끊어도 비용은 이미 나갔다.
        """
        key = self.TASK_KEY.format(task_id=task_id)
        record = await self.client.hgetall(key)
        if not record:
            return None

        status = record.get('status')
        if status in (TaskStatus.SUCCEEDED.value, TaskStatus.FAILED.value, TaskStatus.CANCELLED.value):
            raise ValueError(f"Task already finished: {task_id} ({status})")

        if status == TaskStatus.PENDING.value:
            removed = await self.client.lrem(self.QUEUE_KEY, 0, task_id)
            if removed:
                await self.update(task_id, status=TaskStatus.CANCELLED)
                return await self.get(task_id)

        await self.client.hset(key, 'cancel', '1')
        return await self.get(task_id)

    async def cancel_requested(self, task_id: str) -> bool:
        """워커 전용. 사람이 이 태스크의 취소를 요청했는가."""
        flag = await self.client.hget(self.TASK_KEY.format(task_id=task_id), 'cancel')
        return flag == '1'

    @classmethod
    def _detail(cls, record: dict) -> TaskDetail:
        """Redis 해시를 조회 응답으로 바꾼다. 내부 전용 필드는 걸러낸다."""
        public = {k: v for k, v in record.items() if k not in cls._INTERNAL_FIELDS}
        if 'result' in public:
            public['result'] = json.loads(public['result'])
        if public.get('total') == '':
            public.pop('total')

        return TaskDetail(**public)

    async def payload(self, task_id: str) -> tuple[str, ExtractionParams] | None:
        """워커 전용. 처리에 필요한 파일 경로와 파라미터를 꺼낸다."""
        file_path, params = await self.client.hmget(
            self.TASK_KEY.format(task_id=task_id), ['file_path', 'params'],
        )
        if not file_path or not params:
            return None

        return file_path, ExtractionParams.model_validate_json(params)

    async def pop(self) -> str | None:
        """대기열에서 task_id 를 하나 꺼낸다. 비어 있으면 블록했다가 None."""
        # BRPOP 은 ack 가 없다. 워커가 처리 도중 죽으면 그 태스크는
        # RUNNING 인 채로 남는다. 유실이 문제가 되면 Streams + consumer group 으로.
        item = await self.client.brpop(
            self.QUEUE_KEY, timeout=Constants.TASK_QUEUE_BLOCK_SECONDS,
        )
        return item[1] if item else None

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()


if __name__ == '__main__':
    # 소켓 읽기 제한은 BRPOP 이 서버에서 블록하는 시간보다 반드시 길어야 한다.
    # 같거나 짧으면 클라이언트가 먼저 포기하고, 그 순간 도착한 태스크가 유실된다.
    assert TaskStore.socket_timeout() > Constants.TASK_QUEUE_BLOCK_SECONDS, \
        'Socket read timeout must outlast the BRPOP block'

    # 설정으로 블록 시간을 늘려도 여유가 유지돼야 한다.
    original = Constants.TASK_QUEUE_BLOCK_SECONDS
    try:
        for block in (0, 1, 30, 300):
            Constants.TASK_QUEUE_BLOCK_SECONDS = block
            assert TaskStore.socket_timeout() > block, \
                f"Margin lost at block={block}: {TaskStore.socket_timeout()}"
    finally:
        Constants.TASK_QUEUE_BLOCK_SECONDS = original

    # 클라이언트를 실제로 만들어 그 값이 연결에 실렸는지 본다 (접속하지는 않는다).
    store = TaskStore.from_config()
    configured = store.client.connection_pool.connection_kwargs['socket_timeout']
    assert configured == TaskStore.socket_timeout(), \
        f"Socket timeout not applied to the client: {configured}"

    # 내부 필드는 조회 응답으로 나가면 안 된다. cancel 플래그도 워커 전용이다.
    assert 'file_path' in TaskStore._INTERNAL_FIELDS, 'file_path must stay internal'
    assert 'params' in TaskStore._INTERNAL_FIELDS, 'params must stay internal'
    assert 'cancel' in TaskStore._INTERNAL_FIELDS, 'cancel flag must stay internal'

    # 큐 키는 태스크 키 패턴에 걸린다 — list() 가 건너뛰지 않으면 WRONGTYPE 으로 죽는다.
    # 키 이름을 바꿔 겹치지 않게 되면 이 검사와 list() 의 건너뛰기를 함께 정리한다.
    import fnmatch
    assert fnmatch.fnmatch(TaskStore.QUEUE_KEY, TaskStore.TASK_KEY.format(task_id='*')), \
        'Queue key no longer matches the task pattern — remove the skip in list()'

    # 해시 → 응답 변환. 내부 필드 제거, 미확정 total 제거가 함께 검증된다.
    detail = TaskStore._detail({
        'task_id': 'abc',
        'status': 'PENDING',
        'total': '',
        'file_path': '/should/not/leak',
        'params': '{}',
    })
    assert detail.task_id == 'abc', 'task_id must survive the mapping'
    assert detail.total is None, 'Blank total must become None'
    assert 'file_path' not in detail.model_dump(), 'Internal fields must not leak'

    print(f"OK: socket timeout {configured}s over {Constants.TASK_QUEUE_BLOCK_SECONDS}s block")
