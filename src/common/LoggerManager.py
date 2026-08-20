from __future__ import annotations

import logging
from logging import Logger

from src.utils.Singleton import Singleton

LOG_FORMAT = '%(asctime)s %(levelname)-5s - %(processName)-15s - %(filename)-22s:%(lineno)-3s - %(message)s'


class LoggerManager(metaclass=Singleton):
    """
    프로세스 하나에 로거 하나. 앱은 asyncio 단일 프로세스로 돌고 워커도 태스크라,
    표준 라이브러리 설정 한 번이면 된다.
    """

    def __init__(self):
        logging.basicConfig(level=logging.INFO, format=LOG_FORMAT)
        self.logger = logging.getLogger('integration-api')

    @staticmethod
    def get() -> Logger:
        return LoggerManager().logger
