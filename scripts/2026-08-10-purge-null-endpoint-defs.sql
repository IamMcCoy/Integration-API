-- 엔드포인트 없이 적재된 api_definition 정리 (2026-08-10 실측에서 발견)
--
-- SpecScorer 가 endpoint 없는 규격을 파라미터 근거만으로 통과시키던 구멍으로
-- 들어온 행들이다. 공통 응답 포맷·부록 표 조각이라 호출할 수 없다.
-- 코드 수정(근거 있는 endpoint 없으면 0점) 이후의 새 실행에는 생기지 않는다.
--
-- 실행:
--   kubectl exec -i -n integration-api <mariadb-pod> -- \
--       mariadb -utest -p'<pw>' integration_api < scripts/2026-08-10-purge-null-endpoint-defs.sql

UPDATE api_extraction e JOIN api_definition ad ON ad.id = e.api_def_id
   SET e.review_status = 'rejected', e.score = 0, e.api_def_id = NULL
 WHERE ad.endpoint IS NULL;

DELETE s FROM api_schema s JOIN api_definition ad ON ad.id = s.api_def_id
 WHERE ad.endpoint IS NULL;

DELETE FROM api_definition WHERE endpoint IS NULL;

SELECT (SELECT COUNT(*) FROM api_definition)                                    AS defs,
       (SELECT COUNT(*) FROM api_schema)                                        AS schema_rows,
       (SELECT COUNT(*) FROM api_extraction WHERE review_status = 'approved')   AS approved,
       (SELECT COUNT(*) FROM api_extraction WHERE review_status = 'rejected')   AS rejected;
