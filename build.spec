"""PyInstaller の設定。ビルド: uv run pyinstaller build.spec --noconfirm

配布物は dist フォルダの NekoGantcha(onedir)。必ず Windows 上でビルドする。
"""

import shutil
from pathlib import Path

from PyInstaller.building.build_main import COLLECT, EXE, PYZ, Analysis

# SPECPATH と DISTPATH は、PyInstaller が spec を実行するときに定義する
ROOT = Path(SPECPATH)  # noqa: F821  # ty: ignore[unresolved-reference]
PKG = ROOT / "src" / "neko_gantcha"

# 同梱リソース。展開先は _internal\neko_gantcha\... で、paths.resource_path() の基準と一致させる。
datas = [
    (str(PKG / "data" / "migrations"), "neko_gantcha/data/migrations"),
    (str(PKG / "ui" / "resources"), "neko_gantcha/ui/resources"),
]

# 使うのは QtCore / QtGui / QtWidgets のみ。それ以外は除外してサイズを抑える。
excludes = [
    "PySide6.Qt3DAnimation",
    "PySide6.Qt3DCore",
    "PySide6.Qt3DExtras",
    "PySide6.Qt3DInput",
    "PySide6.Qt3DLogic",
    "PySide6.Qt3DRender",
    "PySide6.QtBluetooth",
    "PySide6.QtCharts",
    "PySide6.QtDataVisualization",
    "PySide6.QtDesigner",
    "PySide6.QtHelp",
    "PySide6.QtMultimedia",
    "PySide6.QtMultimediaWidgets",
    "PySide6.QtNetwork",
    "PySide6.QtOpenGL",
    "PySide6.QtOpenGLWidgets",
    "PySide6.QtPdf",
    "PySide6.QtPdfWidgets",
    "PySide6.QtPositioning",
    "PySide6.QtQml",
    "PySide6.QtQuick",
    "PySide6.QtQuick3D",
    "PySide6.QtQuickControls2",
    "PySide6.QtQuickWidgets",
    "PySide6.QtSql",
    "PySide6.QtSvg",
    "PySide6.QtSvgWidgets",
    "PySide6.QtTest",
    "PySide6.QtWebChannel",
    "PySide6.QtWebEngineCore",
    "PySide6.QtWebEngineQuick",
    "PySide6.QtWebEngineWidgets",
    "PySide6.QtWebSockets",
    "PySide6.QtXml",
    "ssl",  # 通信はしない
    "tkinter",
]

a = Analysis(
    [str(PKG / "__main__.py")],
    pathex=[str(ROOT / "src")],
    binaries=[],
    datas=datas,
    hiddenimports=[],
    excludes=excludes,
    noarchive=False,
)

# Qt のプラグインと DLL のうち、このアプリが使わないものを同梱から外す(サイズ削減)。
# QtCore / QtGui / QtWidgets と、Windows 用のプラットフォーム・スタイル・ICO 読み込みだけを残す。
# 仮想キーボードや SVG のプラグインは Qt Quick / QML / SVG の DLL を道連れにするので一緒に外す。
_DROP_FILES = {
    "opengl32sw.dll",  # OpenGL のソフトウェア実装(Widgets だけなら使わない)
    "qt6quick.dll",
    "qt6qml.dll",
    "qt6qmlmodels.dll",
    "qt6qmlmeta.dll",
    "qt6qmlworkerscript.dll",
    "qt6pdf.dll",
    "qt6opengl.dll",
    "qt6network.dll",
    "qt6svg.dll",
    "qt6virtualkeyboard.dll",
    "qt6virtualkeyboardqml.dll",
    "qdirect2d.dll",
    "qminimal.dll",
    "qoffscreen.dll",
    "libssl-3-x64.dll",
    "_ssl.pyd",
}
_DROP_DIRS = (
    "pyside6/plugins/generic/",
    "pyside6/plugins/iconengines/",
    "pyside6/plugins/platforminputcontexts/",
)
_KEEP_IMAGEFORMATS = {"qico.dll"}  # ウィンドウのアイコン(.ico)の読み込みに使う
_KEEP_TRANSLATIONS = {"qtbase_ja.qm", "qt_ja.qm"}  # 標準ダイアログの日本語化


def _keep(entry: tuple[str, str, str]) -> bool:
    """同梱するファイルか。

    Returns:
        外すファイルなら False。
    """
    dest = entry[0].replace("\\", "/").lower()
    name = dest.rsplit("/", 1)[-1]
    if name in _DROP_FILES or dest.startswith(_DROP_DIRS):
        return False
    if dest.startswith("pyside6/plugins/imageformats/"):
        return name in _KEEP_IMAGEFORMATS
    if dest.startswith("pyside6/translations/"):
        return name in _KEEP_TRANSLATIONS
    return True


a.binaries = [entry for entry in a.binaries if _keep(entry)]
a.datas = [entry for entry in a.datas if _keep(entry)]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name="NekoGantcha",
    console=False,
    upx=False,
    icon=str(PKG / "ui" / "resources" / "neko_gantcha.ico"),
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    upx=False,
    name="NekoGantcha",
)

# ライセンス文書を配布物の直下に置く(LGPL などで、利用者に見える形で添える必要があるため)。
# 本体の MIT は LICENSE.txt、第三者のものは licenses\ にまとめる。
_dist = Path(DISTPATH) / "NekoGantcha"  # noqa: F821  # ty: ignore[unresolved-reference]
shutil.copyfile(ROOT / "LICENSE", _dist / "LICENSE.txt")
shutil.copytree(ROOT / "licenses", _dist / "licenses", dirs_exist_ok=True)
