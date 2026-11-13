"""Shared fixtures. `workspace` is a private copy of tests/fixtures/workspace (the fictional "Alex Example").

Scripts read their paths when they are imported, so code that needs a workspace runs in a subprocess that inherits
OHMYJOB_WORKSPACE from this fixture (the functions that take `root` can be called in the test process).
"""
import os
import shutil
from pathlib import Path

import pytest



def pytest_configure(config):
    """The early liveness check asks real sites: off in every test (a test that wants it unsets this and stubs the fetch)."""
    os.environ["OHMYJOB_NO_LIVENESS"] = "1"


FIXTURE_WORKSPACE = Path(__file__).parent / "fixtures" / "workspace"


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    root = tmp_path / "workspace"
    shutil.copytree(FIXTURE_WORKSPACE, root)
    for name in ("Pending-Review", "Needs-Attention", "Submitted"):
        (root / name).mkdir()
    monkeypatch.setenv("OHMYJOB_WORKSPACE", str(root))
    monkeypatch.delenv("CLAUDE_PROJECT_DIR", raising=False)
    return root
