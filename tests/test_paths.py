"""paths.py のテスト。開発実行と exe 実行で基準フォルダが切り替わることを確かめる。"""

import sys
from typing import TYPE_CHECKING

from neko_gantcha import paths

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def test_dev_app_root_is_project_root() -> None:
    root = paths.app_root()
    assert (root / "pyproject.toml").is_file()
    assert paths.data_dir() == root / "data"
    assert paths.db_path() == root / "data" / "neko_gantcha.db"


def test_dev_resource_path_points_into_package() -> None:
    qss = paths.resource_path("ui", "resources", "style.qss")
    assert qss.is_file()


def test_frozen_paths(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    exe_dir = tmp_path / "NekoGantcha"
    internal = exe_dir / "_internal"
    internal.mkdir(parents=True)
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(exe_dir / "NekoGantcha.exe"))
    monkeypatch.setattr(sys, "_MEIPASS", str(internal), raising=False)

    assert paths.app_root() == exe_dir.resolve()
    assert paths.data_dir() == exe_dir.resolve() / "data"
    assert (
        paths.resource_path("data", "migrations")
        == internal / "neko_gantcha" / "data" / "migrations"
    )


def test_ensure_data_dir_creates_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "executable", str(tmp_path / "NekoGantcha.exe"))

    created = paths.ensure_data_dir()

    assert created == tmp_path.resolve() / "data"
    assert created.is_dir()
    assert list(created.iterdir()) == []
