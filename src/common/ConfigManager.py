from __future__ import annotations

import os
from pathlib import Path
from xml.etree import ElementTree

from src.utils.Singleton import Singleton

CONF_FILENAME = 'ia-conf.xml'


class ConfigManager(metaclass=Singleton):
    """
    conf/ia-conf.xml 의 <property><name>/<value> 쌍을 dict 로 들고 있는다.

    컨테이너에서는 작업 디렉토리 아래 conf/ 가 볼륨으로 마운트된다. 없으면
    소스 기준 상대 경로로 떨어진다 — 로컬에서 아무 데서나 모듈을 실행할 수 있게.
    """

    def __init__(self):
        path = Path.cwd() / 'conf' / CONF_FILENAME
        if not path.is_file():
            path = Path(__file__).resolve().parents[2] / 'conf' / CONF_FILENAME

        root = ElementTree.parse(path).getroot()
        self.conf = {
            p.findtext('name'): (p.findtext('value') or '')
            for p in root.findall('property')
        }

    def get(self, key, default=None) -> str | None:
        """
        설정값을 읽는다. 같은 이름의 `IA_` 환경변수가 있으면 그쪽이 이긴다.

        테스트할 때 XML 을 갈아끼우지 않고 `.env` 로 덮어쓰기 위한 통로다.
        접두사를 붙이는 이유는 `REDIS_HOST` 같은 흔한 이름이 남의 환경변수와
        충돌하는 걸 막기 위해서다.
        """
        override = os.environ.get(f"IA_{key.upper()}")
        if override is not None:
            return override

        return self.conf.get(key, default)


if __name__ == '__main__':
    manager = ConfigManager()

    # XML 에 없는 키도 환경변수로 들어온다.
    os.environ['IA_SELF_CHECK_KEY'] = 'from-env'
    assert manager.get('self_check_key') == 'from-env', 'Env override must be readable'
    assert manager.get('self_check_key', 'fallback') == 'from-env', 'Env must beat the default'

    # XML 에 있는 키는 환경변수가 이긴다. 값 자체는 검사하지 않는다 — 설정 파일 내용에
    # 의존하면 자체 점검이 운영 설정에 묶인다.
    baseline = manager.get('redis_host')
    os.environ['IA_REDIS_HOST'] = 'overridden-host'
    assert manager.get('redis_host') == 'overridden-host', 'Env must beat the XML value'

    del os.environ['IA_REDIS_HOST']
    assert manager.get('redis_host') == baseline, 'XML value must return once env is gone'

    assert manager.get('no_such_key_anywhere', 'fallback') == 'fallback', 'Default must apply'
    assert manager.get('no_such_key_anywhere') is None, 'Missing key without default must be None'

    # 빈 <value/> 는 빈 문자열이지 None 이 아니다.
    assert manager.get('redis_password') == '', 'Empty value must be an empty string'

    del os.environ['IA_SELF_CHECK_KEY']
    print(f"OK: {len(manager.conf)} settings, env override precedence")
