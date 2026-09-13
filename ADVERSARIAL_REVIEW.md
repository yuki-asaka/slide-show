# 敵対的検証レポート（build_video.py）

- 対象: `build_video.py`（現在の全体）、`Dockerfile`
- 観点: `config/slides.yaml` の内容を **信頼できない入力** として扱った場合に何が起きるか
  （設定ファイルが第三者から共有された・ネットからダウンロードした・別人が用意した、
  というケースを想定）
- 方法: コードリーディングに加え、実際に `uv run build_video.py` を実行して
  再現性を確認（PoCはすべてこのリポジトリ外の一時ディレクトリ上で実施し、
  検証後に削除済み）
- 前提: 現状は「自分のPCで自分の設定ファイルを実行する」用途が主だが、
  リモートリポジトリを公開した直後であり、サンプルconfigの共有や
  他者作成のconfigを実行する可能性が今後出てくるため、
  「configは攻撃者が細工できる」という前提での検証を行った

## 対応状況（検出事項1〜4）

検出事項1〜4は対応済み（再現テストにより修正を確認済み）。検出事項5は
一般的な依存ライブラリのリスクの指摘であり、コード変更による対応は行っていない。

- **1（パス・トラバーサル）**: `resolve_config_path()` を追加し、
  `slides[].file` / `bgm.file` / `caption.font_file` / タイトルの `background` /
  `output.file` を既定で `assets/`・`output/` 配下に封じ込めるようにした。
  ただし完全に禁止すると「`assets/` にコピーせず外部の写真フォルダを直接参照する」
  という実際の使い方の利便性を損なうため、**`--allow-outside-assets`
  コマンド引数を指定した場合のみ従来どおり任意パスを許可する**（既定はOFF）。
  実際に絶対パスでのプロジェクト外ファイル読み取り・書き込みが既定でブロックされ、
  フラグ指定時のみ通ることを再テストで確認した。
- **2（フィルタインジェクション）**: `validate_filter_safe()` を追加し、
  色・フォント名などフィルタ文字列へ直接埋め込まれる値に
  `: , ; ' " \ [ ] =` を含められないようにした。報告時のPoC
  （`photo_pile.background` へのカンマ経由でのフィルタ挿入）が
  拒否されることを再テストで確認した。
- **3（数値上限未検証）**: `bounded()` を追加し、width/height/fps や
  各種duration・lanes・rows/colsなどに現実的な上下限を設定した。
- **4（Dockerがroot実行）**: `Dockerfile` に非rootユーザー（既定UID/GID:
  1000、`--build-arg UID/GID` で上書き可）を追加し、`USER` 切り替え後に
  `uv sync` およびアプリの実行を行うようにした。非rootで動作し、
  マウントした `output/` への書き込みも可能なことを確認した。

## 検出事項

### 1. [重大] パス・トラバーサル / 絶対パス上書きによる任意ファイル読み取り・上書き（実証済み）

**該当箇所**: `load_config()` 内のすべての `base_dir / <configの値>` 箇所
（`build_video.py:148` output_file, `:156` font_file, `:217` bgm.file,
`:249` title.background, `:255` slide.file）

**原因**: Python の `pathlib.Path.__truediv__` は右辺が絶対パスの場合、
左辺（`base_dir`）を**完全に無視**する。また `..` を含む相対パスも
正規化・拒否されない。存在チェック（`path.exists()`）はあるが、
「`assets/` 配下に収まっているか」という**封じ込めチェックが一切ない**。

```python
>>> from pathlib import Path
>>> Path("/project/assets") / "/etc/passwd"
PosixPath('/etc/passwd')
```

**再現手順**（実施・確認済み）:

```yaml
# /tmp/adv_test/config/slides.yaml
output:
  file: /tmp/adv_test_output.mp4      # プロジェクト外への絶対パス
slides:
  - type: photo
    file: /Users/yuki/dev/IdeaProjects/slide-show/assets/overlays/sparkle.png  # assets/photos/ 外
    duration: 1
```

```sh
uv run build_video.py --config /tmp/adv_test/config/slides.yaml
```

→ 警告やエラーなく実行が成功し、`/tmp/adv_test_output.mp4` が実際に生成されることを確認した
（`ffprobe` で正常なMP4として再生時間1.0秒を確認）。

**影響**:
- **任意ファイル読み取り**: `slides[].file` / `bgm.file` / `title.background` /
  `caption.font_file` に `../../../../Users/xxx/secret.jpg` や絶対パスを指定すると、
  意図した `assets/` の外の任意のファイルを「写真」として読み込み、
  完成動画に合成してしまう（情報漏えい。動画を人に渡す/公開すれば内容が流出する）。
- **任意ファイル上書き**: `output.file` に `~/.ssh/authorized_keys` や
  `~/.zshrc`、cron定義ファイルなどの絶対パスを指定すると、
  `ffmpeg -y`（常に確認なし上書き）によって**実行ユーザーが書き込み権限を持つ
  任意のファイルがMP4バイナリで上書き・破壊される**。

**対策案**: 各パスを `.resolve()` した上で、
`resolved.is_relative_to(allowed_root.resolve())`（Python 3.9+）で
許可ディレクトリ（`assets/` や `output/` など）配下に収まっているかを検証し、
外れていれば `fail()` で拒否する。全ての `base_dir / ...` 箇所に横展開が必要。

---

### 2. [中〜高] ffmpegフィルタグラフへの未エスケープ文字列埋め込み（フィルタインジェクション、実証済み）

**該当箇所**: `pile_background` / `collage_background` / `film_scroll_background` /
`title_background_color` / `frame_effect_background` などが
`color=c={値}:s=...` のように**クォートなし・エスケープなしで直接f-string展開**されている
（`build_filter_complex` / `build_photo_pile_filter_complex` /
`build_collage_filter_complex` / `render_film_scroll_lane_canvases` 各所）。
`esc()` 関数は存在するが、`font` / `font_file` を `'...'` で囲む箇所にしか
適用されておらず、色指定などクォートなしで展開される値には一切効いていない。

**再現手順**（実施・確認済み）:

```yaml
style: photo_pile
photo_pile:
  background: "black,drawtext=text=INJECTED:fontcolor=white:fontsize=40:x=10:y=10"
```

`--dry-run` の出力（実際に生成されたfilter_complex）:

```
color=c=black,drawtext=text=INJECTED:fontcolor=white:fontsize=40:x=10:y=10:s=1920x1080:d=3.000:r=30[bg0];
```

`background` に仕込んだカンマにより、想定していた `color` フィルタの後ろに
**まったく別の `drawtext` フィルタが1つ丸ごと挿入**されることを確認した
（今回のPoCは末尾に不正なオプションが付いてffmpeg自体はエラーになる想定だが、
攻撃者は `w/h/fps` などconfig側の他の値も自分で完全にコントロールできるため、
末尾のつじつまを合わせて**構文的に正しいフィルタグラフへ任意のフィルタを追加する
ことは十分可能**）。

**影響**: この経路単体でOSコマンド実行はできないが、ffmpegの `movie=`/`amovie=`
（別ファイルを読み込むソースフィルタ）や、ビルドによっては `zmq`/`azmq`
（実行中のフィルタグラフをネットワーク経由で操作するフィルタ）などを
グラフに追加挿入できる可能性がある。ただし**検出事項1（任意ファイル読み取り）が
既により直接的な手段を提供している**ため、実害の観点では1の方が優先度が高い。
根本原因は共通（「configの文字列値を検証・エスケープせずにそのまま
コマンド/フィルタ文字列へ埋め込んでいる」）。

**対策案**: 色指定は `^#?[0-9a-fA-F]{6}$` 等の厳格な正規表現でホワイトリスト検証する。
自由入力を許す設計にする場合は、コロン・カンマ・セミコロン・角カッコ・
シングルクォートを含む値を拒否するか、ffmpegのフィルタオプション用エスケープ
仕様（`\:` `\,` 等）を全箇所に一貫して適用する。

---

### 3. [低〜中] 数値パラメータに上限バリデーションがない（DoS、コード確認のみ・未実行）

`width` / `height` / `fps` / `duration` / `photo_pile.*` / `collage.rows`×`cols` /
`film_scroll.lanes` などは `int()`/`float()` で型変換されるのみで、
上限・妥当性チェックが存在しない（`load_config` 全体を参照）。
`collage.rows/cols` のみ「1未満なら `fail()`」という下限チェックがあるが、
上限は一切ない。

例えば `width: 100000000` や `film_scroll.lanes: 100000` のような極端な値を
指定すると、ffmpegが巨大なフレームバッファ確保やフィルタグラフの
爆発的な入力数展開を試み、実行ホストのメモリ枯渇・長時間ハングを
引き起こし得る（実行はしていないため実害の程度は未確認、PLAUSIBLE）。

**対策案**: 実用上あり得る範囲（例: width/height ≤ 7680, fps ≤ 120,
lanes ≤ 16 など）で上限チェックを追加し、超過時は `fail()` で
分かりやすく拒否する。

---

### 4. [参考/低] Dockerコンテナがrootユーザーで実行される

`Dockerfile` に `USER` 指定がなく、コンテナ内プロセスは root のまま動く。
ローカルで完結するバッチ変換ツールのため実害は限定的だが、
多層防御（検出事項1・2が突破された場合の被害範囲限定）の観点では、
非rootユーザーを作成し `USER` で切り替える余地がある。

---

### 5. [参考] 信頼できないメディアファイルのデコードに伴う一般的リスク

写真・動画は最終的に Pillow / pillow-heif / ffmpeg（libavcodec等）で
デコードされる。これらC実装ライブラリの脆弱性を突く細工済みメディアファイルを
「写真」として渡された場合、本ツール自体のロジックの欠陥ではなく依存ライブラリの
CVEにより影響を受け得る。検出事項1（任意ファイル読み取り）と組み合わさると、
攻撃者が指定した任意パスのファイルを本ツールにデコードさせる経路が
成立する点には留意したい。

**対策案**: ffmpeg / Pillow / pillow-heif を継続的に最新へ追従する
（`uv.lock` の定期更新）。信頼できない入力を扱う可能性が高まるなら、
コンテナのread-onlyルートFS化やseccompプロファイルの適用も検討余地がある。

## 良好だった点（確認済み）

- `yaml.safe_load()` を使用しており、YAML経由の任意コード実行
  （`yaml.load` のデシリアライズ脆弱性）の心配はない。
- すべての `subprocess.run()` 呼び出しがリスト引数形式で `shell=True` を
  使っておらず、OSコマンドインジェクションの経路はない
  （ファイルパスに空白や特殊文字が含まれていても安全）。
- `CLAUDE.md` に記録されている既知のffmpeg不具合
  （`zoompan` への複製フレーム供給、連鎖concat+xfadeでのフレームドロップ）
  への対策が、現行コードの `zoompan_expr`/`build_segment_filter` と
  `flush_pending()` に正しく実装されていることをコードレベルで確認した。
- 字幕本文（`caption.text`）はフィルタ文字列へ直接埋め込まれず、
  一時ファイル経由で `drawtext` の `textfile` オプションに渡されるため、
  キャプション本文からのフィルタインジェクションは発生しない。

## 総括・優先度

| # | 内容 | 深刻度 | 検証状況 |
|---|------|--------|----------|
| 1 | パストラバーサル／絶対パスによる任意ファイル読み取り・上書き | 重大 | 実行して再現確認済み |
| 2 | ffmpegフィルタグラフへの未エスケープ文字列埋め込み | 中〜高 | 実行して再現確認済み |
| 3 | 数値パラメータの上限未検証（DoS） | 低〜中 | コード確認のみ |
| 4 | Dockerコンテナがroot実行 | 参考/低 | コード確認のみ |
| 5 | 信頼できないメディアのデコードリスク | 参考 | 一般的リスクの指摘 |

現状の主な利用形態（自分専用のPCで自分が書いたconfigのみを実行する）では
実害は限定的だが、リモートリポジトリを公開した直後でもあり、
**「他者から受け取った、または配布されたconfigを実行する」運用を
今後少しでも想定するなら、検出事項1・2の修正を先に行うことを推奨する。**
