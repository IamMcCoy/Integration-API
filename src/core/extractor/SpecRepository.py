from __future__ import annotations

import json
import re

from sqlalchemy import bindparam
from sqlalchemy import text

from src.common.Constants import Constants
from src.common.LoggerManager import LoggerManager
from src.core.extractor.SpecScorer import APPROVED
from src.core.extractor.SpecScorer import SpecScorer
from src.database.DBPool import DBPool

HTTP_METHODS = frozenset({'GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'})

#: 헤더 경로 끝에 붙는 규격 항목. 이걸 떼면 API 단위가 남는다.
#: 'System > 4.3.2 ACL 추가 > Query parameters' → 'System > 4.3.2 ACL 추가'
SPEC_SUBSECTION = re.compile(
    r'^\s*[\d.]*\s*(url|returns?|body|request|response|'
    r'(path|query)\s*parameters?|parameters?|예시|example|헤더|header)\s*$',
    re.I,
)

#: 기존 행이 있으면 그 id 를 LAST_INSERT_ID 로 되돌려 받는 MariaDB 관용구.
UPSERT_VENDOR = text("""
    INSERT INTO vendor (name) VALUES (:name)
    ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
""")

UPSERT_DEVICE = text("""
    INSERT INTO device (vendor_id, name) VALUES (:vendor_id, :name)
    ON DUPLICATE KEY UPDATE id = LAST_INSERT_ID(id)
""")

UPSERT_DEFINITION = text("""
    INSERT INTO api_definition (device_id, api_version, title, group_name, method, endpoint)
    VALUES (:device_id, :api_version, :title, :group_name, :method, :endpoint)
    ON DUPLICATE KEY UPDATE
        id = LAST_INSERT_ID(id),
        title = VALUES(title),
        group_name = VALUES(group_name)
""")
# method 와 endpoint 는 갱신하지 않는다. 유니크 키가 그 둘에서 나오므로 충돌했다는 건
# 이미 같은 값이라는 뜻이다. 대신 title 을 갱신한다 — 정체가 아니라 표시용 이름이라
# 다시 추출하면 더 나은 값이 올 수 있다.

UPSERT_SCHEMA = text("""
    INSERT INTO api_schema (api_def_id, request, response)
    VALUES (:api_def_id, :request, :response)
    ON DUPLICATE KEY UPDATE request = VALUES(request), response = VALUES(response)
""")

# 빈 스키마(요청·응답 모두 0개)로는 기존 스키마를 덮지 않을 때 쓴다. 첫 적재는
# 빈 스키마라도 들어가되, 이미 행이 있으면 아무것도 바꾸지 않는다.
#
# 어느 쪽을 쓸지는 파이썬(_promote)이 고른다 — MariaDB 는 ON DUPLICATE KEY UPDATE
# 절 안의 JSON_LENGTH(VALUES(...)) 를 처리하지 못한다(에러 4037).
INSERT_SCHEMA_KEEP = text("""
    INSERT INTO api_schema (api_def_id, request, response)
    VALUES (:api_def_id, :request, :response)
    ON DUPLICATE KEY UPDATE api_def_id = api_def_id
""")

INSERT_EXTRACTION = text("""
    INSERT INTO api_extraction (
        task_id, document_name, vendor_name, device_name, api_version,
        chunk_id, page_number, source_text, spec_json, score, review_status, api_def_id, reviewed_at
    ) VALUES (
        :task_id, :document_name, :vendor_name, :device_name, :api_version,
        :chunk_id, :page_number, :source_text, :spec_json, :score, :review_status, :api_def_id, :reviewed_at
    )
""")


class ExtractionNotFound(LookupError):
    """검토 대상이 없다. 라우터가 400 이 아니라 404 로 내보내려면 구분이 필요하다."""


class SpecRepository:
    """
    추출 결과를 MariaDB 에 적재한다.

    모든 결과는 api_extraction 에 먼저 들어간다. 점수가 자동 승인 임계값을 넘은 것만
    같은 트랜잭션 안에서 api_definition / api_schema 까지 옮긴다.

    ORM 모델을 만들지 않는다. DDL 은 sql/schema.sql 하나가 진실이고, 같은 스키마를
    파이썬으로 한 번 더 적으면 둘이 어긋날 자리만 생긴다.
    """

    def __init__(self, engine=None):
        self.logger = LoggerManager.get()
        self.engine = engine if engine is not None else DBPool().pool

    def save(
            self,
            pairs: list[tuple[dict, dict]],
            task_id: str,
            document_name: str,
            vendor_name: str,
            device_name: str,
            api_version: str,
    ) -> tuple[int, int]:
        """
        (청크, 규격) 쌍들을 적재하고 (적재 건수, 자동 승인 건수) 를 돌려준다.

        같은 API 에서 나온 규격은 먼저 하나로 합친다 — 한 API 가 여러 청크에 걸치면
        추출이 청크마다 돌아 조각난 규격이 여러 개 나오는데, api_schema 는
        UNIQUE(api_def_id) 라 나중 것이 앞의 것을 덮어써서 파라미터가 사라진다.

        묶음마다 트랜잭션을 끊는다 — 수백 개 중 하나가 실패했다고 나머지를
        전부 되돌릴 이유가 없다.
        """
        saved = 0
        approved = 0

        for chunks, spec in self._merge_by_api(pairs):
            # 파라미터가 어느 조각에서 왔든 묶음 전체에서 근거를 찾는다.
            chunk = chunks[0]
            source_text = '\n\n'.join(c['text'] for c in chunks)
            score = SpecScorer.score(spec, source_text)
            status = SpecScorer.classify(
                score, Constants.SCORE_AUTO_THRESHOLD, Constants.SCORE_REVIEW_THRESHOLD,
            )

            try:
                with self.engine.begin() as conn:
                    api_def_id = None
                    if status == APPROVED:
                        api_def_id = self._promote(
                            conn, spec, chunk, vendor_name, device_name, api_version,
                        )
                        approved += 1

                    conn.execute(
                        INSERT_EXTRACTION, {
                            'task_id': task_id,
                            'document_name': document_name,
                            'vendor_name': vendor_name,
                            'device_name': device_name,
                            'api_version': api_version,
                            'chunk_id': chunk['chunk_id'],
                            'page_number': chunk.get('metadata', {}).get('page_number', 1),
                            # 합친 원문을 남긴다. 승인 화면에서 파라미터마다 근거를 대조하려면
                            # 그 파라미터가 나온 조각이 다 있어야 한다.
                            'source_text': source_text,
                            'spec_json': json.dumps(spec, ensure_ascii=False),
                            'score': score,
                            'review_status': status,
                            'api_def_id': api_def_id,
                            'reviewed_at': None,
                        },
                    )
                saved += 1

            except Exception as e:
                self.logger.error(f"Failed to persist spec '{spec.get('title')}': {str(e)}", exc_info=True)

        self.logger.info(
            f"Persisted {saved} specs from {len(pairs)} extractions ({approved} auto-approved)",
        )
        return saved, approved

    @staticmethod
    def _api_group_key(chunk: dict) -> str | None:
        """
        청크가 속한 API 단위. 헤더 경로에서 규격 항목(URL/Returns/...)을 떼어 만든다.

        LLM 이 낸 title 로 묶지 않는 이유: 같은 API 라도 조각마다 'ACL 추가',
        'ACL 추가 응답' 처럼 다르게 나올 수 있다. 헤더 경로는 문서 구조에서 오므로
        같은 API 의 조각들이 반드시 같은 키를 갖는다.
        """
        headings = chunk.get('metadata', {}).get('headings') or []
        kept = [h for h in headings if not SPEC_SUBSECTION.match(h)]
        return ' > '.join(kept) or None

    @staticmethod
    def _merge_by_api(pairs: list[tuple[dict, dict]]) -> list[tuple[list[dict], dict]]:
        """
        같은 API 에서 나온 규격을 하나로 합쳐 (청크들, 규격) 목록을 돌려준다.

        헤더 경로가 없는 청크는 묶지 않는다 — 어디 속하는지 알 수 없으므로 단독으로 둔다.
        """
        groups: dict[str, list[tuple[dict, dict]]] = {}
        order: list[str] = []
        singles: list[tuple[list[dict], dict]] = []

        for chunk, spec in pairs:
            key = SpecRepository._api_group_key(chunk)
            if key is None:
                singles.append(([chunk], spec))
                continue
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append((chunk, spec))

        merged: list[tuple[list[dict], dict]] = []
        for key in order:
            merged.extend(SpecRepository._merge_group(groups[key], key))

        return merged + singles

    @staticmethod
    def _grounded_endpoint(chunk: dict, spec: dict) -> str | None:
        """자기 청크 원문에 실제로 적힌 엔드포인트만 인정한다."""
        path = SpecScorer.endpoint_path(spec.get('endpoint'))
        if path and SpecScorer._normalize(path) in SpecScorer._normalize(chunk['text']):
            return path
        return None

    @staticmethod
    def _merge_group(items: list[tuple[dict, dict]], group_key: str) -> list[tuple[list[dict], dict]]:
        """
        한 헤더 경로 안의 조각들을 합친다.

        헤더 하나에 API 가 여럿 있을 수 있다 — 문서가 '5.2.4 적용/취소' 처럼 묶어 쓰거나
        하위 절 번호가 헤더로 안 잡히는 경우다. 그때 엔드포인트를 하나만 고르면 나머지
        API 를 통째로 잃으므로, 근거 있는 엔드포인트별로 다시 나눈다.
        """
        by_endpoint: dict[str, list[tuple[dict, dict]]] = {}
        unknown: list[tuple[dict, dict]] = []

        for chunk, spec in items:
            path = SpecRepository._grounded_endpoint(chunk, spec)
            if path:
                by_endpoint.setdefault(path, []).append((chunk, spec))
            else:
                unknown.append((chunk, spec))

        # 엔드포인트가 하나(또는 없음)면 이 헤더는 API 하나다. 조각을 전부 합친다.
        if len(by_endpoint) <= 1:
            return [([c for c, _ in items], SpecRepository._merge_specs(items, group_key))]

        # 여럿이면 엔드포인트별로 합친다. 엔드포인트를 못 찾은 조각은 어디 붙일지
        # 알 수 없으므로 단독으로 둔다 — 잘못 붙이면 남의 파라미터가 섞인다.
        out = [
            ([c for c, _ in sub], SpecRepository._merge_specs(sub, group_key))
            for sub in by_endpoint.values()
        ]
        out.extend(([chunk], spec) for chunk, spec in unknown)
        return out

    @staticmethod
    def _merge_specs(items: list[tuple[dict, dict]], group_key: str) -> dict:
        """
        조각들을 규격 하나로 합친다.

        엔드포인트는 자기 청크에 근거가 있는 것만 채택한다 — URL 이 없는 조각에서
        모델이 문맥으로 지어낸 경로를 고르면 그 API 를 못 부르게 된다.
        파라미터는 이름 기준으로 합치되 먼저 나온 것을 남긴다.
        """
        method = endpoint = None
        for chunk, spec in items:
            path = SpecRepository._grounded_endpoint(chunk, spec)
            if path:
                method, endpoint = spec.get('method'), path
                break

        def union(field: str) -> list[dict]:
            seen: dict[str, dict] = {}
            for _, spec in items:
                for parameter in spec.get(field) or []:
                    name = parameter.get('name')
                    if name and name not in seen:
                        seen[name] = parameter
            return list(seen.values())

        # 제목도 헤더 경로에서 가져온다. 조각마다 다르게 나오는 LLM 출력보다 안정적이다.
        title = group_key.split(' > ')[-1] or items[0][1].get('title')

        return {
            'title': title,
            'method': method,
            'endpoint': endpoint,
            'request_parameters': union('request_parameters'),
            'response_parameters': union('response_parameters'),
        }

    def ping(self) -> None:
        """
        접속과 스키마 적용 여부를 기동 시에 확인한다.

        엔진은 첫 쿼리 전까지 접속하지 않으므로, 이걸 두지 않으면 자격증명 오타가
        persisting 단계까지 숨어 있다가 'spec_count 만 0 인 성공' 으로 위장된다.
        """
        with self.engine.begin() as conn:
            conn.execute(text('SELECT 1 FROM api_extraction LIMIT 1'))

    def pending_task_ids(self) -> set[str]:
        """
        아직 사람 판단을 기다리는 추출이 남은 태스크.

        이 태스크들의 업로드 원본은 나이와 무관하게 보존해야 한다 — 승인 화면이
        대조해 보여 줄 근거가 그것뿐이다.
        """
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("SELECT DISTINCT task_id FROM api_extraction WHERE review_status = 'pending'"),
            )

        return {row[0] for row in rows}

    def find(self, extraction_id: int) -> dict | None:
        """단건 상세. 원문 열람(source)과 UI 의 검토 상세 화면이 같이 쓴다."""
        with self.engine.begin() as conn:
            row = conn.execute(
                text("""
                    SELECT id, task_id, document_name, vendor_name, device_name, api_version,
                           chunk_id, page_number, source_text, spec_json, score, review_status,
                           api_def_id, created_at, reviewed_at
                      FROM api_extraction
                     WHERE id = :id
                """),
                {'id': extraction_id},
            ).mappings().first()

        if row is None:
            return None

        return {**row, 'spec_json': json.loads(row['spec_json'])}

    def promote(self, extraction_id: int, spec: dict | None = None) -> tuple[int, int]:
        """
        사람이 승인한 추출을 운영 테이블로 옮기고 (api_definition.id, 점수)를 돌려준다.

        spec 을 주면 저장된 규격을 그것으로 갈아 끼운다 — 채점이 놓친 파라미터를
        사람이 고쳐 넣는 통로다. 고친 규격도 같은 잣대로 다시 채점한다. 사람 손을
        거쳤다고 점수를 면제하면 score 컬럼이 무슨 뜻인지 알 수 없게 된다.

        낮은 점수라도 승인은 막지 않는다. 사람의 판단이 채점보다 위다 —
        대신 그 점수가 reviewed_at 옆에 그대로 남는다.

        이미 승인된 건도 다시 부를 수 있다 — 기존 적재를 되돌린 뒤 수정본으로
        재적재한다. method/endpoint 를 고쳐 API 의 정체가 바뀌어도 옛 행이 남지 않는다.
        """
        with self.engine.begin() as conn:
            row = conn.execute(
                text('SELECT * FROM api_extraction WHERE id = :id'),
                {'id': extraction_id},
            ).mappings().first()

            if row is None:
                raise ExtractionNotFound(f"Extraction not found: {extraction_id}")
            if row['api_def_id'] is not None:
                self._demote(conn, extraction_id, row['api_def_id'])

            if spec is None:
                spec, score = json.loads(row['spec_json']), row['score']
            else:
                score = SpecScorer.score(spec, row['source_text'])

            chunk = {
                'text': row['source_text'],
                'chunk_id': row['chunk_id'],
                'metadata': {'page_number': row['page_number']},
            }

            api_def_id = self._promote(
                conn, spec, chunk, row['vendor_name'], row['device_name'], row['api_version'],
            )

            conn.execute(
                text("""
                    UPDATE api_extraction
                       SET review_status = 'approved', api_def_id = :api_def_id,
                           spec_json = :spec_json, score = :score, reviewed_at = NOW()
                     WHERE id = :id
                """),
                {
                    'api_def_id': api_def_id,
                    'spec_json': json.dumps(spec, ensure_ascii=False),
                    'score': score,
                    'id': extraction_id,
                },
            )

        return api_def_id, score

    def reject(self, extraction_id: int) -> None:
        """
        반려한다. 이미 승인된 건이면 운영 테이블에 올라간 것도 되돌린다 —
        상태만 바꾸면 api_definition 에 근거 잃은 행이 고아로 남는다.
        """
        with self.engine.begin() as conn:
            row = conn.execute(
                text('SELECT api_def_id FROM api_extraction WHERE id = :id'),
                {'id': extraction_id},
            ).mappings().first()

            if row is None:
                raise ExtractionNotFound(f"Extraction not found: {extraction_id}")
            if row['api_def_id'] is not None:
                self._demote(conn, extraction_id, row['api_def_id'])

            conn.execute(
                text("""
                    UPDATE api_extraction
                       SET review_status = 'rejected', reviewed_at = NOW()
                     WHERE id = :id
                """),
                {'id': extraction_id},
            )

    @staticmethod
    def _demote(conn, extraction_id: int, api_def_id: int) -> None:
        """
        이 추출의 적재를 되돌린다. 정의는 다른 추출이 참조하지 않을 때만 지운다 —
        같은 API 에 여러 문서가 기여했으면 남는다. api_schema 는 FK CASCADE 로 동반.
        """
        conn.execute(
            text('UPDATE api_extraction SET api_def_id = NULL WHERE id = :id'),
            {'id': extraction_id},
        )
        conn.execute(
            text("""
                DELETE FROM api_definition
                 WHERE id = :api_def_id
                   AND NOT EXISTS (
                       SELECT 1 FROM api_extraction e WHERE e.api_def_id = :api_def_id
                   )
            """),
            {'api_def_id': api_def_id},
        )

    def list_by_status(
            self,
            status: str,
            limit: int,
            offset: int = 0,
            document_name: str | None = None,
            vendor_name: str | None = None,
            device_name: str | None = None,
    ) -> tuple[list[dict], int]:
        """
        상태별 목록과 필터 적용 후 전체 건수를 함께 돌려준다.

        전체 건수는 UI 페이지네이션용이다 — 없으면 클라이언트가 끝 페이지를 모른다.
        필터는 값이 온 것만 건다. 컬럼명은 아래 튜플에 적힌 것만 쓰므로 SQL 조립이
        사용자 입력과 섞이지 않는다.
        """
        where = ['review_status = :status']
        params: dict = {'status': status}
        for column, value in (
                ('document_name', document_name),
                ('vendor_name', vendor_name),
                ('device_name', device_name),
        ):
            if value:
                where.append(f'{column} = :{column}')
                params[column] = value

        clause = ' AND '.join(where)
        with self.engine.begin() as conn:
            total = conn.execute(
                text(f'SELECT COUNT(*) FROM api_extraction WHERE {clause}'),
                params,
            ).scalar_one()

            rows = conn.execute(
                text(f"""
                    SELECT id, task_id, document_name, vendor_name, device_name, api_version,
                           chunk_id, page_number, source_text, spec_json, score, review_status,
                           api_def_id, created_at, reviewed_at
                      FROM api_extraction
                     WHERE {clause}
                     ORDER BY score DESC, id ASC
                     LIMIT :limit OFFSET :offset
                """),
                {**params, 'limit': limit, 'offset': offset},
            ).mappings().all()

        return [{**row, 'spec_json': json.loads(row['spec_json'])} for row in rows], total

    def stats(self) -> list[dict]:
        """
        문서별 검토 현황. UI 대시보드(문서 목록·상태 뱃지·평균점수)가 이 한 번으로 그려진다.

        SUM(조건) 은 MariaDB 에서 조건이 참인 행 수다 — 상태별 카운트를 조인 없이 얻는다.
        """
        with self.engine.begin() as conn:
            rows = conn.execute(
                text("""
                    SELECT document_name,
                           COUNT(*)                        AS total,
                           SUM(review_status = 'approved') AS approved,
                           SUM(review_status = 'pending')  AS pending,
                           SUM(review_status = 'rejected') AS rejected,
                           ROUND(AVG(score), 1)            AS avg_score
                      FROM api_extraction
                     GROUP BY document_name
                     ORDER BY avg_score ASC
                """),
            ).mappings().all()

        return [dict(row) for row in rows]

    #: 이 정의를 만든 출처 문서. 여러 문서가 같은 정의에 기여했으면 가장 최근 것.
    SOURCE_DOCUMENT = """
        (SELECT e.document_name FROM api_extraction e
          WHERE e.api_def_id = d.id ORDER BY e.id DESC LIMIT 1) AS document_name
    """

    def list_apis(
            self,
            vendor: str | None = None,
            device: str | None = None,
            method: str | None = None,
            q: str | None = None,
            document: str | None = None,
            limit: int = 50,
            offset: int = 0,
    ) -> tuple[list[dict], int]:
        """
        검토를 통과해 적재된 운영 API 목록과 전체 건수.

        벤더/장비/메서드/문서는 정확 일치, q 는 제목·엔드포인트 부분 일치다.
        """
        conditions = []
        params: dict = {}
        if vendor:
            conditions.append('v.name = :vendor')
            params['vendor'] = vendor
        if device:
            conditions.append('dv.name = :device')
            params['device'] = device
        if method:
            conditions.append('d.method = :method')
            params['method'] = method.strip().upper()
        if q:
            conditions.append('(d.title LIKE :q OR d.endpoint LIKE :q)')
            params['q'] = f'%{q}%'
        if document:
            conditions.append("""
                EXISTS (SELECT 1 FROM api_extraction e
                         WHERE e.api_def_id = d.id AND e.document_name = :document)
            """)
            params['document'] = document

        clause = f"WHERE {' AND '.join(conditions)}" if conditions else ''
        base = f"""
              FROM api_definition d
              JOIN device dv ON dv.id = d.device_id
              JOIN vendor v  ON v.id = dv.vendor_id
            {clause}
        """

        with self.engine.begin() as conn:
            total = conn.execute(
                text(f'SELECT COUNT(*) {base}'),
                params,
            ).scalar_one()

            rows = conn.execute(
                text(f"""
                    SELECT d.id, v.name AS vendor_name, dv.name AS device_name, d.api_version,
                           d.title, d.group_name, d.method, d.endpoint, d.created_at, d.updated_at,
                           {self.SOURCE_DOCUMENT}
                    {base}
                     ORDER BY v.name, dv.name, d.id
                     LIMIT :limit OFFSET :offset
                """),
                {**params, 'limit': limit, 'offset': offset},
            ).mappings().all()

        return [dict(row) for row in rows], total

    def get_api(self, api_def_id: int) -> dict | None:
        """운영 API 단건 + 요청/응답 스키마."""
        with self.engine.begin() as conn:
            row = conn.execute(
                text(f"""
                    SELECT d.id, v.name AS vendor_name, dv.name AS device_name, d.api_version,
                           d.title, d.group_name, d.method, d.endpoint, d.created_at, d.updated_at,
                           s.request, s.response,
                           {self.SOURCE_DOCUMENT}
                      FROM api_definition d
                      JOIN device dv ON dv.id = d.device_id
                      JOIN vendor v  ON v.id = dv.vendor_id
                      LEFT JOIN api_schema s ON s.api_def_id = d.id
                     WHERE d.id = :id
                """),
                {'id': api_def_id},
            ).mappings().first()

        if row is None:
            return None

        return {
            **row,
            'request': json.loads(row['request']) if row['request'] else None,
            'response': json.loads(row['response']) if row['response'] else None,
        }

    def purge_task(self, task_id: str) -> tuple[int, int]:
        """
        한 문서(task)의 데이터를 지우고 (추출 삭제 수, 정의 삭제 수)를 돌려준다.

        api_definition 은 여러 task 가 같은 행에 upsert 로 기여할 수 있다 —
        이 task 의 추출이 만든 정의라도 다른 task 의 추출이 아직 참조하면 남긴다.
        api_schema 는 FK CASCADE 로 정의와 함께 진다. vendor/device 마스터는
        건드리지 않는다 — 이름뿐인 행이라 남아도 해가 없다.
        """
        with self.engine.begin() as conn:
            def_ids = [
                row[0] for row in conn.execute(
                    text("""
                        SELECT DISTINCT api_def_id FROM api_extraction
                         WHERE task_id = :task_id AND api_def_id IS NOT NULL
                    """),
                    {'task_id': task_id},
                )
            ]

            extractions = conn.execute(
                text('DELETE FROM api_extraction WHERE task_id = :task_id'),
                {'task_id': task_id},
            ).rowcount

            definitions = 0
            if def_ids:
                definitions = conn.execute(
                    text("""
                        DELETE FROM api_definition
                         WHERE id IN :ids
                           AND NOT EXISTS (
                               SELECT 1 FROM api_extraction e WHERE e.api_def_id = api_definition.id
                           )
                    """).bindparams(bindparam('ids', expanding=True)),
                    {'ids': def_ids},
                ).rowcount

        return extractions, definitions

    def _promote(self, conn, spec: dict, chunk: dict, vendor_name: str, device_name: str, api_version: str) -> int:
        """마스터부터 스키마까지 한 줄기로 밀어 넣고 api_definition.id 를 돌려준다."""
        vendor_id = conn.execute(UPSERT_VENDOR, {'name': vendor_name}).lastrowid
        device_id = conn.execute(UPSERT_DEVICE, {'vendor_id': vendor_id, 'name': device_name}).lastrowid

        api_def_id = conn.execute(
            UPSERT_DEFINITION, {
                'device_id': device_id,
                'api_version': api_version,
                'title': (spec.get('title') or 'untitled')[:200],
                'group_name': self._group_name(chunk),
                'method': self._normalize_method(spec.get('method')),
                # 모델이 'None' / 'null' 을 문자열로 뱉는다. 그대로 두면 컬럼에 그 글자가 들어간다.
                'endpoint': SpecScorer.endpoint_path(spec.get('endpoint')),
            },
        ).lastrowid

        request = self._as_schema(spec.get('request_parameters'))
        response = self._as_schema(spec.get('response_parameters'))

        # API 목록·부록 청크는 endpoint 만 근거 잡혀 파라미터 없이 승인되는데, 그 조각이
        # 상세 절보다 뒤에 오는 문서면 무조건 덮어쓰기가 진짜 스키마를 지운다.
        statement = UPSERT_SCHEMA if self._has_parameters(request, response) else INSERT_SCHEMA_KEEP
        conn.execute(
            statement, {
                'api_def_id': api_def_id,
                'request': request,
                'response': response,
            },
        )

        return api_def_id

    @staticmethod
    def _has_parameters(request: str, response: str) -> bool:
        """직렬화된 스키마에 파라미터가 하나라도 있는가."""
        return request != '[]' or response != '[]'

    @staticmethod
    def _group_name(chunk: dict) -> str | None:
        """
        문서 안 섹션 경로의 최상위를 그룹명으로 쓴다. LLM 에 따로 물을 이유가 없다.

        headings 는 h1 부터 순서대로 쌓인 실제 경로다 — 빈 레벨은 들어 있지 않다.
        """
        headings = chunk.get('metadata', {}).get('headings') or []
        return headings[0][:100] if headings else None

    @staticmethod
    def _normalize_method(method: str | None) -> str | None:
        """CHECK 제약을 통과하지 못할 값은 NULL 로 떨군다. 적재 자체를 막을 값은 아니다."""
        if not method:
            return None

        normalized = method.strip().upper()
        return normalized if normalized in HTTP_METHODS else None

    @staticmethod
    def _as_schema(parameters: list[dict] | None) -> str:
        """
        근거(evidence)는 api_extraction 에만 남긴다. 운영 스키마에는 규격만 싣는다.

        type 과 raw_type 을 둘 다 싣는다 — 호출부는 정규화된 type 을 읽고,
        사람은 길이 제한이나 날짜 형식이 남아 있는 raw_type 을 본다.
        """
        return json.dumps(
            [
                {
                    'name': p.get('name'),
                    'type': p.get('type'),
                    'raw_type': p.get('raw_type', ''),
                    'required': p.get('required', False),
                    'description': p.get('description', ''),
                }
                for p in (parameters or [])
            ],
            ensure_ascii=False,
        )


if __name__ == '__main__':
    assert SpecRepository._normalize_method('get') == 'GET', 'Lowercase method must be upper-cased'
    assert SpecRepository._normalize_method('  Post ') == 'POST', 'Method must be trimmed'
    assert SpecRepository._normalize_method('TRACE') is None, 'Unknown method must fall back to NULL'
    assert SpecRepository._normalize_method(None) is None, 'Missing method must stay NULL'
    assert SpecRepository._normalize_method('') is None, 'Empty method must stay NULL'

    # 모델이 값 없음을 문자열로 뱉어도 컬럼에는 NULL 이 들어가야 한다.
    assert SpecRepository._normalize_method('NONE') is None, "'NONE' must not become a method"
    assert SpecScorer.endpoint_path('None') is None, "'None' must not reach the endpoint column"
    assert SpecScorer.endpoint_path('http(s)://[serverip]/mfd/api/token') == '/mfd/api/token', \
        'Host placeholder must be stripped before storage'

    # --- API 단위 병합 -------------------------------------------------------
    def _chunk(chunk_id, headings, text):
        return {'chunk_id': chunk_id, 'text': text, 'metadata': {'headings': headings, 'page_number': 1}}

    def _param(name):
        return {
            'name': name, 'type': 'string', 'raw_type': '', 'required': True,
            'description': '', 'evidence': f"| {name} | string |",
        }

    assert SpecRepository._api_group_key(
        _chunk(0, ['System', '4.3.2 ACL 추가', 'Query parameters'], ''),
    ) == 'System > 4.3.2 ACL 추가', 'Spec subsection must be stripped from the group key'
    assert SpecRepository._api_group_key(_chunk(0, [], '')) is None, 'No headings must yield no key'

    # 같은 API 의 세 조각. URL 은 첫 조각에만 있고, 파라미터는 흩어져 있다.
    path = ['4. System', '4.3.2 ACL 추가']
    grouped = SpecRepository._merge_by_api([
        (
            _chunk(1, path + ['URL'], 'POST http(s)://[serverip]/mfd/api/system/4/acls'),
            {
                'title': 'ACL 추가', 'method': 'POST', 'endpoint': '/mfd/api/system/4/acls',
                'request_parameters': [_param('srcAddr')], 'response_parameters': [],
            },
        ),
        (
            _chunk(2, path + ['Body'], '| dstPort | string |'),
            {
                'title': 'ACL 추가 요청', 'method': 'POST', 'endpoint': '/acl/add',
                'request_parameters': [_param('dstPort'), _param('srcAddr')], 'response_parameters': [],
            },
        ),
        (
            _chunk(3, path + ['Returns'], '| result | string |'),
            {
                'title': 'ACL 추가 응답', 'method': None, 'endpoint': None,
                'request_parameters': [], 'response_parameters': [_param('result')],
            },
        ),
    ])

    assert len(grouped) == 1, f"Three fragments of one API must merge: {len(grouped)}"
    chunks, spec = grouped[0]
    assert len(chunks) == 3, 'Merged entry must keep every source chunk'
    assert spec['title'] == '4.3.2 ACL 추가', 'Title must come from the heading path, not the LLM'
    assert spec['endpoint'] == '/mfd/api/system/4/acls', 'Grounded endpoint must win over the invented one'
    assert [p['name'] for p in spec['request_parameters']] == ['srcAddr', 'dstPort'], \
        'Request parameters must union without duplicates'
    assert [p['name'] for p in spec['response_parameters']] == ['result'], 'Response params must survive'

    # 헤더 경로가 다르면 합치지 않는다.
    separate = SpecRepository._merge_by_api([
        (_chunk(1, ['A', 'API 하나'], 'x'), {'title': 'a', 'request_parameters': [], 'response_parameters': []}),
        (_chunk(2, ['A', 'API 둘'], 'y'), {'title': 'b', 'request_parameters': [], 'response_parameters': []}),
    ])
    assert len(separate) == 2, 'Different APIs must not merge'

    # 헤더 경로가 없는 청크는 단독으로 남는다.
    orphan = SpecRepository._merge_by_api([
        (_chunk(1, [], 'x'), {'title': 'a', 'request_parameters': [], 'response_parameters': []}),
        (_chunk(2, [], 'y'), {'title': 'b', 'request_parameters': [], 'response_parameters': []}),
    ])
    assert len(orphan) == 2, 'Chunks without a heading path must stay separate'

    # 헤더 하나에 API 가 둘 있으면 엔드포인트별로 나뉜다. 합치면 하나를 잃는다.
    both = ['5. Policy', '5.2.4 적용/취소']
    two_apis = SpecRepository._merge_by_api([
        (
            _chunk(1, both, 'PUT http(s)://[serverip]/mfd/api/policy/user-black-list/apply'),
            {
                'title': '적용', 'method': 'PUT', 'endpoint': '/mfd/api/policy/user-black-list/apply',
                'request_parameters': [_param('a')], 'response_parameters': [],
            },
        ),
        (
            _chunk(2, both, 'PUT http(s)://[serverip]/mfd/api/policy/user-black-list/cancel'),
            {
                'title': '취소', 'method': 'PUT', 'endpoint': '/mfd/api/policy/user-black-list/cancel',
                'request_parameters': [_param('b')], 'response_parameters': [],
            },
        ),
    ])
    endpoints = sorted(s['endpoint'] for _, s in two_apis)
    assert endpoints == [
        '/mfd/api/policy/user-black-list/apply',
        '/mfd/api/policy/user-black-list/cancel',
    ], \
        f"Two APIs under one heading must both survive: {endpoints}"

    # 그 경우 엔드포인트를 못 찾은 조각은 어디에도 붙이지 않는다.
    with_orphan = SpecRepository._merge_by_api([
        (
            _chunk(1, both, 'PUT http(s)://[serverip]/mfd/api/x/apply'),
            {
                'title': '적용', 'method': 'PUT', 'endpoint': '/mfd/api/x/apply',
                'request_parameters': [], 'response_parameters': [],
            },
        ),
        (
            _chunk(2, both, 'PUT http(s)://[serverip]/mfd/api/x/cancel'),
            {
                'title': '취소', 'method': 'PUT', 'endpoint': '/mfd/api/x/cancel',
                'request_parameters': [], 'response_parameters': [],
            },
        ),
        (
            _chunk(3, both, '| note | string |'),
            {
                'title': '설명', 'method': None, 'endpoint': None,
                'request_parameters': [_param('note')], 'response_parameters': [],
            },
        ),
    ])
    assert len(with_orphan) == 3, f"Ungrounded fragment must stay alone: {len(with_orphan)}"

    assert SpecRepository._group_name({'metadata': {'headings': ['장비 API', '조회']}}) == '장비 API'
    assert SpecRepository._group_name({'metadata': {'headings': ['조회']}}) == '조회'
    assert SpecRepository._group_name({'metadata': {'headings': []}}) is None, 'No headings must yield NULL'
    assert SpecRepository._group_name({'metadata': {}}) is None, 'Missing key must yield NULL'
    assert len(SpecRepository._group_name({'metadata': {'headings': ['가' * 200]}})) == 100, \
        'group_name must fit the VARCHAR(100) column'

    schema = json.loads(
        SpecRepository._as_schema([
            {
                'name': 'deviceId', 'type': 'string', 'raw_type': 'varchar(64)',
                'required': True, 'description': '식별자', 'evidence': '| deviceId |',
            },
        ]),
    )
    # --- 사람이 고쳐 보낸 규격 -------------------------------------------------
    # 승인 본문은 ApiSpec 으로 검증한 뒤 그대로 채점기와 적재기로 넘어간다.
    # 필드 이름이 어긋나면 여기서 걸린다.
    from src.core.extractor.SpecModels import ApiSpec
    from src.core.extractor.SpecModels import Parameter

    corrected = ApiSpec(
        title='ACL 조회', method='GET', endpoint='/mfd/api/system/acls',
        request_parameters=[
            Parameter(
                name='page', type='integer', raw_type='int',
                required=False, description='페이지', evidence='| page | int |',
            ),
        ],
        response_parameters=[],
    ).model_dump()
    corrected_source = 'GET http(s)://[serverip]/mfd/api/system/acls\n| page | int |'

    assert SpecScorer.score(corrected, corrected_source) == 100, \
        'Corrected spec must re-score against the stored source text'
    assert json.loads(SpecRepository._as_schema(corrected['request_parameters']))[0]['name'] == 'page', \
        'Corrected spec must project into the operational schema'

    # 사람이 고쳤다고 채점을 면제하지 않는다. 근거 없는 수정은 점수로 드러나야 한다.
    corrected['request_parameters'][0]['evidence'] = '문서에 없는 근거'
    assert SpecScorer.score(corrected, corrected_source) == 0, \
        'Ungrounded human edit must still score zero'

    assert schema[0]['name'] == 'deviceId', 'Parameter name must survive'
    assert schema[0]['type'] == 'string', 'Normalized type must survive'
    assert schema[0]['raw_type'] == 'varchar(64)', 'Document notation must survive'
    assert 'evidence' not in schema[0], 'Evidence must not leak into the operational schema'
    assert SpecRepository._as_schema(None) == '[]', 'Missing parameters must yield an empty array'

    # 빈 스키마 판정은 _as_schema 의 직렬화 형태('[]')에 기대고 있다. 직렬화가 바뀌면
    # 덮어쓰기 가드(_promote 의 INSERT_SCHEMA_KEEP 분기)가 조용히 무력화되므로 여기서 묶는다.
    assert SpecRepository._as_schema(None) == '[]', 'Empty schema must serialize to []'
    assert not SpecRepository._has_parameters(SpecRepository._as_schema(None), SpecRepository._as_schema([])), \
        'Both-empty schema must be detected as parameterless'
    assert SpecRepository._has_parameters(SpecRepository._as_schema([_param('a')]), SpecRepository._as_schema(None)), \
        'One-sided schema must count as having parameters'

    print('OK: method normalization, group naming, schema projection')
