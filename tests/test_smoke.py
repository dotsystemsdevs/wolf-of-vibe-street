"""Smoke tests: every module imports, scaffold is sound. No business logic yet."""

from importlib import import_module

import pytest

MODULES = [
    "agents",
    "api",
    "backtest",
    "data",
    "execution",
    "features",
    "memory",
    "risk",
    "signals",
    "strategies",
    "tools",
    "ui",
    "workers",
]


@pytest.mark.parametrize("name", MODULES)
def test_module_imports(name: str) -> None:
    import_module(name)


def test_python_version() -> None:
    import sys

    assert sys.version_info >= (3, 12), "Project requires Python >= 3.12 (D-1)"
