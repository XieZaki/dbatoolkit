from __future__ import annotations

from pathlib import Path

import pytest

_DATABASE_MARKERS = {
    "test_mysql": "mysql",
    "test_mysql_privileges": "mysql",
    "test_postgresql": "postgresql",
    "test_sqlserver": "sqlserver",
    "test_oracle": "oracle",
}


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """Give existing flat tests a default layer without relocating the suite."""
    for item in items:
        path = Path(str(item.path))
        module_name = path.stem
        database_marker = _DATABASE_MARKERS.get(module_name)
        if database_marker:
            item.add_marker(getattr(pytest.mark, database_marker))

        if not any(
            list(item.iter_markers(name)) for name in ("unit", "integration", "system")
        ):
            item.add_marker(pytest.mark.unit)
