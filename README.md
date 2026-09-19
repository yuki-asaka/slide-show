# スライドショー ビルドツール

写真・動画・BGM・字幕を `config/slides.yaml` に書くだけで、Ken Burns効果やクロスフェード付きの
スライドショー動画（MP4）を自動生成します。完成品は1本の動画ファイルなので、当日はオフラインの
どんな再生機器でも確実に上映できます。

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

## 使い方

1. 素材を配置する
   - 写真: `assets/photos/`
   - 動画: `assets/videos/`
   - BGM: `assets/bgm/`
2. `config/slides.yaml` を編集する（構成・エフェクト・字幕・BGMを指定）
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

## `config/slides.yaml` の書き方

```yaml
output:
  file: output/謝恩会スライドショー.mp4
  width: 1920
  height: 1080
  fps: 30

caption:
  font: "Hiragino Sans"        # macOS標準の日本語フォント名（fontconfig経由）
  # font_file: assets/fonts/xxx.ttf   # 別環境で確実に効かせたい場合はファイルを直接指定

defaults:
  photo_duration: 4             # 写真1枚の表示秒数（各スライドのdurationで上書き可）
  effect: kenburns-in           # none / kenburns-in / kenburns-out / pan-left / pan-right / pan-up / pan-down
                                 # / focus-in / frame-slide-up / parallax-left / parallax-right
                                 # / shake-exit-left / shake-exit-right（詳細は「見た目のバリエーション」参照）
                                 # ※ kenburns-in/out・pan-* は終了フレームに向かって減速するイーズアウトが常に適用されます
  transition: fade              # none / fade / wipeleft / wiperight / slideup / slidedown / circleopen など
  transition_duration: 1.0      # トランジションの長さ（秒）
  title_duration: 3.0           # type: title の表示秒数の既定値
  title_fade_duration: 1.0      # type: title のフェードイン秒数の既定値
  title_background_color: "#000000"  # type: title の背景画像省略時の既定色

bgm:
  file: assets/bgm/theme.mp3
  volume: 0.8
  fade_in: 2
  fade_out: 3

slides:
  - type: photo                 # photo / video / title（titleは「場面タイトル」節を参照）
    file: assets/photos/001.jpg
    duration: 5                 # 省略時は defaults.photo_duration（videoは元の長さ）
    effect: kenburns-in
    transition: fade            # このスライドへ入る際のトランジション（先頭スライドは無視）
    transition_duration: 1.0
    caption:
      text: "入学式"
      position: bottom          # top / bottom / center
```

- `type: video` のスライドは `duration` を省略すると動画自体の長さがそのまま使われます。
- エフェクト（Ken Burns・パン）は写真にのみ適用されます。動画には適用されません。
- 個々のスライドの `effect` / `transition` / `transition_duration` / `duration` は
  `defaults` の値を上書きします（未指定ならdefaultsが使われます）。
- `transition_duration` はそのスライド自身の `duration` より短くしてください（エラーになります）。

## 見た目のバリエーション

### フィルムスクロール（`style: film_scroll`）— シネマフィルム調のメイン演出

写真をフィルムのコマ（スプロケット穴付きの枠）にはめ込んだ、連続したコマからなる帯（フィルム
ストリップ）を`lanes`本（既定2本）並べて画面いっぱいに敷き詰め、それぞれのレーンが互い違いの
方向へ流れていくことで複数枚の写真を次々に見せる演出です。コマが片側から現れてもう片側へ
フレームアウトしていくことで「フィルムが流れている」ように見えます。

```yaml
style: film_scroll

film_scroll:
  direction: horizontal    # horizontal(横) / vertical(縦) / diagonal(斜め)
  lanes: 2                  # 並べるレーン数(縦スクロールなら列数、横・斜めなら行数)
  pace: 2.5                 # コマ1つ分が流れるのにかかる時間(秒)。大きいほど遅い
  gap: 6                    # コマ・レーン同士の隙間(px)
  reverse: false             # レーンの基準方向を反転(奇数番目のレーンは常に逆方向)
  angle: 25                  # direction: diagonal のときの傾き(度、水平から)
  background: "#000000"      # レーンの隙間などを埋める背景色

slides:
  - type: photo
    file: assets/photos/001.jpg
  - type: photo
    file: assets/photos/002.jpg
```

- `style: film_scroll` では `type: video` のスライドは使用できません（写真のみ）。
- 各スライドの `effect` / `transition` / `caption` / `duration` はこのスタイルでは無視されます。
- コマは横長（フィルム枠の自然な比率）のまま、レーン方向（縦スクロールなら列、横・斜めなら行）
  には画面を隙間なく分割するので、画面に余白がほぼ出ません。
- レーンは1本おきに逆方向へスクロールします（例: 横スクロールなら上段は右へ、下段は左へ）。
  `reverse` で全体の基準方向を入れ替えられます。
- `direction: diagonal` は横スクロールの帯を組んだあと、全体を `angle` 度だけ一定角度で
  傾けます。
- 写真が多いほど各レーンの合成画像が長くなるため、他のスタイルと同様に
  **8〜20枚程度** のハイライト向けです。
- `look: vintage`（下記）と組み合わせると、より「往年の映写機」らしい質感になります。

### ヴィンテージ色調（`look: vintage`）

トップレベルに `look: vintage` を追加すると、完成動画全体にフィルムグレイン・周辺減光・
ヴィンテージ色調をかけた後処理を1回だけ適用します（`look: none` が既定）。
`style` の種類を問わず（`standard` / `photo_pile` / `collage` / `film_scroll` いずれにも）
組み合わせられます。

```yaml
look: vintage
```

### ぼかし→ピントイン登場（`effect: focus-in`）

写真が登場する瞬間、ぼやけた状態から徐々にピントが合っていく上品な登場効果です。
`standard` スタイルの写真スライドで `effect: focus-in` を指定します。

```yaml
slides:
  - type: photo
    file: assets/photos/001.jpg
    effect: focus-in
```

- ピントが合うまでの時間は、そのスライドの `duration` の半分（最大1.2秒）です。

### 額装写真スライドイン（`effect: frame-slide-up`）

白フチ＋影のシンプルな額装写真が、画面の下から真っ直ぐスライドインしてくる登場効果です。

```yaml
slides:
  - type: photo
    file: assets/photos/001.jpg
    effect: frame-slide-up
```

- 写真は画面の80%サイズに収まるよう縮小され、白フチと柔らかい影が付きます。
- 停止位置に近づくほどゆっくりになるイージング（ease-out）でスライドインします。
- 背景色は `defaults.frame_background`（既定 `#000000`）で変更できます。

### 奥行きのある動き（`effect: parallax-left` / `parallax-right`）

AI（深度推定モデル）で写真の奥行きを推定し、手前のものほど大きく・奥のものほど
小さく水平方向に動かすことで、1枚の写真からKen Burnsより立体感のある動きを
作る効果です。`parallax-left` は手前が左へ、`parallax-right` は右へ動きます
（`parallax` は `parallax-left` の旧名で、後方互換のため引き続き使えます）。

```yaml
slides:
  - type: photo
    file: assets/photos/001.jpg
    effect: parallax-left   # または parallax-right
```

- 内部的に [Depth Anything V2 Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf)
  で深度マップを推定し、Python側で1フレームずつ視差ワープした画像を書き出してから
  ffmpegでエンコードします。**初回実行時にモデル（約100MB）をHugging Faceから
  ダウンロードします**（インターネット接続が必要）。
- `torch`/`transformers` という重い依存が追加されるため、`uv sync` の初回ダウンロード
  容量・時間が増えます（`parallax` を使わない場合でも依存関係としては導入されます）。
- Apple Silicon Macで `uv run` を使ってネイティブ実行した場合はMPSで高速に処理されます。
  **Docker（Linuxコンテナ）で実行するとMPSが使えずCPUのみになるため、大幅に遅くなります**
  （[Macで使うメリット](#dockerで使う)も参照）。
- 深度推定＋フレーム生成を1枚ずつ行うため、他のエフェクトより生成が遅くなります。
  ハイライトにしたい数枚に絞って使うのがおすすめです。
- `style: photo_pile` / `collage` / `film_scroll` では他のエフェクトと同様に無視されます。

### ぶれて退場（`effect: shake-exit-left` / `shake-exit-right`）

表示の終盤で左右に素早くぶれたあと、画面外へ加速しながらフェードアウトしていく
退場効果です。`shake-exit-left` は左へ、`shake-exit-right` は右へ抜けます。

```yaml
slides:
  - type: photo
    file: assets/photos/001.jpg
    effect: shake-exit-left
    transition: none    # 退場の直後はハードカットが自然に馴染む
```

- ほとんどの時間は静止表示で、`duration` の終わり側（最大0.5秒、durationが短い場合は
  duration×0.6秒）だけ「ぶれ→加速して画面外へフェードアウト」の動きになります。
- 直近フレームを合成する`tmix`により、動いている間だけ自然な残像状のモーションブラーが
  掛かります（静止部分は無変化のままです）。
- フェードアウトで見える背景色は `defaults.frame_background`（既定 `#000000`）です。
- **次のスライドの `transition` は `none`（ハードカット）にするのがおすすめです。**
  フェードアウトの直後に通常のクロスフェードが重なると見た目が濁ります。

### 疾走感のあるスライドショー

新しいスタイルではなく、既存の `standard` スタイルの設定の組み合わせ方のコツです。

```yaml
defaults:
  photo_duration: 0.5     # 写真1枚 = 曲のテンポの2拍分くらいが目安(150〜170BPMなら0.35〜0.5秒)
  transition: none         # ハードカット中心にすると勢いが出る
  transition_duration: 0.1

slides:
  - type: photo
    file: assets/photos/001.jpg
    effect: kenburns-in      # pan-left / pan-right / kenburns-out と混ぜると単調さが減る
  - type: photo
    file: assets/photos/002.jpg
    effect: pan-left
    transition: wipeleft     # たまにハードカット以外を挟むとアクセントになる
    transition_duration: 0.12
```

- **`photo_duration` はBPMの目安から逆算**します。四分音符1拍 = 60/BPM秒。
  150〜170BPM程度のロックなら1拍0.35〜0.4秒、2拍(0.7〜0.8秒)に1枚がよく馴染みます。
  もっと速くしたい場合は1拍に1枚(0.35〜0.4秒)まで詰められます。
- **`transition: none`（ハードカット）を基本にする**と、フェードでもたつかず勢いが出ます。
  全部ハードカットだと単調になりやすいので、数枚おきに `wipeleft` / `circleopen` などの
  短い（0.1〜0.15秒程度）トランジションを混ぜると良いアクセントになります。
- **`effect` は `kenburns-in` / `kenburns-out` / `pan-left` / `pan-right` を混ぜる**と、
  静止画の羅列でも動きの変化が出ます。いずれも表示時間いっぱいを使って終了フレームに
  向かって滑らかに減速するので、`photo_duration` が短くても唐突な印象になりません
  （以前あった `punch-zoom` は瞬間的な急ズームで見づらいため廃止しました）。
  セクションの締めくくりの1枚だけ `shake-exit-left` / `shake-exit-right`
  （続くスライドは `transition: none`）を使うと、ハードカットの連続に
  一呼吸のアクセントを付けられます。
- `look: vintage` は粒子感で落ち着いた雰囲気になるため、疾走感重視なら `look: none`
  （既定）のままの方が締まった印象になります。
- `config/sample_fastpaced.yaml` に実例があります。

### 桜吹雪・キラキラパーティクル（`particles`）

画面全体に花びらやキラキラが漂う装飾レイヤーです。`style` を問わず動画全体に重ねられます。

```yaml
particles: sakura   # none(既定) / sakura / sparkle
```

- 内部的には本編を書き出した後、パーティクルを重ねる追加のffmpegパスを実行するため、
  書き出し時間がやや増えます。

### ライトリーク（`light_leak`）

柔らかい光の筋が画面を斜めに横切っていく、ノスタルジックな雰囲気を作る演出です。
`particles` と同時に使う場合、パーティクルの上に重ねられます。

```yaml
light_leak: true
```

### フィルムストリップ遷移（`transition: filmstrip`）

`style: standard` のスライド間トランジションとして使う、1カットごとの効果です
（`style: film_scroll` とは別物で、こちらは通常のスライドショーの「切り替わりの瞬間」だけに
フィルム枠が重なります）。

スライドの `transition` に `filmstrip` を指定すると、スプロケット穴付きのフィルム枠
（`assets/overlays/film_frame.png`）を重ねながら切り替わる、映画のコマ送りのような
遷移になります。

```yaml
slides:
  - type: photo
    file: assets/photos/002.jpg
    transition: filmstrip
    transition_duration: 0.6
```

### プリント写真パイル（`style: photo_pile`）

トップレベルに `style: photo_pile` を指定すると、通常のKen Burns＋クロスフェードの代わりに、
白フチ付きの写真が1枚ずつランダムな角度で上から落ちてきて積み重なっていく、
「プリントした写真を机に置いていく」ような演出になります（`style: standard` が既定）。

```yaml
style: photo_pile

photo_pile:
  interval: 1.2         # 次の写真が置かれるまでの間隔（秒）
  place_duration: 0.5   # 1枚が落ちて静止するまでの時間（秒）
  hold_at_end: 2.5       # 最後に全体を見せる時間（秒）
  max_coverage: 0.63     # 画面に対する1枚の最大サイズ比率（停止時のサイズ）
  start_scale: 1.8        # 登場時、最終サイズの何倍から始まるか
  background: "#1a1a1a"   # 背景色

slides:
  - type: photo
    file: assets/photos/001.jpg
  - type: photo
    file: assets/photos/002.jpg
```

- `style: photo_pile` では `type: video` のスライドは使用できません（写真のみ）。
- 各スライドの `effect` / `transition` / `caption` / `duration` はこのスタイルでは無視されます。
- 各写真は画面の外側（`start_scale`倍の大きさ）から現れ、フェードではなく実際に落下・縮小
  しながら最終位置へ収まります。停止位置に近づくほど動きがゆっくりになる
  イージング（ease-out）が掛かっています。
- `rotate`・時間変化する`scale`フィルタを写真ごとに動画全編にわたって適用するため、標準
  スタイルより書き出しがかなり遅くなります。ハイライト用に **10〜25枚程度** に絞って使うのが
  おすすめです（50枚一括のような用途には不向き）。
- `look: vintage` と組み合わせ可能です。

### 複数枚同時表示（`style: collage`）

トップレベルに `style: collage` を指定すると、写真を `rows x cols` のグリッドに並べて
1枚ずつフェードインさせながら埋めていき、グリッドが揃ったら次のページへクロスフェードする
演出になります。写真の枚数がグリッドのマス数（`rows x cols`）を超える場合は自動的に
複数ページに分割され、最後のページは余ったマスが背景色のまま残ります。

```yaml
style: collage

collage:
  rows: 3
  cols: 3
  interval: 0.35          # グリッド内で次のマスが現れるまでの間隔（秒）
  place_duration: 0.3     # 1枚がフェードインする時間（秒）
  hold_page: 2.5           # ページが揃ってから次に移るまでの表示時間（秒）
  gap: 10                  # マス同士の隙間（ピクセル）
  transition: fade         # ページ間のトランジション（xfade準拠）
  transition_duration: 0.8
  background: "#1a1a1a"    # 背景色・マスの隙間の色

slides:
  - type: photo
    file: assets/photos/001.jpg
  - type: photo
    file: assets/photos/002.jpg
```

- `style: collage` では `type: video` のスライドは使用できません（写真のみ）。
- 各スライドの `effect` / `transition` / `caption` / `duration` はこのスタイルでは無視されます。
- 各マスは写真を切り抜いて敷き詰める表示（`crop`）になります。プリント写真風の白フチは
  付きません（白フチが欲しい場合は `style: photo_pile` を検討してください）。
- `look: vintage` と組み合わせ可能です。

## 場面タイトル（`type: title`）

`style: standard` の `slides:` の中に `type: title` のスライドを差し込むと、章タイトルの
ような画面をフェードインで挿入できます。背景は単色でも、画像を指定して敷き詰めることも
できます。

```yaml
slides:
  - type: title
    text: "体育祭"
    duration: 3
    background: assets/photos/undoukai_cover.jpg   # 省略可（省略時は単色背景）
    background_color: "#000000"                      # background省略時の背景色
    fade_duration: 1.0                                 # フェードインの長さ（秒）

  - type: photo
    file: assets/photos/001.jpg
```

- `text` は必須です。`background` を省略すると `background_color`（既定 `#000000`）の
  単色背景になります。
- タイトル文字は画面中央に大きく表示され、`fade_duration` 秒かけてフェードインします。
- タイトルの前後のスライドは、`transition` を明示的に指定しない限り自動的に
  `fadeblack`（黒を経由するクロスフェード）になり、章の切り替わりが自然に見えるように
  なっています（他のトランジションにしたい場合は普通にスライド側で `transition` を
  指定してください）。
- `duration` を省略すると `defaults.title_duration`（既定3秒）が使われます。
- `type: title` の `effect` / `caption` は無視されます。

## 長い動画をパーツ分けして作る（`concat`）

5分を超えるような長い動画を1本の `slides.yaml` で作ろうとすると、写真の指定枚数が
膨大になり、途中の1枚を差し替えたいだけでも該当箇所を探すのが大変になります。
そこで、次の2段階構成が使えます。

1. これまで通り `slides:` を使って、パーツごとに個別の動画を作る
   （例: `config/part1.yaml` → `output/part1.mp4`、`config/part2.yaml` → `output/part2.mp4` ...）
2. 作成済みのパーツ動画を、別のYAMLファイルの `concat:` に列挙して1本に結合する

```yaml
# config/combine.yaml
output:
  file: output/full.mp4

concat:
  - file: output/part1.mp4
  - file: output/part2.mp4
  - file: output/part3.mp4
```

```sh
uv run build_video.py --config config/combine.yaml
```

- `concat:` を含むYAMLは、通常の `slides:` 形式とは別モードとして扱われ、`style` や
  `caption` など他の設定は無視されます。
- 結合は**ハードカットのみ**です。ffmpegの `concat` デマルサで再エンコードなしに
  ストリームコピーするため、非常に高速です（クロスフェードでの結合は現時点では
  非対応です）。
- 結合する動画はすべて**解像度・fps・音声トラックの有無が一致**している必要があります
  （パーツを同じ `output.width`/`height`/`fps` で作っていれば自然に揃います）。
  揃っていない場合はエラーで教えます。
- パーツのファイルパスも、既定では `assets/`・`output/` 配下に制限されます
  （[パスの制限について](#パスの制限について--allow-outside-assets)を参照）。

## 既知の制約・拡張余地

- BGMのみを音声トラックとして使用します。動画素材自体が持つ音声は現状ミュートされます
  （必要になったら `keep_original_audio` のようなオプションを追加して `amix` する拡張が可能です）。
- 字幕フォントは `caption.font`（フォント名・fontconfig経由）または `caption.font_file`
  （フォントファイルへの直接パス）で指定します。日本語が文字化けする場合は、
  `fc-list | grep -i hiragino` 等で使えるフォント名を確認するか、`font_file` で直接
  `.ttc`/`.ttf` を指定してください。
- トランジションの種類は ffmpeg の `xfade` フィルタが対応するものがそのまま使えます
  （`fade` / `wipeleft` / `wiperight` / `slideup` / `slidedown` / `circleopen` など）。
  `filmstrip` は本ツール独自の特別なトランジションです。

## 素材について

- `assets/overlays/film_frame.png`（横方向用、スプロケットが上下）と
  `assets/overlays/film_frame_vertical.png`（縦方向用、スプロケットが左右）は、
  本プロジェクト用に生成したオリジナル素材です（角丸正方形の穴・細めの縁取りで写真の見える
  面積を広くしています）。縦方向用は横長のコマの縦横比に合わせて別途描いたもので、横方向用を
  単純に回転させたものではありません（回転+非等倍の拡縮だと穴が楕円に潰れるため）。
- `assets/overlays/sakura_petal.png` / `assets/overlays/sparkle.png` も本プロジェクト用に
  生成したオリジナル素材です（`particles` で使用）。
