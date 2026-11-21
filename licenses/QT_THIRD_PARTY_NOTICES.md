# Qt に含まれる第三者のコンポーネント

配布物に入っている Qt 6.12.0 の DLL(`Qt6Core.dll`、`Qt6Gui.dll`、`Qt6Widgets.dll`)とプラグイン(`qwindows.dll`、`qmodernwindowsstyle.dll`、`qico.dll`)には、Qt の開発元以外が作ったコンポーネントが組み込まれています。その著作権の表示とライセンスを、ここにまとめます。

- 一覧は Qt 6.12.0 のソース(qtbase の `qt_attribution.json`)と、Qt の文書「Licenses Used in Qt」(<https://doc.qt.io/qt-6/licenses-used-in-qt.html>)をもとに作りました。著作権の表示は `qt_attribution.json` の記載をそのまま写しています。
- バイナリで配布するときに全文を添えるよう求めているライセンスは、その全文を `qt/` フォルダ(Apache License 2.0 だけはこのフォルダの `Apache-2.0.txt`)に置いています。全文は qtbase の `LICENSES/` フォルダのものです。MIT や BSD などのひな形にある`<year> <copyright holders>` などの欄は、下の著作権の表示に読み替えてください。
- パブリックドメインや CC0 のもの、バイナリの配布に条件のないもの(zlib、libpng など)は、全文を添えずに著作権の表示だけを載せています。
- 「A AND B」は両方が適用されるライセンスです。どちらかを選べるもの(「A OR B」)は、選んだほうを書いています。
- Windows 以外でだけ使うもの(macOS、Linux の XCB と Wayland、Android、WebAssembly など)と、配布物に入れていないモジュールやプラグインのもの(Qt Network、Qt SQL、Qt D-Bus、JPEG の画像プラグインなど)は載せていません。

FreeType は FreeType License(FTL)を選んでいます。FTL が求める表示は次のとおりです。

```text
This software is based in part on the work of the FreeType Team.
Portions of this software are copyright © 2026 The FreeType Project (https://freetype.org). All rights reserved.
```

## 一覧

| コンポーネント | 版 | Qt での用途 | ライセンス | 全文 |
| --- | --- | --- | --- | --- |
| tl::expected | 41d3e1f48d68 | Qt 内部で使う型(tl::expected) | CC0-1.0 | なし(パブリックドメインと同等) |
| Unicode Character Database (UCD) | 36 | 文字と文字列の処理(QChar、QString) | Unicode-3.0 | `qt/Unicode-3.0.txt` |
| Unicode Common Locale Data Repository (CLDR) | 48.2 | 地域の書式とタイムゾーン(QLocale、QTimeZone) | Unicode-3.0 | `qt/Unicode-3.0.txt` |
| Apache Tika MimeType Definitions | c29724782854 | MIME タイプの判定(QMimeDatabase) | Apache-2.0 | `Apache-2.0.txt` |
| Efficient Binary-Decimal and Decimal-Binary Conversion Routines for IEEE Doubles | 3.4.0 | 浮動小数点数と文字列の変換 | BSD-3-Clause | `qt/BSD-3-Clause.txt` |
| Easing Equations by Robert Penner | — | イージング曲線(QEasingCurve) | BSD-3-Clause | `qt/BSD-3-Clause.txt` |
| Data Compression Library (zlib) | 1.3.2 | データの圧縮(qCompress など) | Zlib | なし(バイナリの配布に条件なし) |
| PCRE2 | 10.48 | 正規表現(QRegularExpression) | BSD-3-Clause(PCRE2 の例外つき) | `qt/BSD-3-Clause.txt` |
| PCRE2 - Stack-less Just-In-Time Compiler | 10.48 | 正規表現の JIT コンパイル(QRegularExpression) | BSD-2-Clause | `qt/BSD-2-Clause.txt` |
| SipHash Algorithm | — | ハッシュ表(QHash) | CC0-1.0 | なし(パブリックドメインと同等) |
| TinyCBOR | 7.0 | CBOR の読み書き(QCborStreamReader、QCborStreamWriter) | MIT | `qt/MIT.txt` |
| BLAKE2 (reference implementation) | ed1974ea8343 | ハッシュ値の計算(QCryptographicHash) | CC0-1.0(CC0-1.0 と Apache-2.0 から選択) | なし(パブリックドメインと同等) |
| MD4 | — | ハッシュ値の計算(QCryptographicHash) | CC0-1.0 | なし(パブリックドメインと同等) |
| MD5 | — | ハッシュ値の計算(QCryptographicHash) | CC0-1.0 | なし(パブリックドメインと同等) |
| Secure Hash Algorithm SHA-1 | — | ハッシュ値の計算(QCryptographicHash) | LicenseRef-SHA1-Public-Domain | なし(パブリックドメイン) |
| Secure Hash Algorithms SHA-384 and SHA-512 | — | ハッシュ値の計算(QCryptographicHash) | BSD-3-Clause | `qt/BSD-3-Clause.txt` |
| Secure Hash Algorithm SHA-3 - Keccak | 3.2 | ハッシュ値の計算(QCryptographicHash) | CC0-1.0 | なし(パブリックドメインと同等) |
| Secure Hash Algorithm SHA-3 - brg_endian | 1.0.0 | ハッシュ値の計算(QCryptographicHash) | BSD-2-Clause | `qt/BSD-2-Clause.txt` |
| Freetype 2 | 2.14.3 | フォントの読み込みと描画(Qt GUI) | FTL(FTL と GPL-2.0-only から選択) | なし(上記のクレジットの表示だけ) |
| Freetype 2 - zlib | — | FreeType に含まれる zlib(Qt GUI) | Zlib | なし(バイナリの配布に条件なし) |
| Freetype 2 - Bitmap Distribution Format (BDF) support | — | FreeType の BDF フォントの読み込み(Qt GUI) | MIT | `qt/MIT.txt` |
| Freetype 2 - Portable Compiled Format (PCF) support | — | FreeType の PCF フォントの読み込み(Qt GUI) | MIT AND MIT-open-group | `qt/MIT.txt`、`qt/MIT-open-group.txt` |
| Anti-aliasing rasterizer from FreeType 2 | — | アンチエイリアスの描画(Qt GUI) | FTL(FTL と GPL-2.0-only から選択) | なし(上記のクレジットの表示だけ) |
| HarfBuzz-NG | 14.5.0 | 文字の組版(Qt GUI) | MIT | `qt/MIT.txt` |
| Emoji Segmenter | 0.4.0 | 絵文字の区切りの判定(Qt GUI) | Apache-2.0 | `Apache-2.0.txt` |
| LibPNG | 1.6.58 | PNG 画像の読み書き(Qt GUI) | Libpng AND libpng-2.0 | なし(バイナリの配布に条件なし) |
| sRGB color profile icc file | — | sRGB のカラープロファイル(Qt GUI) | LicenseRef-ICC-License | なし(改変しなければ制限なし) |
| MD4C | 0.5.3 | Markdown の読み込み(QTextDocument) | MIT | `qt/MIT.txt` |
| Adobe Glyph List For New Fonts | 1.7 | PDF の出力(Qt GUI) | BSD-3-Clause | `qt/BSD-3-Clause.txt` |
| Smooth Scaling Algorithm | — | 画像のなめらかな縮小(QImage) | BSD-2-Clause AND Imlib2 | `qt/BSD-2-Clause.txt`、`qt/Imlib2.txt` |
| X Server helper | — | 領域の計算(QRegion) | X11 AND HPND | `qt/X11.txt`、`qt/HPND.txt` |
| XSVG | — | 円弧の計算(Qt GUI) | HPND-sell-variant | `qt/HPND-sell-variant.txt` |
| WebGradients | — | グラデーションの既定値(QGradient) | MIT | `qt/MIT.txt` |
| OpenGL Headers | Revision 27684 | OpenGL のヘッダー(Qt GUI) | MIT | `qt/MIT.txt` |
| OpenGL ES 2 Headers | Revision 27673 | OpenGL ES 2 のヘッダー(Qt GUI) | MIT | `qt/MIT.txt` |
| Vulkan API Registry | 1.4.308 | Vulkan の関数の定義(QVulkanFunctions) | MIT(Apache-2.0 と MIT から選択) | `qt/MIT.txt` |
| Vulkan Memory Allocator | 3.3.0 | Vulkan のメモリ管理(Qt GUI の描画) | MIT | `qt/MIT.txt` |
| D3D12 Memory Allocator | 3.1.0 | Direct3D 12 のメモリ管理(Qt GUI の描画) | MIT | `qt/MIT.txt` |
| Mipmap generator for D3D12 | 0aa79bad7899 | Direct3D 12 のミップマップ生成(Qt GUI の描画) | MIT | `qt/MIT.txt` |
| Wintab API | — | ペンタブレットの入力(Windows 用プラグイン qwindows) | LicenseRef-Lcs-Telegraphics | なし(制限なし) |

## 著作権の表示

### tl::expected

```text
To the extent possible under law, Sy Brand has waived all copyright and related or neighboring rights to the expected library. This work is published from: United Kingdom.
```

### Unicode Character Database (UCD)

```text
Copyright (C) 1991-2025 Unicode, Inc.
```

### Unicode Common Locale Data Repository (CLDR)

```text
Copyright (C) 2004-2025 Unicode, Inc.
```

### Apache Tika MimeType Definitions

```text
Copyright 2026 The Apache Software Foundation
```

### Efficient Binary-Decimal and Decimal-Binary Conversion Routines for IEEE Doubles

```text
Copyright 2006-2012, the V8 project authors
```

### Easing Equations by Robert Penner

```text
Copyright (c) 2001 Robert Penner
```

### Data Compression Library (zlib)

```text
(C) 1995-2026 Jean-loup Gailly and Mark Adler
```

### PCRE2

```text
Copyright (c) 1997-2007 University of Cambridge
Copyright (c) 2007-2024 Philip Hazel
Copyright (c) 2010-2024 Zoltan Herczeg
```

### PCRE2 - Stack-less Just-In-Time Compiler

```text
Copyright Zoltan Herczeg
```

### SipHash Algorithm

```text
Copyright (C) 2012-2014 Jean-Philippe Aumasson
Copyright (C) 2012-2014 Daniel J. Bernstein <djb@cr.yp.to>
Copyright (C) 2016 Intel Corporation
```

### TinyCBOR

```text
Copyright (C) 2015-2025 Intel Corporation
```

### BLAKE2 (reference implementation)

```text
Copyright 2012, Samuel Neves <sneves@dei.uc.pt>
```

### MD4

```text
Written by Alexander Peslyak - better known as Solar Designer <solar@openwall.com> - in 2001, and placed in the public domain. There's absolutely no warranty.
```

### MD5

```text
Written by Colin Plumb in 1993, no copyright is claimed. Ian Jackson <ian@chiark.greenend.org.uk>.
```

### Secure Hash Algorithm SHA-1

```text
Copyright (C) Dominik Reichl <dominik.reichl@t-online.de>
Copyright (C) 2016 The Qt Company Ltd
```

### Secure Hash Algorithms SHA-384 and SHA-512

```text
Copyright (c) 2011 IETF Trust and the persons identified as authors of the code.
```

### Secure Hash Algorithm SHA-3 - Keccak

```text
Guido Bertoni, Joan Daemen, Michaël Peeters and Gilles Van Assche.
To the extent possible under law, the implementers have waived all copyright
and related or neighboring rights to the source code in this file.
```

### Secure Hash Algorithm SHA-3 - brg_endian

```text
Copyright (c) 1998-2013, Brian Gladman, Worcester, UK. All rights reserved.
```

### Freetype 2

```text
Copyright (c) 2007-2014 Adobe Systems Incorporated
Copyright (c) 2004-2026 Albert Chin-A-Young
Copyright (c) 2018-2026 Armin Hasitzka, David Turner, Robert Wilhelm, and Werner Lemberg
Copyright (c) 2000 Computing Research Labs, New Mexico State University
Copyright (c) 1996-2026 David Turner, Robert Wilhelm, Dominik Röttsches, and Werner Lemberg
Copyright (c) 2004-2026 David Turner, Robert Wilhelm, Werner Lemberg and George Williams
Copyright (c) 2022-2026 David Turner, Robert Wilhelm, Werner Lemberg, and Moazin Khatti
Copyright (c) 2008-2026 David Turner, Robert Wilhelm, Werner Lemberg, and suzuki toshiya
Copyright (c) 2003-2026 David Turner, Robert Wilhelm, and Werner Lemberg
Copyright (c) 2005-2026 David Turner
Copyright (c) 2007-2026 Derek Clegg and Michael Toftdal
Copyright (c) 2007 Dmitry Timoshkov for Codeweavers
Copyright (c) 2001-2015 Francesco Zappa Nardelli
Copyright (c) 2005, 2007, 2008, 2013 George Williams
Copyright (c) 2013-2026 Google, Inc. Google Author(s) Behdad Esfahbod and Stuart Gill
Copyright (c) 2013-2022 Google, Inc.
Copyright (c) 2003 Huw D M Davies for Codeweavers
Copyright (c) 2010-2026 Joel Klinghed
Copyright (c) 1996-2026 Just van Rossum, David Turner, Robert Wilhelm, and Werner Lemberg
Copyright (c) 2003-2026 Masatake YAMATO and Redhat K.K.
Copyright (c) 2004-2026 Masatake YAMATO, Redhat K.K, David Turner, Robert Wilhelm, and Werner Lemberg
Copyright (c) 2019-2026 Nikhil Ramakrishnan, David Turner, Robert Wilhelm, and Werner Lemberg
Copyright (c) 2009-2026 Oran Agra and Mickey Gabel
Copyright (c) 2007-2026 Rahul Bhalerao <rahul.bhalerao@redhat.com>
Copyright (c) 2002-2026 Roberto Alameda
Copyright (c) 2015-2026 Werner Lemberg
Copyright (c) 2004-2026 suzuki toshiya, Masatake YAMATO, Red Hat K.K., David Turner, Robert Wilhelm, and Werner Lemberg
Copyright (c) 2019 nyorain
Copyright (c) 2022-2026 David Turner, Robert Wilhelm, Werner Lemberg, George Williams, and Dominik Röttsches
Copyright (C) 2009, 2023  Red Hat, Inc.
```

### Freetype 2 - zlib

```text
Copyright (C) 1995-2024 Jean-loup Gailly and Mark Adler
```

### Freetype 2 - Bitmap Distribution Format (BDF) support

```text
Copyright (c) 2000 Computing Research Labs, New Mexico State University
Copyright (c) 2001-2014 Francesco Zappa Nardelli
```

### Freetype 2 - Portable Compiled Format (PCF) support

```text
Copyright (c) 2001, 2012 David Turner, Robert Wilhelm, and Werner Lemberg
Copyright (c) 2000-2014 Francesco Zappa Nardelli
Copyright (c) 1990, 1994, 1998 The Open Group
```

### Anti-aliasing rasterizer from FreeType 2

```text
Copyright 2000-2016 by David Turner, Robert Wilhelm, and Werner Lemberg.
```

### HarfBuzz-NG

```text
Copyright © 2010-2022  Google, Inc.
Copyright © 2015-2020  Ebrahim Byagowi
Copyright © 2019,2020  Facebook, Inc.
Copyright © 2012,2015  Mozilla Foundation
Copyright © 2011  Codethink Limited
Copyright © 2008,2010  Nokia Corporation and/or its subsidiary(-ies)
Copyright © 2009  Keith Stribley
Copyright © 2011  Martin Hosken and SIL International
Copyright © 2007  Chris Wilson
Copyright © 2005,2006,2020,2021,2022,2023  Behdad Esfahbod
Copyright © 2004,2007,2008,2009,2010,2013,2021,2022,2023  Red Hat, Inc.
Copyright © 1998-2005  David Turner and Werner Lemberg
Copyright © 2016  Igalia S.L.
Copyright © 2022  Matthias Clasen
Copyright © 2018,2021  Khaled Hosny
Copyright © 2018,2019,2020  Adobe, Inc
Copyright © 2013-2015  Alexei Podtelezhnikov
```

### Emoji Segmenter

```text
Copyright 2019 Google LLC
```

### LibPNG

```text
Copyright (c) 1995-2026 The PNG Reference Library Authors
Copyright (c) 2000-2026 Cosmin Truta
Copyright (c) 1998-2018 Glenn Randers-Pehrson
Copyright (c) 1996-1997 Andreas Dilger
Copyright (c) 1995-1996 Guy Eric Schalnat, Group 42, Inc.
Copyright (c) 2000-2017 Simon-Pierre Cadieux
Copyright (c) 2000-2017 Eric S. Raymond
Copyright (c) 2000-2017 Mans Rullgard
Copyright (c) 2000-2017 Gilles Vollant
Copyright (c) 2000-2017 James Yu
Copyright (c) 2000-2017 Mandar Sahastrabuddhe
Copyright (c) 1998-2000 Tom Lane
Copyright (c) 1998-2000 Willem van Schaik
Copyright (c) 1996-1997 John Bowler
Copyright (c) 1996-1997 Kevin Bracey
Copyright (c) 1996-1997 Sam Bushell
Copyright (c) 1996-1997 Magnus Holmgren
Copyright (c) 1996-1997 Greg Roelofs
Copyright (c) 1996-1997 Tom Tanner
Copyright (c) 1995-1996 Dave Martindale
Copyright (c) 1995-1996 Paul Schmidt
Copyright (c) 1995-1996 Tim Wegner
```

### sRGB color profile icc file

```text
Copyright International Color Consortium, 2015
```

### MD4C

```text
Copyright © 2016-2024 Martin Mitáš
```

### Adobe Glyph List For New Fonts

```text
Copyright 2002, 2003, 2005, 2006, 2008, 2010, 2015 Adobe Systems
```

### Smooth Scaling Algorithm

```text
Copyright (C) 2004, 2005 Daniel M. Duley.
(C) Carsten Haitzler and various contributors.
(C) Willem Monsuwe <willem@stack.nl>
```

### X Server helper

```text
Copyright (c) 1987, 1988 X Consortium
Copyright 1987, 1988 by Digital Equipment Corporation, Maynard, Massachusetts.
```

### XSVG

```text
Copyright 2002 USC/Information Sciences Institute
```

### WebGradients

```text
Copyright (c) 2017 itmeo
```

### OpenGL Headers

```text
Copyright (c) 2013-2014 The Khronos Group Inc.
```

### OpenGL ES 2 Headers

```text
Copyright (c) 2013-2014 The Khronos Group Inc.
```

### Vulkan API Registry

```text
Copyright (c) 2015-2025 The Khronos Group Inc.
```

### Vulkan Memory Allocator

```text
Copyright (c) 2017-2025 Advanced Micro Devices, Inc. All rights reserved.
```

### D3D12 Memory Allocator

```text
Copyright (c) 2019-2026 Advanced Micro Devices, Inc. All rights reserved.
```

### Mipmap generator for D3D12

```text
Copyright (c) 2015 Microsoft
```

### Wintab API

```text
Copyright 1991-1998 by LCS/Telegraphics.
```
