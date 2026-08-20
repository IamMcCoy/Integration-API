from __future__ import annotations

from pathlib import Path

from src.api.models.TaskModels import ExtractionParams
from src.api.models.TaskModels import TaskCreated
from src.api.models.TaskModels import TaskDetail
from src.common.LoggerManager import LoggerManager
from src.core.parser.PDFParser import FileParser
from src.core.reader.FileReader import FileReader
from src.core.task.TaskStore import TaskStore


class Orchestrator:
    """
    파일 처리 총괄 진입점.

    업로드 접수(submit_upload)와 실제 처리(process_file)가 분리돼 있다.
    API 는 접수만 하고 task_id 를 즉시 돌려주고, 처리는 워커가 큐에서 꺼내 한다.
    """

    def __init__(self, store: TaskStore):
        self.logger = LoggerManager.get()
        self.parser = FileParser()
        self.store = store

    async def submit_upload(
            self,
            upload,
            params: ExtractionParams,
            filename: str | None = None,
    ) -> TaskCreated:
        """
        업로드를 작업 디렉토리에 착지시키고 태스크로 등록한다. 처리는 하지 않는다.

        여기서 만든 디렉토리는 워커가 처리를 끝낸 뒤 정리한다.
        """
        params.validated()

        work_dir = FileReader.staging_dir()
        try:
            file_path: str = await FileReader.save_upload(upload, work_dir, filename)
        except Exception:
            # 접수 실패 시 태스크가 만들어지지 않으므로 정리할 주체가 없다. 여기서 지운다.
            FileReader.cleanup(work_dir)
            raise

        task_id = await self.store.create(
            params=params,
            filename=Path(file_path).name,
            file_path=file_path,
        )
        self.logger.info(f"Task queued: {task_id} {file_path}")

        return TaskCreated(task_id=task_id)

    async def get_task(self, task_id: str) -> TaskDetail | None:
        return await self.store.get(task_id)

    async def list_tasks(self) -> list[TaskDetail]:
        return await self.store.list()

    async def cancel_task(self, task_id: str) -> TaskDetail | None:
        return await self.store.cancel(task_id)

    async def process_file(self, file_path: str, chunk_size: int, chunk_overlap: int) -> list[dict]:
        """
        로컬 경로의 파일을 확장자에 맞는 처리기로 넘긴다.

        확장자를 추가하려면 여기 분기 한 줄과 처리기 하나를 더하면 된다.
        어떤 처리기든 {chunk_id, text, metadata} 구조를 돌려줘야 한다 —
        뒤따르는 선별/추출 단계가 text 와 metadata 를 읽는다.
        """
        extension: str = Path(file_path).suffix.lower()

        if extension == '.pdf':
            return await self.parser.process_pdf_file(file_path, chunk_size, chunk_overlap)
        if extension == '.txt':
            return await self.parser.process_txt_file(file_path, chunk_size, chunk_overlap)

        raise ValueError(f"Unsupported file extension: {extension or '(none)'}")
