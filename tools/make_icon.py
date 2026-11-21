"""アプリのアイコン(neko_gantcha.ico と neko_gantcha.png)を描いて書き出す開発用スクリプト。

実行: uv run python tools/make_icon.py
ICO は複数サイズの PNG をまとめた形式で書く(Qt の ICO 出力は1サイズしか書けないため)。
"""

import logging
import struct
import sys
from pathlib import Path

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QPointF, QRectF, Qt
from PySide6.QtGui import (
    QColor,
    QGuiApplication,
    QImage,
    QLinearGradient,
    QPainter,
    QPainterPath,
    QPen,
)

OUT = Path(__file__).resolve().parents[1] / "src" / "neko_gantcha" / "ui" / "resources"
SIZES = (16, 24, 32, 48, 64, 128, 256)

# ガントの帯の色。左上から順に
BAR_COLORS = ("#3B82F6", "#8B5CF6", "#EC4899")
ICO_MAX_SIDE = 256  # ICO の見出しに書ける大きさの上限。256 は 0 と書く決まり

logger = logging.getLogger(__name__)


def cat_head(s: float) -> QPainterPath:
    """ねこの頭の形(耳つき)を作る。

    Args:
        s: 拡大率。256 ピクセル角を基準にした座標を s 倍する。

    Returns:
        頭の輪郭。
    """
    head = QPainterPath()
    head.addRoundedRect(QRectF(36 * s, 80 * s, 184 * s, 144 * s), 72 * s, 64 * s)
    for sign in (-1, 1):  # 左右の耳。外側の辺は頭の輪郭へなめらかにつなぐ
        ear = QPainterPath()
        ear.moveTo(QPointF((128 + sign * 82) * s, 136 * s))
        ear.lineTo(QPointF((128 + sign * 64) * s, 36 * s))
        ear.lineTo(QPointF((128 + sign * 10) * s, 90 * s))
        ear.closeSubpath()
        head = head.united(ear)
    return head


def draw(size: int) -> QImage:
    """ねこの顔の中にガントチャートの帯を描いたアイコンを作る。

    角丸の四角に青から桃色のグラデーションを塗り、白いねこの顔を重ねる。

    Args:
        size: 一辺の大きさ(ピクセル)。

    Returns:
        アイコンの画像。
    """
    image = QImage(size, size, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    p = QPainter(image)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    s = size / 256  # 256 を基準に描く

    gradient = QLinearGradient(0, 0, size, size)
    gradient.setColorAt(0, QColor("#4F7CF7"))
    gradient.setColorAt(1, QColor("#E5489B"))
    p.setPen(Qt.PenStyle.NoPen)
    p.setBrush(gradient)
    p.drawRoundedRect(QRectF(8 * s, 8 * s, 240 * s, 240 * s), 56 * s, 56 * s)

    # ねこの頭(角を丸めるため、同じ色の太い線でなぞる)
    white = QColor("#FFFFFF")
    outline = QPen(white, 14 * s)
    outline.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(outline)
    p.setBrush(white)
    p.drawPath(cat_head(s))

    # 顔の中のガントの帯(小さいサイズでもつぶれないよう、太さに下限を設ける)
    height = max(2.0, 20 * s)
    bars = ((70, 132), (104, 166), (138, 190))
    for index, ((left, right), color) in enumerate(zip(bars, BAR_COLORS, strict=True)):
        y = (124 + index * 30) * s
        rect = QRectF(left * s, y, (right - left) * s, height)
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(rect, height / 2, height / 2)
    p.end()
    return image


def png_bytes(image: QImage) -> bytes:
    """画像を PNG のバイト列にする。

    Args:
        image: 画像。

    Returns:
        PNG のバイト列。
    """
    data = QByteArray()
    buffer = QBuffer(data)
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    # PySide6 の型定義では形式は bytes だが、実際は str でないと受け付けない
    image.save(buffer, "PNG")  # ty: ignore[no-matching-overload]
    buffer.close()
    return bytes(data.data())


def write_ico(path: Path, images: list[QImage]) -> None:
    """複数サイズの画像を、PNG を詰めた ICO ファイルに書く。

    Args:
        path: 書き出すファイルのパス。
        images: 各サイズの画像。
    """
    blobs = [png_bytes(image) for image in images]
    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries = b""
    for image, blob in zip(images, blobs, strict=True):
        side = image.width() if image.width() < ICO_MAX_SIDE else 0
        entries += struct.pack("<BBBBHHII", side, side, 0, 0, 1, 32, len(blob), offset)
        offset += len(blob)
    path.write_bytes(header + entries + b"".join(blobs))


def main() -> int:
    """アイコンを描いて書き出す。--preview なら確認用の画像だけを作る。

    Returns:
        終了コード。
    """
    QGuiApplication(sys.argv)
    images = [draw(size) for size in SIZES]
    if "--preview" in sys.argv:
        # 確認用に、各サイズを拡大して1枚に並べる。書き出しはしない
        out = Path(sys.argv[sys.argv.index("--preview") + 1])
        sheet = QImage(256 * len(images), 256, QImage.Format.Format_ARGB32)
        sheet.fill(QColor("#202020"))
        p = QPainter(sheet)
        for i, image in enumerate(images):
            p.drawImage(QRectF(i * 256, 0, 256, 256), image)
        p.end()
        sheet.save(str(out))
        return 0
    write_ico(OUT / "neko_gantcha.ico", images)
    images[-1].save(str(OUT / "neko_gantcha.png"))
    logger.info("書き出しました: %s", OUT / "neko_gantcha.ico")
    return 0


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    sys.exit(main())
