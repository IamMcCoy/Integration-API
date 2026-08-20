from __future__ import annotations

import asyncio
import shutil
import time
import uuid
from pathlib import Path
from typing import IO

import aiofiles

from src.common.Constants import Constants


#: 검토용으로 남겨 둔 업로드 원본이 들어가는 하위 디렉토리. 작업 디렉토리와 형제라
#: cleanup() 이 지우지 않는다.
REVIEW_DIR = 'review'


class UploadTooLarge(ValueError):
    """크기 상한 초과. 라우터에서 413 으로 매핑하려고 따로 둔다."""


class FileReader:
    """
    파일 읽기 단계.

    업로드로 들어온 파일은 반드시 디스크에 착지시켜야 한다.
    opendataloader_pdf 가 경로만 받기 때문에 메모리 스트림을 그대로 넘길 수 없다.
    그 착지 지점을 여기서 관리한다.
    """

    ALLOWED_EXTENSIONS = frozenset({'.pdf', '.txt', '.json', '.jsonl', '.csv'})

    MAX_UPLOAD_BYTES = Constants.UPLOAD_MAX_BYTES

    #: 스트리밍 복사 단위. 업로드 전체를 메모리에 올리지 않기 위한 값.
    COPY_CHUNK_BYTES = 1024 * 1024

    @staticmethod
    def staging_dir() -> str:
        """
        업로드 하나가 통째로 들어가는 격리 디렉토리를 만들고 경로를 반환.

        수명이 요청이 아니라 태스크에 묶인다. POST 핸들러는 task_id 만 주고
        바로 반환하므로, 워커가 처리를 끝낼 때까지 파일이 살아 있어야 한다.
        정리는 워커가 cleanup() 으로 한다.

        레플리카가 2개 이상이면 이 경로는 반드시 공유 볼륨이어야 한다.
        업로드를 받은 파드와 워커 파드가 다를 수 있다.
        """
        path = Path(Constants.UPLOAD_STAGING_DIR) / uuid.uuid4().hex
        path.mkdir(parents=True, exist_ok=True)
        return str(path)

    @staticmethod
    def cleanup(path: str) -> None:
        """작업 디렉토리를 통째로 지운다. 업로드 원본과 변환 산출물이 함께 사라진다."""
        shutil.rmtree(path, ignore_errors=True)

    @staticmethod
    def retain(file_path: str, task_id: str) -> str | None:
        """
        업로드 원본을 검토용으로 빼돌리고 그 경로를 반환. 실패하면 None.

        cleanup() 이 작업 디렉토리를 통째로 지우므로 그 전에 옮겨야 한다. 승인 화면이
        원문 페이지를 열어 보려면 파일이 남아 있어야 한다 — source_text 는 청크 하나뿐이라
        표가 잘려 있거나 앞뒤 맥락이 없는 경우가 있다.

        복사가 아니라 이동이다. 어차피 지워질 파일을 두 벌 들고 있을 이유가 없다.

        여기 쌓인 것은 sweep_retained() 가 턴다.
        """
        source = Path(file_path)
        target = Path(Constants.UPLOAD_STAGING_DIR) / REVIEW_DIR / f"{task_id}{source.suffix}"

        try:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(source), str(target))
            return str(target)
        except OSError:
            return None

    @staticmethod
    def sweep_retained(keep_days: int, protected: set[str]) -> int:
        """
        오래됐고 검토가 끝난 보관본을 지우고, 지운 개수를 반환.

        보관본은 저절로 줄지 않는다. 새로 하나 남길 때마다 이걸 부른다 — 파일이
        늘어나는 순간이 곧 정리할 순간이라, 별도의 스케줄러가 필요 없다.

        protected 에 든 태스크는 나이와 무관하게 남긴다. 아직 사람 판단을 기다리는
        추출이 있다는 뜻이고, 그 원문이 승인 화면이 보여 줄 유일한 근거다.

        keep_days 가 0 이하면 아무것도 지우지 않는다 — 보존이 필요한 환경의 탈출구.
        """
        root = Path(Constants.UPLOAD_STAGING_DIR) / REVIEW_DIR
        if keep_days <= 0 or not root.is_dir():
            return 0

        cutoff = time.time() - keep_days * 86400
        removed = 0

        for path in root.iterdir():
            if not path.is_file() or path.stem in protected:
                continue
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
                path.unlink()
            except OSError:
                # 한 파일이 안 지워져도 나머지는 마저 턴다.
                continue
            removed += 1

        return removed

    @staticmethod
    def retained(task_id: str) -> str | None:
        """검토용으로 남겨 둔 원본 경로. 없으면 None."""
        # task_id 는 DB 를 거쳐 돌아온 값이다. 글롭 패턴에 그대로 넣기 전에 확인한다.
        if not task_id.isalnum():
            return None

        found = sorted(Path(Constants.UPLOAD_STAGING_DIR).glob(f"{REVIEW_DIR}/{task_id}.*"))
        return str(found[0]) if found else None

    @classmethod
    async def save_upload(cls, upload, dst_dir: str, filename: str | None = None) -> str:
        """
        업로드 스트림을 dst_dir 에 저장하고 저장된 경로를 반환.

        upload 는 동기 read 를 가진 파일 객체이거나, 그것을 .file 로 감싼 객체
        (FastAPI UploadFile 등) 를 받는다. 프레임워크에 묶이지 않도록 덕타이핑한다.
        """
        name = cls._safe_filename(filename or cls._peek_filename(upload))
        dst = Path(dst_dir) / name

        # 동기 파일 객체만 지원한다. async read 만 가진 소스가 생기면 분기 추가.
        source: IO[bytes] = getattr(upload, 'file', upload)
        await asyncio.to_thread(cls._copy, source, dst)

        return str(dst)

    @staticmethod
    async def read_text(file_path: str, encoding: str = 'utf-8') -> str:
        """텍스트 파일을 비동기적으로 읽어서 문자열로 반환"""
        async with aiofiles.open(file_path, 'r', encoding=encoding) as f:
            return await f.read()

    @staticmethod
    def _peek_filename(upload) -> str | None:
        return getattr(upload, 'filename', None) or getattr(upload, 'name', None)

    @classmethod
    def _safe_filename(cls, filename: str | None) -> str:
        """
        업로드 파일명은 사용자 입력이다. 경로 성분을 전부 버리고 basename 만 취한다.

        '../../etc/cron.d/x' 같은 이름을 그대로 이어 붙이면 임의 위치에 파일을 쓰게 된다.
        """
        if not filename:
            raise ValueError('Upload has no filename')

        # posix 에서 백슬래시는 구분자가 아니라 Path 가 못 걸러낸다. 먼저 정규화한다.
        name = Path(str(filename).replace('\\', '/')).name

        if not name or name in {'.', '..'} or '\x00' in name:
            raise ValueError(f"Invalid upload filename: {filename!r}")

        extension = Path(name).suffix.lower()
        if extension not in cls.ALLOWED_EXTENSIONS:
            raise ValueError(f"Unsupported file extension: {extension or '(none)'}")

        return name

    @classmethod
    def _copy(cls, source: IO[bytes], dst: Path) -> None:
        """청크 단위로 복사하며 크기 상한을 강제한다 (동기, to_thread 에서 호출)."""
        if hasattr(source, 'seek'):
            source.seek(0)

        written = 0
        with open(dst, 'wb') as f:
            while True:
                buffer = source.read(cls.COPY_CHUNK_BYTES)
                if not buffer:
                    break

                written += len(buffer)
                if written > cls.MAX_UPLOAD_BYTES:
                    # 부분 저장된 파일은 작업 디렉토리 정리 시 함께 사라진다.
                    raise UploadTooLarge(f"Upload exceeds {cls.MAX_UPLOAD_BYTES} bytes")

                f.write(buffer)

        if written == 0:
            raise ValueError('Upload is empty')


if __name__ == '__main__':
    import io
    import os
    import tempfile

    # 자체 점검은 운영 설정(공유 볼륨 경로)에 의존하지 않는다.
    Constants.UPLOAD_STAGING_DIR = tempfile.mkdtemp(prefix='ia-selfcheck-')

    class _Upload:
        """FastAPI UploadFile 흉내 (.filename + .file)"""

        def __init__(self, filename: str, payload: bytes):
            self.filename = filename
            self.file = io.BytesIO(payload)

    async def _main() -> None:
        work_dir = FileReader.staging_dir()
        work = Path(work_dir)
        try:
            # 정상 저장
            saved = Path(await FileReader.save_upload(_Upload('report.pdf', b'%PDF-1.7 body'), work_dir))
            assert saved.parent == work, f"Saved outside workspace: {saved}"
            assert saved.read_bytes() == b'%PDF-1.7 body', 'Content mismatch'

            # path traversal - 디렉토리 성분이 제거돼야 한다
            for evil in ('../../../etc/passwd.pdf', 'C:\\windows\\evil.pdf', '/abs/path/x.pdf'):
                out = Path(await FileReader.save_upload(_Upload(evil, b'x'), work_dir))
                assert out.parent == work, f"Traversal not contained: {evil} -> {out}"
                assert '..' not in out.parts, f"Parent reference left: {out}"

            # 거부돼야 하는 입력들
            for bad in ('..', '', 'evil.exe', 'noext', 'nul\x00.pdf'):
                try:
                    await FileReader.save_upload(_Upload(bad, b'x'), work_dir)
                except ValueError:
                    pass
                else:
                    raise AssertionError(f"Should have been rejected: {bad!r}")

            # 빈 업로드 거부
            try:
                await FileReader.save_upload(_Upload('empty.pdf', b''), work_dir)
            except ValueError:
                pass
            else:
                raise AssertionError('Empty upload accepted')

            # 크기 상한
            original_max = FileReader.MAX_UPLOAD_BYTES
            FileReader.MAX_UPLOAD_BYTES = 10
            try:
                await FileReader.save_upload(_Upload('big.pdf', b'y' * 11), work_dir)
            except ValueError:
                pass
            else:
                raise AssertionError('Size limit not enforced')
            finally:
                FileReader.MAX_UPLOAD_BYTES = original_max

            # 텍스트 읽기 왕복
            (work / 'note.txt').write_text('안녕하세요', encoding='utf-8')
            assert await FileReader.read_text(str(work / 'note.txt')) == '안녕하세요', 'Text read mismatch'
            # 검토용 보관 — cleanup 이 지우는 자리 밖으로 나가야 한다
            kept = FileReader.retain(str(saved), 'abc123')
            assert kept is not None, 'Retention must return the kept path'
            assert not saved.exists(), 'Retained file must move, not copy'
            assert Path(kept).read_bytes() == b'%PDF-1.7 body', 'Retained content mismatch'
            assert FileReader.retained('abc123') == kept, 'Retained file must be findable by task id'
        finally:
            FileReader.cleanup(work_dir)

        assert not work.exists(), 'Staging dir not cleaned up'
        assert Path(FileReader.retained('abc123') or '').exists(), 'Cleanup must not remove retained sources'
        assert FileReader.retained('nosuchtask') is None, 'Missing source must yield None'

        # 글롭 패턴에 들어가는 값이라 경로 성분이 섞이면 안 된다.
        assert FileReader.retained('../../etc/passwd') is None, 'Path components must not reach the glob'

        # --- 보관본 정리 -----------------------------------------------------
        review = Path(Constants.UPLOAD_STAGING_DIR) / REVIEW_DIR
        old = time.time() - 40 * 86400

        for task in ('oldpending', 'olddone', 'freshdone'):
            (review / f"{task}.pdf").write_bytes(b'x')
        for task in ('oldpending', 'olddone'):
            os.utime(review / f"{task}.pdf", (old, old))

        removed = FileReader.sweep_retained(30, {'oldpending'})
        assert removed == 1, f"Only the aged, reviewed source must go: {removed}"
        assert (review / 'oldpending.pdf').exists(), 'Pending review must survive regardless of age'
        assert (review / 'freshdone.pdf').exists(), 'Recent source must survive'
        assert not (review / 'olddone.pdf').exists(), 'Aged reviewed source must be removed'

        # 0 이면 정리하지 않는다 — 보존이 필요한 환경의 탈출구
        os.utime(review / 'freshdone.pdf', (old, old))
        assert FileReader.sweep_retained(0, set()) == 0, 'Zero retention must disable sweeping'
        assert (review / 'freshdone.pdf').exists(), 'Sweeping must not run when disabled'

        print('OK: FileReader')

    asyncio.run(_main())
