"""Test __main__.py entry point."""
import runpy
from unittest.mock import patch

import pytest


def test_main_module_import():
    import geo_manager.__main__
    assert hasattr(geo_manager.__main__, "main")


def test_main_module_entry_point_calls_main():
    with patch("geo_manager.main.main", side_effect=SystemExit(0)):
        with pytest.raises(SystemExit):
            runpy.run_module("geo_manager.__main__", run_name="__main__")
