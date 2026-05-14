"""Tests for the main router aggregator."""

from aiogram import Router

from src.bot.handlers import get_main_router


def test_get_main_router_returns_router_with_subrouters():
    # Routers can only be attached once, so build once and check both invariants.
    r = get_main_router()
    assert isinstance(r, Router)
    assert len(r.sub_routers) >= 10
