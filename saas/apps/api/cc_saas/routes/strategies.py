from __future__ import annotations

from fastapi import APIRouter

from ..strategies import public_catalog

router = APIRouter(tags=["strategies"])


@router.get("/api/strategies")
def list_strategies():
    return public_catalog()
