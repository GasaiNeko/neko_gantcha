"""複数の画面で使う小さな部品。"""

from datetime import datetime
from typing import TYPE_CHECKING, override

from PySide6.QtCore import QDate, QDateTime, QTime, Signal
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QPlainTextEdit

if TYPE_CHECKING:
    from PySide6.QtGui import QFocusEvent


def color_icon(color: str, size: int = 12) -> QIcon:
    """分類の色を示す丸いアイコンを作る。

    Args:
        color: 色(`#3B82F6` の形)。
        size: アイコンの一辺の大きさ(ピクセル)。

    Returns:
        丸いアイコン。
    """
    pixmap = QPixmap(size, size)
    pixmap.fill(QColor(0, 0, 0, 0))
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setBrush(QColor(color))
    painter.setPen(QColor(0, 0, 0, 0))
    painter.drawEllipse(1, 1, size - 2, size - 2)
    painter.end()
    return QIcon(pixmap)


def mix_colors(base: QColor, other: QColor, ratio: float) -> QColor:
    """色 base に色 other を ratio だけ混ぜる。パレットから罫線や見出しの色を作るのに使う。

    Args:
        base: もとの色。
        other: 混ぜる色。
        ratio: 混ぜる割合(0〜1)。

    Returns:
        混ぜた色。
    """
    return QColor(
        round(base.red() + (other.red() - base.red()) * ratio),
        round(base.green() + (other.green() - base.green()) * ratio),
        round(base.blue() + (other.blue() - base.blue()) * ratio),
    )


def to_qdatetime(value: datetime) -> QDateTime:
    """日時を QDateTime にする。

    Args:
        value: 変換する日時。

    Returns:
        同じ日時の QDateTime。
    """
    return QDateTime(
        QDate(value.year, value.month, value.day), QTime(value.hour, value.minute, value.second)
    )


def from_qdatetime(value: QDateTime) -> datetime:
    """QDateTime を秒までの datetime にする。

    Args:
        value: 変換する日時。

    Returns:
        同じ日時の datetime。
    """
    day, moment = value.date(), value.time()
    return datetime(
        day.year(), day.month(), day.day(), moment.hour(), moment.minute(), moment.second()
    )


def format_datetime(value: datetime | None) -> str:
    """日時を表示用の文字列にする。

    Args:
        value: 日時。

    Returns:
        `2026/11/16 12:00` の形の文字列。None なら空文字。
    """
    return value.strftime("%Y/%m/%d %H:%M") if value is not None else ""


class NoteEdit(QPlainTextEdit):
    """フォーカスが外れたときに editing_finished を出す複数行入力。"""

    editing_finished = Signal()

    @override
    def focusOutEvent(self, event: QFocusEvent) -> None:
        super().focusOutEvent(event)
        self.editing_finished.emit()
