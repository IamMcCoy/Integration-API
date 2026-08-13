"""
Author : Wonjun Kim
e-mail : wonjun.kim@seculayer.com
Powered by Seculayer © 2026 AI Team, R&D Center.
"""
from __future__ import annotations

from fastapi import APIRouter

from src.api.routes import Api
from src.api.routes import Document
from src.api.routes import Extraction
from src.api.routes import Task

#: 앱에 붙이는 단일 진입점. 엔드포인트는 src/api/routes/ 아래에 정의하고
#: 여기서 합치기만 한다. 라우터가 늘어나면 import 와 include 각각 한 줄씩 추가한다.
router = APIRouter()

router.include_router(Document.router)
router.include_router(Task.router)
router.include_router(Extraction.router)
router.include_router(Api.router)
