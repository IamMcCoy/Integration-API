from __future__ import annotations

from fastapi import APIRouter
from fastapi import HTTPException
from fastapi import Query
from fastapi import Request

from src.api.models.TaskModels import TaskDetail
from src.api.models.TaskModels import TaskStatus
from src.common.Constants import Constants

router = APIRouter(prefix=Constants.TASK_ENDPOINT, tags=['Tasks'])


@router.get('', response_model=list[TaskDetail])
async def list_tasks(
        request: Request,
        status: TaskStatus | None = Query(default=None),
        limit: int = Query(default=100, gt=0, le=500),
):
    """
    작업 목록을 최신 생성 순으로 조회합니다.

    UI 의 작업 현황 화면용입니다 — 진행 중인 업로드가 어느 단계(stage)에 있는지,
    processed/total 로 얼마나 남았는지 한 화면에서 봅니다. `status` 를 주면
    해당 상태만 걸러냅니다. 만료(기본 24시간)된 작업은 목록에서 사라집니다.
    """
    orchestrator = request.app.state.orchestrator

    try:
        tasks = await orchestrator.list_tasks()
    except Exception as e:
        orchestrator.logger.error(f"Task listing failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')

    if status is not None:
        tasks = [task for task in tasks if task.status == status]

    return tasks[:limit]


@router.delete('/{task_id}', response_model=TaskDetail)
async def cancel_task(request: Request, task_id: str):
    """
    작업을 취소합니다.

    대기 중(PENDING)이면 큐에서 빠져 즉시 CANCELLED 가 됩니다. 처리 중(RUNNING)이면
    취소 요청만 접수되고, 워커가 현재 단계를 마친 뒤 다음 단계 진입 전에 멈춥니다 —
    응답의 status 가 아직 RUNNING 일 수 있으니 잠시 후 다시 조회하세요.

    이미 끝난(SUCCEEDED/FAILED/CANCELLED) 작업이면 400, 없으면 404 입니다.
    """
    orchestrator = request.app.state.orchestrator

    try:
        task = await orchestrator.cancel_task(task_id)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        orchestrator.logger.error(f"Task cancel failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')

    if task is None:
        raise HTTPException(status_code=404, detail='Task not found')

    return task


@router.get('/{task_id}', response_model=TaskDetail)
async def get_task(request: Request, task_id: str):
    """
    작업 진행 상황을 조회합니다.

    - **status**: PENDING(대기) / RUNNING(처리중) / SUCCEEDED(완료) / FAILED(실패)
    - **stage**: staged → parsing → chunking → filtering → extracting → persisting → done
    - **processed / total**: total 은 청킹이 끝나야 확정되며 그전에는 null 입니다
    - **result**: SUCCEEDED 일 때만 채워집니다
    - **error**: FAILED 일 때만 채워집니다

    완료된 작업은 일정 시간(기본 24시간) 후 만료되어 404 가 됩니다.
    """
    orchestrator = request.app.state.orchestrator

    try:
        task = await orchestrator.get_task(task_id)
    except Exception as e:
        orchestrator.logger.error(f"Task lookup failed: {str(e)}", exc_info=True)
        raise HTTPException(status_code=500, detail='Internal server error')

    if task is None:
        raise HTTPException(status_code=404, detail='Task not found')

    return task
