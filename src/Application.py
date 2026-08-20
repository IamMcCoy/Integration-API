from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from contextlib import suppress
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI

# Constants 가 import 시점에 설정을 읽으므로, .env 는 그보다 먼저 올라와야 한다.
# 파일이 없으면 조용히 넘어간다 — 운영에서는 환경변수를 직접 준다.
load_dotenv()

from src.api import Router                                          # noqa: E402
from src.common.Constants import Constants                          # noqa: E402
from src.common.LoggerManager import LoggerManager                  # noqa: E402
from src.core.Orchestrator import Orchestrator                      # noqa: E402
from src.core.extractor.SpecExtractor import SpecExtractor          # noqa: E402
from src.core.extractor.SpecRepository import SpecRepository        # noqa: E402
from src.core.task.TaskStore import TaskStore                       # noqa: E402
from src.core.task.TaskWorker import TaskWorker                     # noqa: E402


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger = LoggerManager.get()

    Path(Constants.UPLOAD_STAGING_DIR).mkdir(parents=True, exist_ok=True)

    store = TaskStore.from_config()
    orchestrator = Orchestrator(store)

    # extractor 와 repository 는 워커들이 공유한다. 각자 만들면 OpenAI 동시 요청 제한과
    # DB 커넥션 풀이 워커 수만큼 배로 풀려 레이트 리밋에 걸린다.
    extractor = SpecExtractor()
    repository = SpecRepository()

    # 자격증명이 틀렸거나 스키마가 안 올라갔으면 여기서 죽는다. 그냥 뜨면 그 사실이
    # persisting 단계까지 숨어 있다가 'spec_count 만 0 인 성공' 으로 나타난다.
    await asyncio.to_thread(repository.ping)
    logger.info('Database connection verified')

    workers = [
        TaskWorker(store, orchestrator, extractor, repository)
        for _ in range(Constants.WORKER_COUNT)
    ]

    app.state.orchestrator = orchestrator
    app.state.repository = repository
    app.state.workers = workers

    # 워커는 API 와 같은 프로세스에서 돈다. 파드를 늘리면 워커도 같이 늘어난다.
    worker_tasks = [asyncio.create_task(w.run()) for w in workers]
    logger.info(f"Application started ({len(workers)} workers)")

    yield

    for worker in workers:
        worker.stop()
    for worker_task in worker_tasks:
        worker_task.cancel()
    with suppress(asyncio.CancelledError):
        await asyncio.gather(*worker_tasks)
    # 5.0.1 에서 close() -> aclose() 로 이름이 바뀌었다. 둘 다 받는다.
    close = getattr(store.client, 'aclose', None) or store.client.close
    await close()
    logger.info('Application stopped')


app = FastAPI(
    title='Integration API',
    description='PDF 문서에서 API 규격을 추출해 데이터베이스에 적재하는 파이프라인',
    lifespan=lifespan,
)
app.include_router(Router.router)


@app.get('/health', tags=['System'])
async def health():
    return {'status': 'ok'}


if __name__ == '__main__':
    uvicorn.run(app, host='0.0.0.0', port=8880)
