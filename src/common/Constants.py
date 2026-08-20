from __future__ import annotations

from src.common.ConfigManager import ConfigManager


class Constants:
    """conf/ia-conf.xml 값을 타입 맞춰 노출한다. 인스턴스를 만들지 않는다."""

    __config_manager = ConfigManager()

    REDIS_HOST: str = __config_manager.get('redis_host', 'localhost')
    REDIS_PORT: int = int(__config_manager.get('redis_port', '6379'))
    REDIS_DB: int = int(__config_manager.get('redis_db', '0'))
    REDIS_PASSWORD: str | None = __config_manager.get('redis_password') or None

    UPLOAD_STAGING_DIR: str = __config_manager.get('upload_staging_dir', '/tmp/ia-staging')
    UPLOAD_MAX_BYTES: int = int(__config_manager.get('upload_max_bytes', str(20 * 1024 * 1024)))
    REVIEW_RETENTION_DAYS: int = int(__config_manager.get('review_retention_days', '30'))

    TASK_TTL_SECONDS: int = int(__config_manager.get('task_ttl_seconds', '86400'))
    TASK_QUEUE_BLOCK_SECONDS: int = int(__config_manager.get('task_queue_block_seconds', '5'))
    WORKER_COUNT: int = int(__config_manager.get('worker_count', '4'))

    #: API 키는 설정 파일이 아니라 OPENAI_API_KEY 환경변수로 온다.
    #: base_url 이 비면 SDK 기본값(공식 엔드포인트)을 쓴다.
    OPENAI_BASE_URL: str | None = __config_manager.get('openai_base_url') or None
    OPENAI_FILTER_MODEL: str = __config_manager.get('openai_filter_model', 'gpt-4o-mini')
    OPENAI_EXTRACT_MODEL: str = __config_manager.get('openai_extract_model', 'gpt-4o-mini')
    OPENAI_MAX_CONCURRENCY: int = int(__config_manager.get('openai_max_concurrency', '8'))

    #: 비우면 인자를 아예 보내지 않는다. 추론 모델은 이 값을 받지 않는다.
    _temperature: str = (__config_manager.get('openai_temperature') or '').strip()
    OPENAI_TEMPERATURE: float | None = float(_temperature) if _temperature else None

    FILTER_PROMPT: str = (__config_manager.get('filter_prompt') or '').strip()
    EXTRACT_PROMPT: str = (__config_manager.get('extract_prompt') or '').strip()
    IDENTIFY_PROMPT: str = (__config_manager.get('identify_prompt') or '').strip()

    SCORE_AUTO_THRESHOLD: int = int(__config_manager.get('score_auto_threshold', '90'))
    SCORE_REVIEW_THRESHOLD: int = int(__config_manager.get('score_review_threshold', '70'))

    DOCUMENT_ENDPOINT: str = '/documents'
    TASK_ENDPOINT: str = '/tasks'
    EXTRACTION_ENDPOINT: str = '/extractions'
    API_ENDPOINT: str = '/apis'
