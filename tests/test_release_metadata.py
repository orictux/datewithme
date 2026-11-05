"""Verify that published build metadata uses one version and safe defaults."""

from pathlib import Path
import re
import tomllib


ROOT = Path(__file__).resolve().parents[1]


def test_release_version_is_consistent():
    metadata = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    version = (ROOT / "VERSION").read_text(encoding="utf-8").strip()
    assert version == metadata["project"]["version"] == "1.4.9"
    app = (ROOT / "app/main.py").read_text(encoding="utf-8")
    index = (ROOT / "web/index.html").read_text(encoding="utf-8")
    assert f"'version':'{version}'" in app
    assert f"v={version}" in index
    assert (ROOT / "RELEASE_NOTES.md").is_file()


def test_release_has_no_sensitive_runtime_files():
    for name in [".env", "setup.token", "database.key", "database.enc", "vapid-private.pem"]:
        assert not list(ROOT.rglob(name)), f"Do not distribute {name}"
    assert "Copyright (c) 2026 Orictux" in (ROOT / "LICENSE").read_text(encoding="utf-8")
