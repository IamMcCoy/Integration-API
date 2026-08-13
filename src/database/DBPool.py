"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

import urllib.parse
from configparser import ConfigParser
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from src.utils.AES256 import AES256


class DBPool:
    """
    conf/db.properties 를 읽어 SQLAlchemy 엔진을 만든다.

    설정이 ia-conf.xml 과 갈라져 있는 이유는 자격증명이 AES256 으로 감싸여 있어서다.

    두 가지 방식으로 쓴다 — SpecRepository 는 `.pool`(Engine)에 Core 쿼리를 던지고,
    DBHandler 를 거치는 쪽은 `get_connection()` 으로 ORM 세션을 받는다.
    """

    def __init__(self, db_config=None):
        if db_config is None:
            path = Path(__file__).resolve().parents[2] / 'conf' / 'db.properties'

            parser = ConfigParser()
            parser.read(path)
            config = parser['database']

            db_config = {
                'url': config['url'],
                'user': AES256().decrypt(config.get('username')),
                'password': AES256().decrypt(config.get('password')),
                'pool_size': int(config.get('pool_size', '5')),
                'pool_recycle': int(config.get('pool_recycle', '500')),
            }

        self.db_config = db_config
        passwd = urllib.parse.quote_plus(db_config['password'])

        self.pool = create_engine(
            f"mariadb+pymysql://{db_config['user']}:{passwd}@{db_config['url']}",
            pool_size=db_config['pool_size'],
            pool_recycle=db_config['pool_recycle'],
            max_overflow=10,
            # 유휴 커넥션이 MariaDB 의 wait_timeout 에 끊긴 뒤 첫 쿼리가 실패하는 걸 막는다.
            # 체크아웃 시점에 핑을 던지고 죽었으면 조용히 새로 잡는다.
            pool_pre_ping=True,
        )
        self.session = sessionmaker(bind=self.pool)

    def get_connection(self):
        """ORM 세션. Core 로 쓰려면 `.pool` 을 직접 쓴다."""
        return self.session()
