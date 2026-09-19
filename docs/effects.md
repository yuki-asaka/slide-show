# 見た目のバリエーション

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

### 奥行きのある動き（`effect: parallax`）

AI（深度推定モデル）で写真の奥行きを推定し、手前のものほど大きく・奥のものほど
小さく水平方向に動かすことで、1枚の写真からKen Burnsより立体感のある動きを
作る効果です。

```yaml
slides:
  - type: photo
    file: assets/photos/001.jpg
    effect: parallax
```

- 内部的に [Depth Anything V2 Small](https://huggingface.co/depth-anything/Depth-Anything-V2-Small-hf)
  で深度マップを推定し、Python側で1フレームずつ視差ワープした画像を書き出してから
  ffmpegでエンコードします。**初回実行時にモデル（約100MB）をHugging Faceから
  ダウンロードします**（インターネット接続が必要）。
- `torch`/`transformers` という重い依存が追加されるため、`uv sync` の初回ダウンロード
  容量・時間が増えます（`parallax` を使わない場合でも依存関係としては導入されます）。
- Apple Silicon Macで `uv run` を使ってネイティブ実行した場合はMPSで高速に処理されます。
  **Docker（Linuxコンテナ）で実行するとMPSが使えずCPUのみになるため、大幅に遅くなります**
  （[Macで使うメリット](usage.md#macで使うメリットネイティブ実行-vs-docker)も参照）。
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

## 素材について

- `assets/overlays/film_frame.png`（横方向用、スプロケットが上下）と
  `assets/overlays/film_frame_vertical.png`（縦方向用、スプロケットが左右）は、
  本プロジェクト用に生成したオリジナル素材です（角丸正方形の穴・細めの縁取りで写真の見える
  面積を広くしています）。縦方向用は横長のコマの縦横比に合わせて別途描いたもので、横方向用を
  単純に回転させたものではありません（回転+非等倍の拡縮だと穴が楕円に潰れるため）。
- `assets/overlays/sakura_petal.png` / `assets/overlays/sparkle.png` も本プロジェクト用に
  生成したオリジナル素材です（`particles` で使用）。
