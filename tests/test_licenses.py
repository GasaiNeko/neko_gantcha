"""配布物に添えるライセンス文書(licenses/)のテスト。

exe には Qt / PySide6(LGPL v3)や Python などが入るので、その全文と一覧を同梱する。
依存のバージョンを上げたのに一覧を直し忘れないよう、バージョンの記載も確かめる。
"""

import hashlib
import platform
import re
from pathlib import Path

import PySide6
import shiboken6

ROOT = Path(__file__).resolve().parents[1]
LICENSES = ROOT / "licenses"

# gnu.org で配布されている原文と同じ内容か(MD5)
GNU_TEXTS = {
    "LGPL-3.0.txt": "3000208d539ec061b899bce1d9ce9404",
    "GPL-3.0.txt": "1ebbd3e34237af26da5dc08a4e440464",
}


def test_license_files_exist() -> None:
    assert (ROOT / "LICENSE").read_text(encoding="utf-8").startswith("MIT License")
    for name in ("THIRD_PARTY_NOTICES.md", "Python-LICENSE.txt", "Apache-2.0.txt", *GNU_TEXTS):
        assert (LICENSES / name).is_file(), name


def test_gnu_license_texts_are_verbatim() -> None:
    for name, digest in GNU_TEXTS.items():
        assert (
            hashlib.md5((LICENSES / name).read_bytes(), usedforsecurity=False).hexdigest() == digest
        ), name


def test_notices_mention_bundled_versions() -> None:
    notices = (LICENSES / "THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert f"| Qt for Python(PySide6、shiboken6) | {PySide6.__version__} |" in notices
    assert shiboken6.__version__ == PySide6.__version__
    assert f"| Python | {platform.python_version()} |" in notices


def test_qt_third_party_notices_reference_existing_texts() -> None:
    notices = (LICENSES / "QT_THIRD_PARTY_NOTICES.md").read_text(encoding="utf-8")
    assert f"Qt {PySide6.__version__} の DLL" in notices
    referenced = set(re.findall(r"`((?:qt/)?[\w.-]+\.txt)`", notices))
    assert "qt/MIT.txt" in referenced
    for name in referenced:
        assert (LICENSES / name).is_file(), name
    # 使っていない全文を置きっぱなしにしない
    for path in (LICENSES / "qt").glob("*.txt"):
        assert f"qt/{path.name}" in referenced, path.name


def test_build_spec_copies_licenses() -> None:
    spec = (ROOT / "build.spec").read_text(encoding="utf-8")
    assert '"LICENSE.txt"' in spec
    assert '"licenses"' in spec
