"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

from sqlalchemy.orm import Session

from src.common.LoggerManager import LoggerManager
from src.database.DBPool import DBPool
from src.utils.Singleton import Singleton

RETRY_MAX = 5


class DBHandler(metaclass=Singleton):
    """
    ORM 세션을 재시도와 함께 내준다.

    Core 로 적재하는 SpecRepository 는 `DBPool().pool`(Engine)을 직접 쓴다.
    이쪽은 세션 단위로 다루는 코드를 위한 통로다.
    """

    def __init__(self, db_config=None):
        self.db_pool = DBPool(db_config)
        self.logger = LoggerManager.get()

    def get_connection(self) -> Session | None:
        """
        세션을 못 얻으면 None 을 돌려준다 — 호출자가 반드시 확인해야 한다.

        엔진이 pool_pre_ping 으로 죽은 커넥션을 이미 걸러내므로, 여기까지 오는 건
        DB 가 실제로 안 떠 있는 경우다.
        """
        for attempt in range(RETRY_MAX):
            try:
                return self.db_pool.get_connection()
            except Exception as e:
                self.logger.error(
                    f"Failed to get connection (attempt {attempt + 1}/{RETRY_MAX}): {str(e)}",
                    exc_info=True,
                )

        self.logger.error('Max retries reached. Returning None for connection.')
        return None
