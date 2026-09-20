# 使い方

## 事前準備

1. **ffmpeg をインストール**（未導入の場合）
   ```sh
   brew install ffmpeg
   ```
2. **uv をインストール**（未導入の場合）
   ```sh
   brew install uv
   ```
3. **ライブラリを導入**（初回のみ。`pyproject.toml` / `uv.lock` を元に
   `.venv` が自動作成されます）
   ```sh
   uv sync
   ```

## ローカルで実行する

1. 素材を配置する
   - 写真: `assets/photos/`
   - 動画: `assets/videos/`
   - BGM: `assets/bgm/`
2. `config/slides.yaml` を編集する（構成・エフェクト・字幕・BGMを指定。書き方は
   [config/slides.yaml の書き方](configuration.md) を参照）
3. 動画を生成する
   ```sh
   uv run build_video.py --config config/slides.yaml
   ```
   → `output/` 以下にMP4が生成されます。
4. コマンドを実行せずに確認だけしたい場合は `--dry-run` を付ける
   ```sh
   uv run build_video.py --config config/slides.yaml --dry-run
   ```

## パスの制限について（`--allow-outside-assets`）

`slides[].file` / `bgm.file` / `caption.font_file` / タイトルの `background` /
`output.file` は、既定では **`assets/`（読み込み系）・`output/`（書き出し先）の
配下しか指定できません**。これは、第三者から受け取った `slides.yaml` を
うっかり実行した場合に、プロジェクト外の任意のファイルを読み込んだり
（情報漏えい）、任意のファイルを上書きしたり（`ffmpeg -y` は常に無確認で
上書きするため）することを防ぐためのガードです。

自分のPhotosライブラリなど、`assets/` にコピーせず外部のフォルダを直接
参照したい場合は `--allow-outside-assets` を付けてこの制限を無効化できます。

```sh
uv run build_video.py --config config/slides.yaml --allow-outside-assets
```

**自分で書いた・内容を把握しているconfigに対してのみ**このオプションを
使ってください。他人から受け取ったconfigに対しては付けないことを推奨します。

## Dockerで使う

ffmpegやPythonライブラリをローカルに入れたくない場合は、Dockerで実行できます。
`assets/` `config/` `output/` はイメージに含めず、実行時にボリュームとしてマウントする
構成なので、素材やYAMLを変更してもイメージの再ビルドは不要です。

### 事前準備

- [Docker Desktop](https://www.docker.com/products/docker-desktop/) など、Docker
  が使える環境を用意する。

### イメージをビルドする（初回・Dockerfile変更時のみ）

```sh
docker compose build
```

（`docker compose` が使えない場合は `docker build -t slide-show .`）

コンテナ内は非rootユーザー（既定UID/GID: 1000）で動作します。Linuxホストで
マウントした `output/` への書き込み権限エラーが出る場合は、ホストのUID/GIDを
指定してビルドし直してください（macOSのDocker Desktopでは通常不要です）。

```sh
UID=$(id -u) GID=$(id -g) docker compose build
```

### 動画を生成する

1. 素材を配置する
   - 写真: `assets/photos/`
   - 動画: `assets/videos/`
   - BGM: `assets/bgm/`
2. `config/slides.yaml` を編集する
3. 動画を生成する
   ```sh
   docker compose run --rm slide-show --config config/slides.yaml
   ```
   → ホスト側の `output/` 以下にMP4が生成されます。

   `docker compose` を使わない場合:
   ```sh
   docker run --rm \
     -v "$(pwd)/config:/work/config" \
     -v "$(pwd)/assets:/work/assets" \
     -v "$(pwd)/output:/work/output" \
     slide-show --config config/slides.yaml
   ```
4. `--dry-run` も同様に付けられます。
   ```sh
   docker compose run --rm slide-show --config config/slides.yaml --dry-run
   ```
5. `--allow-outside-assets` も同様に付けられます（[パスの制限について](#パスの制限について--allow-outside-assets)を参照）。
   ```sh
   docker compose run --rm slide-show --config config/slides.yaml --allow-outside-assets
   ```

### 字幕フォントについて（Docker環境固有の注意）

コンテナのベースイメージ（Debian）には macOS の `Hiragino Sans` は存在しないため、
`caption.font` を省略・デフォルトのままにすると字幕が正しく表示されません。
イメージには日本語フォント一式（`fonts-noto-cjk`）を同梱しているので、
`config/slides.yaml` の `caption.font` を以下のように変更してください。

```yaml
caption:
  font: "Noto Sans CJK JP"
```

コンテナ内で使えるフォント名を確認したい場合:

```sh
docker compose run --rm --entrypoint fc-list slide-show | grep -i noto
```

自前のフォントファイルを使いたい場合は `assets/fonts/` に `.ttf`/`.ttc` を置き、
`caption.font_file` で指定すれば（`assets/` はマウントされるため）そのまま使えます。

```yaml
caption:
  font_file: assets/fonts/your-font.ttf
```

### Macで使うメリット（ネイティブ実行 vs Docker）

- **速度**: Docker Desktop（特にApple Silicon）はLinux VM上でコンテナを動かすため、
  `libx264`のエンコードやマウントしたボリューム越しの大量の写真/動画I/Oでオーバーヘッドが
  乗ります。ネイティブ実行ならこの分だけ速いはずです。
- **フォント**: `caption.font: "Hiragino Sans"`がそのまま使えます。Dockerだと
  `Noto Sans CJK JP`への変更が必須です。
- **任意パスへのアクセスのしやすさ**: `~/Pictures/...`のようなプロジェクト外のフォルダを
  直接参照する場合、ネイティブなら`--allow-outside-assets`だけで済みますが、Dockerだと
  そのフォルダを追加でボリュームマウントする設定がもう一手間必要です。
- **`effect: parallax`のGPU高速化**: Apple SiliconのMPSはDockerコンテナからは使えないため、
  ネイティブ実行でないと高速化の恩恵を受けられません（詳細は
  [見た目のバリエーション](effects.md#奥行きのある動きeffect-parallax)を参照）。
- 開発中に `build_video.py` 自体を編集する場合も、ネイティブならすぐ反映されますが、
  Dockerはイメージの再ビルドが必要です。

逆にDockerの利点は「ffmpeg/uvのインストール不要・環境差異を気にしなくてよい再現性」なので、
自分のMacで使う分にはネイティブ（`uv run`）の方が素直で速く、Dockerは「別のPC・CI・人に配る」
ときの選択肢、という位置づけがおすすめです。
