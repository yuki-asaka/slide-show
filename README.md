# スライドショー ビルドツール

写真・動画・BGM・字幕を `config/slides.yaml` に書くだけで、Ken Burns効果やクロスフェード付きの
スライドショー動画（MP4）を自動生成します。完成品は1本の動画ファイルなので、当日はオフラインの
どんな再生機器でも確実に上映できます。

## 主な機能

- Ken Burns・パン・クロスフェードなど、写真1枚から動きを作る各種エフェクト
- AI（深度推定）による奥行きのあるパララックス効果
- フィルムスクロール・プリント写真パイル・グリッドコラージュなど複数の演出スタイル
- 場面タイトル、字幕、ヴィンテージ調・パーティクル・ライトリークなどの装飾
- 長い動画をパーツ分けして作り、あとから1本に結合する機能
- Docker対応（ffmpeg等のローカルインストール不要で実行可能）

## クイックスタート

```sh
brew install ffmpeg uv   # 未導入の場合
uv sync                  # ライブラリを導入
uv run build_video.py --config config/slides.yaml
```

写真・動画・BGMを `assets/` 以下に置き、`config/slides.yaml` を編集してから実行してください。
`output/` 以下にMP4が生成されます。Dockerでの実行方法や詳しい設定方法は下記のドキュメントを
参照してください。

## ドキュメント

- [使い方（ローカル / Docker）](docs/usage.md)
- [config/slides.yaml の書き方](docs/configuration.md)
- [見た目のバリエーション（エフェクト・スタイル一覧）](docs/effects.md)
