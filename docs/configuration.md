# config/slides.yaml の書き方

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
                                 # / shake-exit-left / shake-exit-right / mono-fade-out
                                 # （詳細は見た目のバリエーションを参照）
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
- エフェクト（Ken Burns・パン等）は写真にのみ適用されます。動画には適用されません。
  種類は [見た目のバリエーション](effects.md) を参照してください。
- 個々のスライドの `effect` / `transition` / `transition_duration` / `duration` は
  `defaults` の値を上書きします（未指定ならdefaultsが使われます）。
- `transition_duration` はそのスライド自身の `duration` より短くしてください（エラーになります）。

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
- 結合する動画はすべて**解像度・fps・音声パラメータ（サンプルレート/チャンネル数）が
  一致**している必要があります（パーツを同じ `output.width`/`height`/`fps` で作っていれば
  自然に揃います）。揃っていない場合はエラーで教えます。
- パーツのファイルパスも、既定では `assets/`・`output/` 配下に制限されます
  （[パスの制限について](usage.md#パスの制限について--allow-outside-assets)を参照）。

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
