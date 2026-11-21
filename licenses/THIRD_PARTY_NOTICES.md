# 第三者ソフトウェアのライセンス(Third-Party Notices)

ねこがんちゃ(NekoGantcha)本体は MIT ライセンスです(配布物では `LICENSE.txt`、リポジトリでは `LICENSE`)。

配布物(`NekoGantcha` フォルダ)には、次の第三者ソフトウェアが含まれます。ライセンスの全文はこのフォルダにあります。

| ソフトウェア | バージョン | ライセンス | 全文 |
| --- | --- | --- | --- |
| Qt | 6.12.0 | GNU LGPL v3 | `LGPL-3.0.txt`、`GPL-3.0.txt` |
| Qt に組み込まれた第三者のコンポーネント | — | MIT、BSD、Zlib、FTL ほか | `QT_THIRD_PARTY_NOTICES.md`、`qt` フォルダ |
| Qt for Python(PySide6、shiboken6) | 6.12.0 | GNU LGPL v3 | `LGPL-3.0.txt`、`GPL-3.0.txt` |
| Python | 3.14.10 | PSF License v2 ほか | `Python-LICENSE.txt` |
| OpenSSL | 3.5.9 | Apache License 2.0 | `Apache-2.0.txt` |
| mpdecimal | 4.0.0 | BSD 2-Clause | このファイルの下部 |
| zlib-ng | 1.3.1 | zlib License | このファイルの下部 |
| bzip2、Zstandard | — | BSD 系 | `Python-LICENSE.txt` |
| SQLite | 3.53.1 | パブリックドメイン | — |
| XZ Utils(liblzma) | — | 0BSD / パブリックドメイン | — |
| Microsoft Visual C++ ランタイム、UCRT | — | Microsoft の再頒布可能コード | `Python-LICENSE.txt` の「Additional Conditions for this Windows binary build」 |
| PyInstaller ブートローダー | 6.22.3 | GPL v2 以降(ブートローダー例外つき) | 下記 |

---

## Qt と Qt for Python(LGPL v3)

このアプリは Qt と Qt for Python(PySide6、shiboken6)を GNU Lesser General Public License version 3(LGPL v3)のもとで使っています。LGPL v3 は GNU General Public License version 3 の条項に追加の許可を加えたものなので、両方の全文を添えています。

- 著作権: Copyright (C) The Qt Company Ltd. and other contributors.
- 含まれるファイル(`_internal` フォルダ内):
  - `PySide6\Qt6Core.dll`、`Qt6Gui.dll`、`Qt6Widgets.dll`、`pyside6.abi3.dll`、`QtCore.pyd`、`QtGui.pyd`、`QtWidgets.pyd`
  - `PySide6\plugins\platforms\qwindows.dll`、`plugins\styles\qmodernwindowsstyle.dll`、`plugins\imageformats\qico.dll`
  - `PySide6\translations\qtbase_ja.qm`、`qt_ja.qm`
  - `shiboken6\shiboken6.abi3.dll`、`Shiboken.pyd`
- 改変: していません。PyPI で配布されている PySide6-Essentials 6.12.0 と shiboken6 6.12.0 をそのまま使っています。
- 差し替え: これらは別ファイルの DLL / pyd として置いてあるので、互換性のあるバージョンや、自分でビルドしたものに差し替えて使えます。
- ソースコードの入手先:
  - Qt: <https://download.qt.io/official_releases/qt/>(過去のバージョンは <https://download.qt.io/archive/qt/>)、<https://code.qt.io/cgit/qt/>
  - Qt for Python: <https://download.qt.io/official_releases/QtForPython/>、<https://code.qt.io/cgit/pyside/pyside-setup.git/>
- このアプリのソースコード: GitHub のリポジトリで MIT ライセンスで公開しています。
- Qt の DLL に組み込まれている第三者のコンポーネント(PCRE2、HarfBuzz、FreeType、libpng、zlib、Unicode のデータなど)の著作権の表示とライセンスは、`QT_THIRD_PARTY_NOTICES.md` にまとめています。ライセンスの全文は `qt` フォルダにあります。

## Python

Python 本体(`python314.dll`、`python3.dll`、`base_library.zip`、各 `.pyd`)は Python Software Foundation License Version 2 などのもとで配布されています。全文と、Windows 版に含まれる Microsoft の再頒布可能コード・bzip2・Zstandard の条項は `Python-LICENSE.txt` にあります。

Copyright (c) 2001 Python Software Foundation. All rights reserved.

## OpenSSL

`libcrypto-3-x64.dll` は OpenSSL です。Apache License 2.0 のもとで配布されています(`Apache-2.0.txt`)。

Copyright (c) The OpenSSL Project Authors.
<https://www.openssl.org/>

## mpdecimal(`_decimal.pyd` に組み込み)

```text
Copyright (c) 2008-2024 Stefan Krah. All rights reserved.

Redistribution and use in source and binary forms, with or without
modification, are permitted provided that the following conditions
are met:

1. Redistributions of source code must retain the above copyright
   notice, this list of conditions and the following disclaimer.

2. Redistributions in binary form must reproduce the above copyright
   notice, this list of conditions and the following disclaimer in the
   documentation and/or other materials provided with the distribution.

THIS SOFTWARE IS PROVIDED BY THE AUTHOR AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE
IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE
ARE DISCLAIMED.  IN NO EVENT SHALL THE AUTHOR OR CONTRIBUTORS BE LIABLE
FOR ANY DIRECT, INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL
DAMAGES (INCLUDING, BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS
OR SERVICES; LOSS OF USE, DATA, OR PROFITS; OR BUSINESS INTERRUPTION)
HOWEVER CAUSED AND ON ANY THEORY OF LIABILITY, WHETHER IN CONTRACT, STRICT
LIABILITY, OR TORT (INCLUDING NEGLIGENCE OR OTHERWISE) ARISING IN ANY WAY
OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED OF THE POSSIBILITY OF
SUCH DAMAGE.
```

## zlib-ng(`python314.dll` に組み込み)

```text
(C) 1995-2024 Jean-loup Gailly and Mark Adler
(C) zlib-ng contributors

This software is provided 'as-is', without any express or implied
warranty.  In no event will the authors be held liable for any damages
arising from the use of this software.

Permission is granted to anyone to use this software for any purpose,
including commercial applications, and to alter it and redistribute it
freely, subject to the following restrictions:

1. The origin of this software must not be misrepresented; you must not
   claim that you wrote the original software. If you use this software
   in a product, an acknowledgment in the product documentation would be
   appreciated but is not required.
2. Altered source versions must be plainly marked as such, and must not be
   misrepresented as being the original software.
3. This notice may not be removed or altered from any source distribution.
```

## SQLite、XZ Utils

`sqlite3.dll`(SQLite)はパブリックドメインです。`_lzma.pyd` に組み込まれた liblzma(XZ Utils)は 0BSD またはパブリックドメインです。どちらも表示の義務はありませんが、ここに記載しておきます。

## Microsoft Visual C++ ランタイム、UCRT

`VCRUNTIME140.dll`、`VCRUNTIME140_1.dll`、`MSVCP140*.dll`、`ucrtbase.dll`、`api-ms-win-*.dll` は Microsoft の再頒布可能コードです。条件は `Python-LICENSE.txt` の「Additional Conditions for this Windows binary build」を参照してください。

## PyInstaller ブートローダー

`NekoGantcha.exe` の起動部分は PyInstaller 6.22.3 のブートローダーです。PyInstaller は GPL v2 以降のライセンスですが、ブートローダーには例外があり、ほかのプログラムと組み合わせて制限なく配布できます。
<https://github.com/pyinstaller/pyinstaller>
