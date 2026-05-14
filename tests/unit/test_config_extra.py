"""Extra config coverage: GIF_FPS fallback on invalid env."""

import importlib
import os
from unittest.mock import patch


def test_gif_fps_falls_back_to_30_on_non_int():
    with patch.dict(os.environ, {"GIF_FPS": "not-a-number"}):
        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.GIF_FPS == 30


def test_gif_fps_min_clamps_to_one():
    with patch.dict(os.environ, {"GIF_FPS": "0"}):
        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.GIF_FPS == 1


def test_gif_fps_uses_env_value():
    with patch.dict(os.environ, {"GIF_FPS": "15"}):
        import src.config as cfg

        importlib.reload(cfg)
        assert cfg.GIF_FPS == 15
