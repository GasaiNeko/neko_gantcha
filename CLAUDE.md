# CLAUDE.md

ねこがんちゃ(NekoGantcha)を Claude Code で開発するときの指示書です。作業の前に読み、ここに書いたルールに従ってください。

## プロジェクトの概要

- ソシャゲのイベントと日課を、ToDo・カレンダー・ガントチャートの3画面で管理する Windows デスクトップアプリ。
- Python 3.14、PySide6-Essentials(QtCore / QtGui / QtWidgets だけ)、標準の sqlite3。配布は PyInstaller の onedir。
- ライセンスは MIT。Qt と PySide6 は LGPL v3 で使っている(`licenses/`)。

## 守ること

- 返答、コメント、docstring、画面の文言は日本語で書く。識別子は英語。
- 実在のゲームの名前を、コード、テストのデータ、文書、スクリーンショットのどこにも書かない。例には「ソシャゲA」「ソシャゲB」を使う。
- メールアドレスなどの個人情報をリポジトリに書かない。
- コミット、マージ、タグ付け、push は、ユーザーに頼まれたときだけ行う。force push と、`main` / `develop` の履歴の書き換えはしない。
- ファイルを消すときは、ごみ箱に移すなど元に戻せる方法を使う。

## ブランチの運用(Git Flow)

Git Flow で運用する。

| ブランチ | 作る元 | マージ先 | 用途 |
| --- | --- | --- | --- |
| `main` | — | — | リリースしたものだけを置く。リリースごとに `vX.Y.Z` のタグを付ける |
| `develop` | `main` | — | 次のリリースに向けて変更をまとめる |
| `feature/<内容>` | `develop` | `develop` | 機能の追加や修正。名前は英小文字とハイフン(例: `feature/weekly-reset`) |
| `release/X.Y.Z` | `develop` | `main` と `develop` | リリースの準備。版の更新と、見つかった不具合の修正だけを行う |
| `hotfix/X.Y.Z` | `main` | `main` と `develop` | リリースした版の緊急の修正 |

- `main` と `develop` には直接コミットしない。変更を始める前に今のブランチを確かめ、`main` か `develop` にいるときは、用途に合ったブランチを作ってから作業する。ブランチを作ったら、そのことをユーザーに伝える。
- マージは `git merge --no-ff` で行い、ブランチの流れを履歴に残す。マージが終わったブランチを消すかどうかはユーザーに確かめる。
- 版は Semantic Versioning(`MAJOR.MINOR.PATCH`)で付ける。不具合の修正だけなら PATCH、機能の追加やマイグレーションの追加は MINOR、データの互換性がなくなる変更は MAJOR を上げる。
- コミットメッセージは日本語で書く。1行目に変更の要約を書き、必要なら1行空けて理由や詳細を書く。

### リリースの手順

1. `develop` から `release/X.Y.Z` を作る。
2. 版を `pyproject.toml` の `version` と `src/neko_gantcha/__init__.py` の `__version__` の両方で上げ、`uv lock` で `uv.lock` を更新する。
3. 依存の版が変わっていたら、`licenses/` の一覧を直す(「依存を更新するとき」を参照)。
4. チェックとテストを通し、exe をビルドして起動を確かめる。
5. `main` へ `--no-ff` でマージし、注釈つきのタグ `vX.Y.Z` を付ける。同じものを `develop` にもマージする。
6. 配布用の zip を作る。`data\` は入れない。

   ```powershell
   robocopy dist\NekoGantcha dist\release\NekoGantcha /E /XD data
   Compress-Archive -Path dist\release\NekoGantcha -DestinationPath dist\NekoGantcha-X.Y.Z-win64.zip
   ```

hotfix も同じ流れで、作る元が `main` になる。

## よく使うコマンド

```text
uv sync                                 依存のインストール
uv run python -m neko_gantcha           開発実行(データはプロジェクト直下の data\ に保存)
uv run pytest                           テスト
uv run pyinstaller build.spec --noconfirm   exe のビルド(アイコンを変えたときは --clean も付ける)
```

- exe のビルドは `dist\NekoGantcha\` を作り直すので、そこにある `data\` も消える。ビルドの前に `data\` があれば退避し、ビルドの後に戻す。
- ユーザーが起動しているアプリを勝手に終了しない。ファイルが使用中でビルドできないときは、ユーザーに閉じてもらう。

## 設計

```text
src/neko_gantcha/
├─ domain/     日付の計算、ToDo・カレンダー・ガントの配置(Qt に依存しない)
├─ data/       sqlite3 の接続、マイグレーション、バックアップ、JSON 入出力
├─ services/   AppStore(画面とデータの窓口、変更の通知、選択状態)
└─ ui/         PySide6 の画面
```

- `domain` は Qt も sqlite3 も import しない。純粋な関数とデータクラスにし、テストを書く。
- 画面(`ui`)はデータの読み書きを必ず `AppStore` を通して行い、`data` の関数を直接呼ばない(`data` から import してよいのは、`TransferError` のような例外クラスだけ)。`ui` から `domain` を直接呼ぶのは、データを変えない計算(表示の整形や判定など)に限る。
- データを変えたら `AppStore` のシグナルで画面に知らせ、各画面はシグナルを受けて描き直す。
- Qt のモジュールは QtCore / QtGui / QtWidgets だけを使う(`tests/test_qt_modules.py` で確かめている)。ほかのモジュールが必要になったら、ライセンスと配布の大きさに関わるので、先にユーザーに相談する。

### データ

- スキーマは `src/neko_gantcha/data/migrations/NNN_*.sql` を番号順に適用し、版を `PRAGMA user_version` に持つ。リリースした後のマイグレーションは書き換えず、新しい番号のファイルを足す。
- JSON のエクスポートの形式を変えたら、`data/transfer.py` の `FORMAT_VERSION` を上げ、古い形式の読み込みを考える。
- `app_settings`(配色などの端末ごとの設定)は JSON のエクスポートに含めない。
- データは exe と同じフォルダの `data\` に保存し、レジストリなどには書き込まない。

### 依存を更新するとき

- `licenses/THIRD_PARTY_NOTICES.md` の版を、実際に同梱するものに合わせる(PySide6 と Python の版は `tests/test_licenses.py` で確かめている)。
- Qt の版が変わったら、`licenses/QT_THIRD_PARTY_NOTICES.md` を、新しい版の qtbase にある `qt_attribution.json` をもとに直す。バイナリの配布に全文が要るライセンスの全文だけを `licenses/qt/` に置く。
- `licenses/` の `.txt` は原文のまま、改行を LF に保つ(`.gitattributes`)。

## 静的チェック

Ruff と ty を、ほぼすべてのルールを有効にして運用している。設定は `pyproject.toml` の `[tool.ruff]` と `[tool.ty]` にある。

### 作業を終える条件

変更した後は次の順に実行し、すべてエラーが 0 になるまで直す。テストも通す。

```text
uv run ruff format .
uv run ruff check --fix .
uv run ruff check .
uv run ty check
uv run pytest
```

- ty は warning でも終了コードが 1 になる(`error-on-warning = true`)。warning も残さない。
- `--unsafe-fixes` は使わない。必要なら手で直す。
- チェックを通さずに「完了」と報告しない。通らないものが残るときは、その内容と理由を報告する。

### やってはいけないこと

- `pyproject.toml` の Ruff と ty の設定を緩めること(`ignore` や `per-file-ignores` への追加、ルールを `"ignore"` や `"warn"` にすること、上限の引き上げ)。必要だと思ったら、変えずにユーザーに提案する。
- 引数のない抑制コメント(`# noqa`、`# ty: ignore`、`# type: ignore`)。`# type: ignore` は ty では無効にしている(`respect-type-ignore-comments = false`)ので、書いても効かない。
- エラーを消すためだけに `Any`、`cast()`、`assert` を入れること。

### どうしても抑制するとき

最後の手段にし、ルールを指定して理由を書く。PySide6 の API の都合でコードが複雑になる場合は、抑制してよい。抑制したら、そのことをユーザーに伝える。使われなくなった抑制コメントもエラーになる(RUF100、`unused-ignore-comment`)。

```python
result = legacy_api()  # ty: ignore[invalid-return-type]  # 外部ライブラリの型定義が誤っている
subprocess.run(cmd, check=True)  # noqa: S603  # cmd は固定値のリストで外部入力を含まない
```

## コーディング規約

- 1行は 100 文字まで、インデントはスペース 4 つ。
- docstring は Google スタイルで、本文は日本語。

### 型注釈(ANN、ty)

- すべての引数と戻り値に型注釈を付ける。`-> None` も省かない。
- `Any` は使わない(ANN401)。`object`、ジェネリクス、`Protocol` で表す。
- `X | None` を使い、`Optional` と `Union` は使わない。
- 型エイリアスとジェネリクスは PEP 695 の構文で書く(UP040、UP046、UP047)。

  ```python
  type Pair[T] = tuple[T, T]


  def first[T](items: list[T]) -> T: ...
  ```

- 親クラスのメソッドをオーバーライドするときは `@override`(`typing.override`)を付ける。
- `None` になりうる値は `if x:` で判定せず、`if x is not None:` と書く。
- 依存のパッケージを import するときは、`pyproject.toml` の `dependencies` にあるか確かめる。

### import

- 相対 import は使わない(TID252)。`from neko_gantcha.domain.models import Task` のように書く。
- 型注釈でしか使わない import は `if TYPE_CHECKING:` の中に入れる(TC001〜TC003、strict)。Python 3.14 では注釈の評価が遅れるので、型を文字列にしなくてよい。
- import はファイルの先頭に置く(PLC0415)。

### 関数とクラス(PLR、C901、FBT)

| 項目 | 上限 |
| --- | --- |
| 引数の数 | 5 |
| 位置引数の数 | 3 |
| 循環的複雑度 | 10 |
| 分岐の数 | 10 |
| return 文の数 | 4 |
| 文の数 | 40 |
| ローカル変数の数 | 12 |
| ネストの深さ | 3 |
| bool 式の項の数 | 4 |
| public メソッドの数 | 15 |

- 上限を超えそうなら、関数を分ける、dataclass で引数をまとめる、早めに return する、などで対処する。
- bool を位置引数にしない(FBT001、FBT002)。`*` の後ろにキーワード専用引数として置く。`ui/` では、Qt の `setEnabled(False)` のように bool を位置引数でしか渡せない API があるので、FBT003 だけを外している。
- `self` を使わないメソッドは `@staticmethod` かモジュールの関数にする(PLR6301)。
- マジック値(数値も文字列も)を比較に直接書かない(PLR2004)。定数か `Enum` にする。

### docstring(D、DOC)

- public なモジュール、パッケージ(`__init__.py`)、クラス、関数、メソッドには docstring を書く。
- 引数はすべて `Args:` に書く(D417)。戻り値は `Returns:`(ジェネレーターなら `Yields:`)、送出する例外は `Raises:` に書く。
- `__init__` には docstring を書かず、その引数はクラスの docstring の `Args:` に書く。
- 全角の記号は `（）：；！？，．～` だけを使ってよい。全角の英数字と全角のスペースは使わない(RUF001〜RUF003)。

### 例外とログ

- 例外のメッセージは変数に入れてから渡す(EM101、EM102、TRY003)。

  ```python
  msg = f"不正な値です: {value}"
  raise ValueError(msg)
  ```

- `except Exception:` や素の `except:` で握りつぶさない(BLE001)。
- `except` の中でログを出すときは `logger.exception()` を使う(TRY400)。
- ログに f-string を使わず、`logger.info("件数: %d", n)` の形で書く(G004)。
- `print()` は使わず、`logging` を使う(T201)。

### 標準ライブラリ

- パスの操作は `pathlib` を使い、`os.path` や `open()` は使わない(PTH)。
- テキストを読み書きするときは `encoding="utf-8"` を必ず指定する(PLW1514)。
- 日時はタイムゾーンなしの日本時間として扱い、ISO 8601 の文字列で保存する設計なので、DTZ は外している。
- 暗号の用途の乱数には `random` ではなく `secrets` を使う(S311)。

### その他

- コメントアウトしたコードを残さない(ERA001)。
- `TODO` や `FIXME` のコメントを残さない(FIX002)。どうしても残すときは、ユーザーに確かめたうえで issue のリンクを付ける(TD003)。
- 組み込みの名前(`id`、`type`、`list`、`input` など)を変数名やモジュール名に使わない(A001 など)。
- クラス属性に変更できる値を置くときは `ClassVar` を付ける(RUF012)。

### テスト(`tests/`)で外しているルール

`tests/**/*.py` に限り、次のルールを外している。型注釈など、それ以外のルールはテストにも当てはまる。

- `assert` の使用(S101)
- マジック値(PLR2004)
- private なメンバーへのアクセスと import(SLF001、PLC2701)
- `__init__.py` がないこと(INP001)
- docstring(D、DOC)

### ルールの調べ方

知らないルールのコードで指摘されたら、推測で直さずに説明を読む。

```text
uv run ruff rule PLR0917   Ruff のルールの説明
uv run ty check --help     ty のオプション
```

ty のルールの一覧: <https://docs.astral.sh/ty/reference/rules/>
