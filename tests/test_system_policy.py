from __future__ import annotations

import pytest
from test_system_cli import is_local_test_instance


@pytest.mark.unit
@pytest.mark.parametrize(
    ("group", "tags", "expected"),
    [
        ("local-test", [], True),
        ("LOCAL-TEST", [], True),
        (None, ["test"], True),
        (None, ["docker"], True),
        (None, ["local-test"], True),
        ("production", ["24x7"], False),
        (None, [], False),
    ],
)
def test_system_inventory_safety_gate(group: str | None, tags: list[str], expected: bool) -> None:
    assert is_local_test_instance(group, tags) is expected
