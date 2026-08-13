-- =============================================================================
-- API 규격 관리 스키마 (MariaDB)
--
-- Powered by Seculayer © 2026 AI Team, R&D Center.
--
-- 적재 흐름:
--   추출 결과는 전부 api_extraction 에 먼저 쌓인다. 점수가 임계값을 넘은 것만
--   api_definition + api_schema 로 옮겨진다. 나머지는 사람이 승인할 때까지 대기한다.
--
--   api_extraction ──(승인)──> api_definition ──> api_schema
-- =============================================================================

-- -----------------------------------------------------------------------------
-- vendor : 제조사 마스터
-- -----------------------------------------------------------------------------
CREATE TABLE vendor (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    name        VARCHAR(100) NOT NULL,
    description TEXT         NULL,
    CONSTRAINT uk_vendor_name UNIQUE (name)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;


-- -----------------------------------------------------------------------------
-- device : 벤더가 보유한 장비 마스터
--
-- 모델명은 벤더 안에서만 고유하다. 전역 고유로 두면 서로 다른 벤더가 같은
-- 모델명을 쓸 수 없다.
-- -----------------------------------------------------------------------------
CREATE TABLE device (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    vendor_id   INT          NOT NULL,
    name        VARCHAR(100) NOT NULL,
    description TEXT         NULL,
    CONSTRAINT uk_device_vendor_name UNIQUE (vendor_id, name),
    CONSTRAINT fk_device_vendor
        FOREIGN KEY (vendor_id) REFERENCES vendor (id)
        ON DELETE RESTRICT ON UPDATE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;


-- -----------------------------------------------------------------------------
-- api_definition : 디바이스별·버전별 개별 API 명세
--
-- api_version 을 별도 마스터 테이블로 두지 않는다. 버전 체계는 벤더마다 다르고
-- (SECUI v4.3, FortiOS v5.6), 전역 유니크로 묶으면 두 벤더가 같은 버전 문자열을
-- 쓸 수 없다. 같은 문서의 버전 쌍이 실제로 존재하므로 title 도 버전과 함께 묶는다.
-- -----------------------------------------------------------------------------
CREATE TABLE api_definition (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    device_id   INT          NOT NULL,
    api_version VARCHAR(50)  NOT NULL,
    title       VARCHAR(200) NOT NULL,
    group_name  VARCHAR(100) NULL,
    method      VARCHAR(20)  NULL,
    endpoint    VARCHAR(500) NULL,
    created_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at  TIMESTAMP    NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,

    -- API 의 정체는 호출 주소다. 제목이 아니다.
    --
    -- title 은 청크의 헤더 경로에서 온다. 한 절에 API 가 여럿 있는 문서가 흔해서
    -- (`5.2.4 적용/취소` 아래 apply 와 cancel) 제목을 유니크 키에 쓰면 서로 다른 API 가
    -- 한 행으로 뭉개지고, 나중에 적재된 것이 앞의 것을 덮어쓴다.
    --
    -- endpoint 가 NULL 이면 제목으로 물러선다. NULL 을 그대로 키에 넣으면 SQL 이
    -- NULL 끼리 다르다고 봐서 같은 문서를 다시 올릴 때마다 행이 하나씩 늘어난다.
    api_key     VARCHAR(540)
                AS (CONCAT(COALESCE(method, '-'), ' ',
                           COALESCE(endpoint, CONCAT('#', title)))) STORED,

    CONSTRAINT uk_api_definition UNIQUE (device_id, api_version, api_key),
    CONSTRAINT fk_api_definition_device
        FOREIGN KEY (device_id) REFERENCES device (id)
        ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT ck_api_definition_method
        CHECK (method IS NULL OR method IN
               ('GET', 'POST', 'PUT', 'PATCH', 'DELETE', 'HEAD', 'OPTIONS'))
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;


-- -----------------------------------------------------------------------------
-- api_schema : API 정의의 요청/응답 파라미터 (1:1)
-- -----------------------------------------------------------------------------
CREATE TABLE api_schema (
    id         INT AUTO_INCREMENT PRIMARY KEY,
    api_def_id INT       NOT NULL,
    request    JSON      NOT NULL,
    response   JSON      NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uk_api_schema_def UNIQUE (api_def_id),
    CONSTRAINT fk_api_schema_definition
        FOREIGN KEY (api_def_id) REFERENCES api_definition (id)
        ON DELETE CASCADE ON UPDATE CASCADE
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;


-- -----------------------------------------------------------------------------
-- api_extraction : 추출 결과 스테이징 + 사람 검토 대기열
--
-- source_text 를 여기 복사해 두는 이유:
--   TaskWorker 가 작업이 끝나면 finally 에서 업로드 원본과 변환 산출물을 통째로
--   지운다. 원문을 남기지 않으면 사람이 승인 화면에서 대조할 근거가 아무 데도 없다.
--
-- vendor/device 를 id 가 아니라 이름으로 들고 있는 이유:
--   반려된 추출은 마스터에 행을 만들지 않는다. 승인 시점에 upsert 한다.
-- -----------------------------------------------------------------------------
CREATE TABLE api_extraction (
    id            BIGINT AUTO_INCREMENT PRIMARY KEY,
    task_id       VARCHAR(32)      NOT NULL,
    document_name VARCHAR(64)      NOT NULL,
    vendor_name   VARCHAR(100)     NOT NULL,
    device_name   VARCHAR(100)     NOT NULL,
    api_version   VARCHAR(50)      NOT NULL,
    chunk_id      INT              NOT NULL,
    page_number   INT              NOT NULL,
    source_text   TEXT             NOT NULL,
    spec_json     JSON             NOT NULL,
    score         TINYINT UNSIGNED NOT NULL,
    review_status VARCHAR(20)      NOT NULL,
    api_def_id    INT              NULL,
    created_at    TIMESTAMP        NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at   TIMESTAMP        NULL,
    CONSTRAINT ck_api_extraction_status
        CHECK (review_status IN ('approved', 'pending', 'rejected')),
    CONSTRAINT ck_api_extraction_score
        CHECK (score BETWEEN 0 AND 100),
    CONSTRAINT fk_api_extraction_definition
        FOREIGN KEY (api_def_id) REFERENCES api_definition (id)
        ON DELETE SET NULL ON UPDATE CASCADE,
    KEY idx_api_extraction_review (review_status)
) ENGINE = InnoDB DEFAULT CHARSET = utf8mb4 COLLATE = utf8mb4_unicode_ci;
