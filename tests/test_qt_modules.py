"""アプリが使う Qt のモジュールを確かめる。

依存は PySide6-Essentials だけなので、アプリ本体(src)とアイコン生成(tools)は
QtCore / QtGui / QtWidgets 以外を import しない。開発環境には紹介動画用に Addons も
入っているため、使えてしまっても気づけるようにここで検査する。
"""

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ALLOWED = {"QtCore", "QtGui", "QtWidgets"}
PATTERN = re.compile(r"^\s*(?:from|import)\s+PySide6\.(Qt\w+)", re.MULTILINE)


def test_app_uses_only_essentials_modules() -> None:
    used: dict[str, list[str]] = {}
    for folder in ("src", "tools"):
        for path in (ROOT / folder).rglob("*.py"):
            for module in PATTERN.findall(path.read_text(encoding="utf-8")):
                used.setdefault(module, []).append(str(path.relative_to(ROOT)))
    assert used, "Qt の import が見つからない(検査の対象を間違えている)"
    extra = {module: files for module, files in used.items() if module not in ALLOWED}
    assert not extra, f"Essentials の想定外のモジュールを使っている: {extra}"
