"""配色(ライト・ダーク)の切り替え。

Qt の `QStyleHints.setColorScheme` でアプリ全体の配色を決める。`Unknown` を渡すと
Windows の設定に従う状態に戻る。色はすべてパレットから作っているので、
配色を変えればパレットに追従して描き直される。
"""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from neko_gantcha.domain.models import Theme

THEME_LABELS = {
    Theme.SYSTEM: "Windows に合わせる",
    Theme.LIGHT: "ライト",
    Theme.DARK: "ダーク",
}

_SCHEMES = {
    Theme.SYSTEM: Qt.ColorScheme.Unknown,
    Theme.LIGHT: Qt.ColorScheme.Light,
    Theme.DARK: Qt.ColorScheme.Dark,
}


def apply_theme(theme: Theme | str) -> None:
    """アプリ全体の配色を切り替える。"""
    QApplication.styleHints().setColorScheme(_SCHEMES[Theme(theme)])


def reload_stylesheet(app: QApplication) -> None:
    """スタイルシートの palette(...) を新しいパレットで解決し直す。"""
    sheet = app.styleSheet()
    app.setStyleSheet("")
    app.setStyleSheet(sheet)
