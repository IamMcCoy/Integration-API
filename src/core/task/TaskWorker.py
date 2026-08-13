"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from src.api.models.TaskModels import ExtractionResult
from src.api.models.TaskModels import TaskStage
from src.api.models.TaskModels import TaskStatus
from src.common.Constants import Constants
from src.common.LoggerManager import LoggerManager
from src.core.extractor.SpecExtractor import SpecExtractor
from src.core.extractor.SpecRepository import SpecRepository
from src.core.Orchestrator import Orchestrator
from src.core.reader.FileReader import FileReader
from src.core.task.TaskStore import TaskStore


class CancelRequested(Exception):
    """사람이 취소를 요청했다. 실패가 아니라 별도 갈래(CANCELLED)로 끝낸다."""


class TaskWorker:
    """
    대기열에서 태스크를 꺼내 추출 파이프라인을 돌리고 상태를 갱신한다.

    파드마다 하나씩 뜨고, 한 번에 태스크 하나씩 순차 처리한다.
    동시 처리가 필요해지면 워커를 여러 개 띄우면 된다. 큐가 Redis 라 같은 태스크를
    둘이 집는 일은 없다.
    """

    def __init__(
            self,
            store: TaskStore,
            orchestrator: Orchestrator,
            extractor: SpecExtractor,
            repository: SpecRepository,
    ):
        self.logger = LoggerManager.get()
        self.store = store
        self.orchestrator = orchestrator
        self.extractor = extractor
        self.repository = repository
        self._running = False

    async def run(self) -> None:
        self._running = True
        self.logger.info('Task worker started')

        while self._running:
            try:
                task_id = await self.store.pop()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                # Redis 가 잠깐 끊겨도 워커는 죽지 않아야 한다.
                self.logger.error(f"Queue pop failed: {str(e)}", exc_info=True)
                await asyncio.sleep(1)
                continue

            if task_id:
                await self._handle(task_id)

        self.logger.info('Task worker stopped')

    def stop(self) -> None:
        self._running = False

    async def _handle(self, task_id: str) -> None:
        payload = await self.store.payload(task_id)
        if payload is None:
            # TTL 만료 등으로 상태가 사라진 태스크. 큐에만 남은 것이므로 버린다.
            self.logger.warning(f"Task payload missing, skipped: {task_id}")
            return

        file_path, params = payload
        work_dir = str(Path(file_path).parent)

        try:
            # 취소는 단계 경계에서만 확인한다 — 진행 중인 LLM 호출을 끊지는 못하지만,
            # 다음 단계(특히 비싼 extracting)에 들어가는 것은 막는다.
            # ponytail: 단계 경계 확인. extracting 도중 즉시 중단이 필요해지면
            # SpecExtractor 에 중단 콜백을 내리는 것으로.
            await self._raise_if_cancelled(task_id)
            await self.store.update(task_id, status=TaskStatus.RUNNING, stage=TaskStage.PARSING)

            chunks = await self.orchestrator.process_file(
                file_path, params.chunk_size, params.chunk_overlap,
            )

            # 여기가 모든 포맷이 지나가는 한 지점이다. 빈 결과를 성공으로 돌려주면
            # 호출자는 초록불을 받고 아무것도 적재되지 않은 상태를 갖게 된다.
            if not chunks:
                raise ValueError('No content extracted from file')

            await self.store.update(
                task_id,
                stage=TaskStage.CHUNKING,
                total=len(chunks),
                processed=len(chunks),
            )

            await self._raise_if_cancelled(task_id)
            await self.store.update(task_id, stage=TaskStage.FILTERING, processed=0)
            api_chunks = await self.extractor.select(chunks)

            await self._raise_if_cancelled(task_id)
            await self.store.update(
                task_id, stage=TaskStage.EXTRACTING, processed=len(api_chunks),
            )
            pairs = await self.extractor.extract(api_chunks)

            await self._raise_if_cancelled(task_id)
            await self.store.update(task_id, stage=TaskStage.PERSISTING)
            vendor, device, version = await self._resolve_target(params, chunks)

            # 동기 DB 호출이라 그대로 부르면 이벤트 루프가 멈춘다.
            saved, approved = await asyncio.to_thread(
                self.repository.save,
                pairs, task_id, params.document_name, vendor, device, version,
            )

            # 승인 화면에서 원문 페이지를 열어 보려면 업로드 원본이 살아 있어야 한다.
            # 아래 finally 가 작업 디렉토리를 통째로 지우므로 그 전에 빼돌린다.
            if saved and not await asyncio.to_thread(FileReader.retain, file_path, task_id):
                self.logger.warning(f"Source retention failed, review link unavailable: {task_id}")

            try:
                swept = await asyncio.to_thread(self._sweep_sources)
                if swept:
                    self.logger.info(f"Swept {swept} retained source(s)")
            except Exception as e:
                # 정리에 실패했다고 성공한 작업을 실패로 만들지 않는다. 다음 작업이 또 턴다.
                self.logger.warning(f"Source sweep failed: {str(e)}")

            await self.store.update(
                task_id,
                status=TaskStatus.SUCCEEDED,
                stage=TaskStage.DONE,
                processed=len(chunks),
                result=ExtractionResult(
                    document_name=params.document_name,
                    chunk_count=len(chunks),
                    api_chunk_count=len(api_chunks),
                    spec_count=saved,
                ),
            )
            self.logger.info(
                f"Task succeeded: {task_id} "
                f"({len(chunks)} chunks -> {len(api_chunks)} api chunks -> {saved} specs, "
                f"{approved} auto-approved)",
            )

        except asyncio.CancelledError:
            raise
        except CancelRequested:
            self.logger.info(f"Task cancelled: {task_id}")
            await self.store.update(task_id, status=TaskStatus.CANCELLED)
        except Exception as e:
            self.logger.error(f"Task failed: {task_id} - {str(e)}", exc_info=True)
            await self.store.update(task_id, status=TaskStatus.FAILED, error=str(e))

        finally:
            FileReader.cleanup(work_dir)

    async def _raise_if_cancelled(self, task_id: str) -> None:
        if await self.store.cancel_requested(task_id):
            raise CancelRequested()

    def _sweep_sources(self) -> int:
        """
        오래된 보관본을 턴다. 동기 DB 호출이 섞여 있어 to_thread 에서 부른다.

        검토 대기 중인 태스크의 원본은 보호한다.
        """
        return FileReader.sweep_retained(
            Constants.REVIEW_RETENTION_DAYS, self.repository.pending_task_ids(),
        )

    async def _resolve_target(self, params, chunks: list[dict]) -> tuple[str, str, str]:
        """
        적재 대상(벤더/장비/버전)을 확정한다. 폼 입력이 있으면 그쪽이 이긴다.

        셋 다 비었을 때만 LLM 에 물어본다. 그마저 비면 document_name 으로 떨어뜨린다 —
        DB 가 NOT NULL 이라 빈 문자열로 두면 적재 자체가 실패한다.
        """
        vendor, device, version = params.vendor_name, params.device_name, params.api_version

        if not (vendor and device and version):
            identity = await self.extractor.identify(chunks)
            vendor = vendor or identity.vendor_name
            device = device or identity.device_name
            version = version or identity.api_version
            self.logger.info(f"Resolved target from document: {vendor} / {device} / {version}")

        return (
            vendor or params.document_name,
            device or params.document_name,
            version or 'unknown',
        )
