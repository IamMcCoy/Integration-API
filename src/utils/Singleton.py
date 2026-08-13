from __future__ import annotations


class Singleton(type):
    _instance = None

    def __call__(cls, *args, **kwargs):
        if not isinstance(cls._instance, cls):
            cls._instance = super().__call__(*args, **kwargs)
        return cls._instance
