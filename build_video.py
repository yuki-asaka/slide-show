#!/usr/bin/env python3
"""
YAML設定(config/slides.yaml)から写真・動画・BGMを読み込み、
Ken Burns/パン/クロスフェード/字幕付きのスライドショー動画をffmpegで書き出す。

使い方:
    python3 build_video.py --config config/slides.yaml
    python3 build_video.py --config config/slides.yaml --dry-run   # コマンド確認のみ
"""

from __future__ import annotations

import argparse
import math
import random
import shutil
import subprocess
import sys
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import yaml
from PIL import Image, ImageOps
import pillow_heif

pillow_heif.register_heif_opener()

VALID_EFFECTS = {
    "none", "kenburns-in", "kenburns-out",
    "pan-left", "pan-right", "pan-up", "pan-down",
    "focus-in", "frame-slide-up",
    "shake-exit-left", "shake-exit-right",
}
VALID_LOOKS = {"none", "vintage"}
VALID_PARTICLES = {"none", "sakura", "sparkle"}
VALID_STYLES = {"standard", "photo_pile", "collage", "film_scroll"}
VALID_SCROLL_DIRECTIONS = {"horizontal", "vertical", "diagonal"}

FILM_STRIP_PATH = Path(__file__).resolve().parent / "assets" / "overlays" / "film_frame.png"
# 縦スクロール用(スプロケットが左右)。回転+非等倍拡縮だと穴が楕円に潰れるため、
# 横長セルの縦横比のまま最初から専用に描いたものを使う。
FILM_STRIP_VERTICAL_PATH = Path(__file__).resolve().parent / "assets" / "overlays" / "film_frame_vertical.png"

SAKURA_PARTICLE_PATH = Path(__file__).resolve().parent / "assets" / "overlays" / "sakura_petal.png"
SPARKLE_PARTICLE_PATH = Path(__file__).resolve().parent / "assets" / "overlays" / "sparkle.png"
PARTICLE_PROFILES = {
    "sakura": {
        "asset": SAKURA_PARTICLE_PATH, "count": 18,
        "size_range": (50, 110), "fall_range": (6.0, 11.0),
        "sway_range": (30, 90), "rotate_range": (0.3, 1.2),
    },
    "sparkle": {
        "asset": SPARKLE_PARTICLE_PATH, "count": 22,
        "size_range": (18, 46), "fall_range": (5.0, 9.0),
        "sway_range": (40, 110), "rotate_range": (1.0, 2.5),
    },
}

# 「シネマフィルム調」の後処理フィルタチェーン
# curves=preset=vintage: ffmpeg内蔵のヴィンテージ色調カーブ
# noise: フィルムグレイン / vignette: 周辺減光 / eq: 軽いコントラスト・彩度補正
VINTAGE_LOOK_FILTER = (
    "curves=preset=vintage,noise=alls=20:allf=t+u,vignette,eq=contrast=1.05:saturation=0.85"
)


@dataclass
class Caption:
    text: str
    position: str = "bottom"  # top / bottom / center


@dataclass
class Slide:
    kind: str  # "photo" / "video" / "title"
    path: Optional[Path]
    duration: Optional[float]
    effect: str
    transition: str
    transition_duration: float
    caption: Optional[Caption]
    title_text: Optional[str] = None
    title_bg_color: str = "#000000"
    title_fade_duration: float = 1.0


@dataclass
class BGMConfig:
    path: Path
    volume: float
    fade_in: float
    fade_out: float


@dataclass
class Config:
    output_file: Path
    width: int
    height: int
    fps: int
    font: str
    font_file: Optional[Path]
    slides: list[Slide]
    bgm: Optional[BGMConfig]
    look: str
    style: str
    pile_interval: float
    pile_place_duration: float
    pile_hold_at_end: float
    pile_max_coverage: float
    pile_start_scale: float
    pile_background: str
    collage_rows: int
    collage_cols: int
    collage_interval: float
    collage_place_duration: float
    collage_hold_page: float
    collage_gap: int
    collage_transition: str
    collage_transition_duration: float
    collage_background: str
    film_scroll_direction: str
    film_scroll_pace: float
    film_scroll_gap: int
    film_scroll_reverse: bool
    film_scroll_background: str
    film_scroll_angle: float
    film_scroll_lanes: int
    frame_effect_background: str
    particles: str
    light_leak: bool


def fail(message: str) -> None:
    print(f"設定エラー: {message}", file=sys.stderr)
    sys.exit(1)


# ffmpegフィルタ文字列(color=c=..., font=... など)に検証なしで埋め込むと、
# ここに列挙した文字を使って別のフィルタを挿入されたり(フィルタインジェクション)、
# クォートを閉じて出力オプションを改ざんされたりする恐れがある。
FORBIDDEN_FILTER_CHARS = set(":,;'\"\\[]=")


def validate_filter_safe(value: str, label: str) -> str:
    bad = sorted(FORBIDDEN_FILTER_CHARS & set(value))
    if bad:
        fail(f"{label} に使用できない文字が含まれています({''.join(bad)}): {value!r}")
    return value


def bounded(value: float, lo: float, hi: float, label: str) -> float:
    if not (lo <= value <= hi):
        fail(f"{label} は {lo}〜{hi} の範囲にしてください（現在値: {value}）")
    return value


def resolve_config_path(
    base_dir: Path, value: str, allowed_root: Path, allow_outside_assets: bool, label: str
) -> Path:
    """configで指定されたパスをbase_dir基準で解決する。

    `Path("/project") / "/etc/passwd"` が base_dir を無視して "/etc/passwd" に
    なってしまうpathlibの挙動や、"../" によるディレクトリトラバーサルを悪用されると、
    assets/output の外にある任意ファイルの読み取り・上書きを許してしまう。
    既定では allowed_root（assets/ や output/）の外を指すパスを拒否し、
    --allow-outside-assets 指定時のみ意図的に許可する。
    """
    resolved = (base_dir / value).resolve()
    if not allow_outside_assets and not resolved.is_relative_to(allowed_root):
        fail(
            f"{label} が {allowed_root} の外を指しています: {resolved}\n"
            f"（意図している場合は --allow-outside-assets を指定してください）"
        )
    return resolved


def load_config(config_path: Path, allow_outside_assets: bool = False) -> Config:
    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    base_dir = config_path.resolve().parent.parent
    assets_root = (base_dir / "assets").resolve()
    output_root = (base_dir / "output").resolve()

    out = raw.get("output", {})
    output_file = resolve_config_path(
        base_dir, out.get("file", "output/slideshow.mp4"), output_root, allow_outside_assets, "output.file"
    )
    width = bounded(int(out.get("width", 1920)), 64, 7680, "output.width")
    height = bounded(int(out.get("height", 1080)), 64, 7680, "output.height")
    fps = bounded(int(out.get("fps", 30)), 1, 120, "output.fps")

    cap_cfg = raw.get("caption", {})
    font = validate_filter_safe(cap_cfg.get("font", "Hiragino Sans"), "caption.font")
    font_file_raw = cap_cfg.get("font_file")
    font_file = (
        resolve_config_path(base_dir, font_file_raw, assets_root, allow_outside_assets, "caption.font_file")
        if font_file_raw else None
    )

    defaults = raw.get("defaults", {})
    default_photo_duration = bounded(float(defaults.get("photo_duration", 4)), 0.01, 3600, "defaults.photo_duration")
    default_effect = defaults.get("effect", "kenburns-in")
    default_transition = defaults.get("transition", "fade")
    default_transition_duration = bounded(
        float(defaults.get("transition_duration", 1.0)), 0, 30, "defaults.transition_duration"
    )
    default_title_duration = bounded(float(defaults.get("title_duration", 3.0)), 0.01, 3600, "defaults.title_duration")
    default_title_fade_duration = bounded(
        float(defaults.get("title_fade_duration", 1.0)), 0, 30, "defaults.title_fade_duration"
    )
    default_title_bg_color = validate_filter_safe(
        defaults.get("title_background_color", "#000000"), "defaults.title_background_color"
    )

    look = raw.get("look", "none")
    if look not in VALID_LOOKS:
        fail(f"look は {sorted(VALID_LOOKS)} のいずれかにしてください（{look}）")

    style = raw.get("style", "standard")
    if style not in VALID_STYLES:
        fail(f"style は {sorted(VALID_STYLES)} のいずれかにしてください（{style}）")

    pile_cfg = raw.get("photo_pile", {})
    pile_interval = bounded(float(pile_cfg.get("interval", 1.2)), 0, 600, "photo_pile.interval")
    pile_place_duration = bounded(float(pile_cfg.get("place_duration", 0.5)), 0.01, 600, "photo_pile.place_duration")
    pile_hold_at_end = bounded(float(pile_cfg.get("hold_at_end", 2.5)), 0, 600, "photo_pile.hold_at_end")
    pile_max_coverage = bounded(float(pile_cfg.get("max_coverage", 0.63)), 0.01, 1.0, "photo_pile.max_coverage")
    pile_start_scale = bounded(float(pile_cfg.get("start_scale", 1.8)), 1.0, 10.0, "photo_pile.start_scale")
    pile_background = validate_filter_safe(pile_cfg.get("background", "#1a1a1a"), "photo_pile.background")

    collage_cfg = raw.get("collage", {})
    collage_rows = bounded(int(collage_cfg.get("rows", 3)), 1, 20, "collage.rows")
    collage_cols = bounded(int(collage_cfg.get("cols", 3)), 1, 20, "collage.cols")
    collage_interval = bounded(float(collage_cfg.get("interval", 0.35)), 0, 600, "collage.interval")
    collage_place_duration = bounded(float(collage_cfg.get("place_duration", 0.3)), 0.01, 600, "collage.place_duration")
    collage_hold_page = bounded(float(collage_cfg.get("hold_page", 2.5)), 0, 600, "collage.hold_page")
    collage_gap = bounded(int(collage_cfg.get("gap", 10)), 0, 500, "collage.gap")
    collage_transition = collage_cfg.get("transition", "fade")
    collage_transition_duration = bounded(
        float(collage_cfg.get("transition_duration", 0.8)), 0, 30, "collage.transition_duration"
    )
    collage_background = validate_filter_safe(collage_cfg.get("background", "#1a1a1a"), "collage.background")

    scroll_cfg = raw.get("film_scroll", {})
    film_scroll_direction = scroll_cfg.get("direction", "horizontal")
    if film_scroll_direction not in VALID_SCROLL_DIRECTIONS:
        fail(f"film_scroll.direction は {sorted(VALID_SCROLL_DIRECTIONS)} のいずれかにしてください（{film_scroll_direction}）")
    film_scroll_pace = bounded(float(scroll_cfg.get("pace", 2.5)), 0.05, 60, "film_scroll.pace")
    film_scroll_gap = bounded(int(scroll_cfg.get("gap", 6)), 0, 500, "film_scroll.gap")
    film_scroll_reverse = bool(scroll_cfg.get("reverse", False))
    film_scroll_background = validate_filter_safe(scroll_cfg.get("background", "#000000"), "film_scroll.background")
    film_scroll_angle = bounded(float(scroll_cfg.get("angle", 25)), -180, 180, "film_scroll.angle")
    film_scroll_lanes = bounded(int(scroll_cfg.get("lanes", 2)), 1, 32, "film_scroll.lanes")

    frame_effect_background = validate_filter_safe(
        defaults.get("frame_background", "#000000"), "defaults.frame_background"
    )

    particles = raw.get("particles", "none")
    if particles not in VALID_PARTICLES:
        fail(f"particles は {sorted(VALID_PARTICLES)} のいずれかにしてください（{particles}）")

    light_leak_raw = raw.get("light_leak", False)
    light_leak = bool(light_leak_raw)

    bgm_raw = raw.get("bgm")
    bgm = None
    if bgm_raw:
        bgm_path = resolve_config_path(base_dir, bgm_raw["file"], assets_root, allow_outside_assets, "bgm.file")
        if not bgm_path.exists():
            fail(f"bgm.file が見つかりません: {bgm_path}")
        bgm = BGMConfig(
            path=bgm_path,
            volume=float(bgm_raw.get("volume", 0.8)),
            fade_in=float(bgm_raw.get("fade_in", 2.0)),
            fade_out=float(bgm_raw.get("fade_out", 3.0)),
        )

    slides_raw = raw.get("slides", [])
    if not slides_raw:
        fail("slides が1件も定義されていません")

    slides: list[Slide] = []
    for i, s in enumerate(slides_raw):
        kind = s.get("type", "photo")
        if kind not in ("photo", "video", "title"):
            fail(f"slides[{i}]: type は photo / video / title のいずれかにしてください（{kind}）")

        title_text = None
        title_bg_color = default_title_bg_color
        title_fade_duration = default_title_fade_duration

        if kind == "title":
            if "text" not in s:
                fail(f"slides[{i}]: type=title には text が必要です")
            title_text = str(s["text"])
            title_bg_color = validate_filter_safe(
                s.get("background_color", default_title_bg_color), f"slides[{i}].background_color"
            )
            title_fade_duration = bounded(
                float(s.get("fade_duration", default_title_fade_duration)), 0, 30, f"slides[{i}].fade_duration"
            )

            bg_file = s.get("background")
            path = (
                resolve_config_path(base_dir, bg_file, assets_root, allow_outside_assets, f"slides[{i}].background")
                if bg_file else None
            )
            if path is not None and not path.exists():
                fail(f"slides[{i}]: background画像が見つかりません: {path}")
        else:
            if "file" not in s:
                fail(f"slides[{i}]: file が指定されていません")
            path = resolve_config_path(base_dir, s["file"], assets_root, allow_outside_assets, f"slides[{i}].file")
            if not path.exists():
                fail(f"slides[{i}]: ファイルが見つかりません: {path}")

        effect = s.get("effect", default_effect if kind == "photo" else "none")
        if effect not in VALID_EFFECTS:
            fail(f"slides[{i}]: 不正な effect です: {effect}（有効値: {sorted(VALID_EFFECTS)}）")
        if kind in ("video", "title") and effect != "none":
            print(f"注記: slides[{i}] は type={kind} のため effect は無視されます", file=sys.stderr)
            effect = "none"

        duration = s.get("duration")
        if duration is None and kind == "photo":
            duration = default_photo_duration
        elif duration is None and kind == "title":
            duration = default_title_duration
        if duration is not None:
            duration = bounded(float(duration), 0.01, 3600, f"slides[{i}].duration")

        # タイトルシーンの前後は、指定がなければ自動で fadeblack にして
        # 「場面の切り替わり」を自然に見せる。
        prev_kind = slides[-1].kind if slides else None
        auto_transition = "fadeblack" if (kind == "title" or prev_kind == "title") else default_transition
        transition = s.get("transition", auto_transition) if i > 0 else "none"
        transition_duration = (
            bounded(float(s.get("transition_duration", default_transition_duration)), 0, 30, f"slides[{i}].transition_duration")
            if i > 0 else 0.0
        )

        caption = None
        cap = s.get("caption")
        if cap:
            if "text" not in cap:
                fail(f"slides[{i}]: caption.text が指定されていません")
            caption = Caption(text=str(cap["text"]), position=cap.get("position", "bottom"))
            if caption.position not in ("top", "bottom", "center"):
                fail(f"slides[{i}]: caption.position は top/bottom/center のいずれかにしてください")

        slides.append(Slide(
            kind=kind, path=path, duration=duration, effect=effect,
            transition=transition, transition_duration=transition_duration,
            caption=caption, title_text=title_text, title_bg_color=title_bg_color,
            title_fade_duration=title_fade_duration,
        ))

    return Config(
        output_file=output_file, width=width, height=height, fps=fps,
        font=font, font_file=font_file, slides=slides, bgm=bgm,
        look=look, style=style,
        pile_interval=pile_interval, pile_place_duration=pile_place_duration,
        pile_hold_at_end=pile_hold_at_end, pile_max_coverage=pile_max_coverage,
        pile_start_scale=pile_start_scale, pile_background=pile_background,
        collage_rows=collage_rows, collage_cols=collage_cols,
        collage_interval=collage_interval, collage_place_duration=collage_place_duration,
        collage_hold_page=collage_hold_page, collage_gap=collage_gap,
        collage_transition=collage_transition,
        collage_transition_duration=collage_transition_duration,
        collage_background=collage_background,
        film_scroll_direction=film_scroll_direction, film_scroll_pace=film_scroll_pace,
        film_scroll_gap=film_scroll_gap, film_scroll_reverse=film_scroll_reverse,
        film_scroll_background=film_scroll_background, film_scroll_angle=film_scroll_angle,
        film_scroll_lanes=film_scroll_lanes,
        frame_effect_background=frame_effect_background,
        particles=particles, light_leak=light_leak,
    )


def probe_duration(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        print(f"エラー: {path} の長さを取得できませんでした。\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    return float(result.stdout.strip())


def probe_dimensions(path: Path) -> tuple[int, int]:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=width,height", "-of", "csv=s=x:p=0", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or "x" not in result.stdout:
        print(f"エラー: {path} のサイズを取得できませんでした。\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    w_str, h_str = result.stdout.strip().split("x")
    return int(w_str), int(h_str)


def probe_fps(path: Path) -> float:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "stream=r_frame_rate", "-of", "default=noprint_wrappers=1:nokey=1", str(path)],
        capture_output=True, text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        print(f"エラー: {path} のfpsを取得できませんでした。\n{result.stderr}", file=sys.stderr)
        sys.exit(1)
    num, _, den = result.stdout.strip().partition("/")
    return float(num) / float(den) if den else float(num)


def probe_has_audio(path: Path) -> bool:
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "a",
         "-show_entries", "stream=index", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True,
    )
    return result.returncode == 0 and result.stdout.strip() != ""


def normalize_photo(path: Path, tmp_dir: Path, idx: int) -> Path:
    """写真素材を1フレームPNGに書き出し、入力を単一の安定した形式に揃える。

    HEIC/JPEGは撮影機器によって内部コンテナ形式がまちまちで、-loop 1 を
    受け付けないデマルチプレクサに振り分けられることがあるため、事前に
    正規化しておく（EXIF回転もここで焼き込まれる）。

    HEIC/HEIFは pillow-heif 経由でPILで読み込む。単純にffmpegやsipsで変換すると
    以下の問題がそれぞれ単独で起こることを実測で確認したため、両方を同時に正しく
    処理できる pillow-heif + ImageOps.exif_transpose に統一している。
    - ffmpegの `-frames:v 1`: iPhoneの高解像度HEICが「タイルグリッド」(HEIF grid)
      形式の場合、再合成前の1タイル(512x512角など、写真のごく一部分)だけを
      抜き出してしまう(「なぜか異常にズームされて何が写っているか分からない」)。
    - sips単体: タイルグリッドは正しく再合成するが、EXIFのOrientationタグを
      無視するため、縦向きに撮影した写真が横倒しのまま出力される。
    """
    out_path = tmp_dir / f"photo_src_{idx}.png"
    if path.suffix.lower() in (".heic", ".heif"):
        try:
            img = ImageOps.exif_transpose(Image.open(path))
            img.convert("RGB").save(out_path)
        except Exception as e:
            print(f"エラー: 写真を読み込めませんでした: {path}\n{e}", file=sys.stderr)
            sys.exit(1)
    else:
        result = subprocess.run(
            ["ffmpeg", "-y", "-i", str(path), "-frames:v", "1", str(out_path)],
            capture_output=True, text=True,
        )
        if result.returncode != 0 or not out_path.exists():
            print(f"エラー: 写真を読み込めませんでした: {path}\n{result.stderr}", file=sys.stderr)
            sys.exit(1)

    # 縦向き写真は16:9への収まり込み(中央クロップ)で上下が大きく切れるため、
    # 顔が見切れる可能性がある。生成時に検知できるよう警告を出す。
    w, h = Image.open(out_path).size
    if h > w:
        print(f"警告: 縦向きの写真です。16:9に収める際に上下が切れ顔が見切れる可能性があります: {path}", file=sys.stderr)
    return out_path


def validate_durations(cfg: Config) -> None:
    for i, slide in enumerate(cfg.slides):
        if not slide.duration or slide.duration <= 0:
            fail(f"slides[{i}]: duration を正しく決定できませんでした")
        if i > 0 and slide.transition != "none" and slide.transition_duration >= slide.duration:
            fail(
                f"slides[{i}]: transition_duration({slide.transition_duration}) は "
                f"duration({slide.duration}) より小さくしてください"
            )


def esc(value: str) -> str:
    """ffmpegフィルタのシングルクォート値に埋め込むための最小限のエスケープ。"""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def zoompan_expr(effect: str, frames: int, fps: int = 30, min_hold_seconds: float = 0.0) -> Optional[str]:
    # 終了フレームぴったりまで動き続けると、カットした瞬間に写真を認識できない。
    # 表示時間の終盤(既定1.0秒。duration自体がそれより短い場合はframes-1までに制限)
    # は動きを止めて静止させ、見た目を確保してから次のスライドへ切り替える。
    # 次のスライドへのxfadeがこの停止区間の途中から重なり始めると、まだ動いている
    # 状態が透けて見えてしまうため、次スライドのtransition_durationを完全に
    # 内包できるだけの停止区間を安全マージン(1フレーム)込みで確保する。
    default_hold = round(1.0 * fps)
    required_hold = round(min_hold_seconds * fps) + 1 if min_hold_seconds > 0 else 0
    hold_frames = max(default_hold, required_hold)
    hold_frames = min(hold_frames, max(frames - 1, 0))
    motion_frames = max(frames - hold_frames, 1)
    last = max(motion_frames - 1, 1)
    # 終了フレームに向かって減速するイーズアウト（3乗）。全ての動きに一貫して適用する。
    u = f"min(on/{last},1)"
    ease = f"(1-pow(1-{u},3))"
    # kenburns-inは停止区間の間ズームした状態(z=1+ZOOM)を保持し続けるため、
    # ZOOMを大きくしすぎると停止中ずっと「何の写真か分からない」ほど寄ってしまう。
    # pan-*も停止区間を含め全編この倍率を保持するため、控えめな値にしている。
    ZOOM = 0.08
    if effect == "kenburns-in":
        z, x, y = f"1+{ZOOM}*{ease}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif effect == "kenburns-out":
        z, x, y = f"{1 + ZOOM}-{ZOOM}*{ease}", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)"
    elif effect == "pan-left":
        z, x, y = f"{1 + ZOOM}", f"(iw-iw/zoom)*(1-{ease})", "ih/2-(ih/zoom/2)"
    elif effect == "pan-right":
        z, x, y = f"{1 + ZOOM}", f"(iw-iw/zoom)*{ease}", "ih/2-(ih/zoom/2)"
    elif effect == "pan-up":
        z, x, y = f"{1 + ZOOM}", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*(1-{ease})"
    elif effect == "pan-down":
        z, x, y = f"{1 + ZOOM}", "iw/2-(iw/zoom/2)", f"(ih-ih/zoom)*{ease}"
    else:
        return None
    return f"z='{z}':x='{x}':y='{y}'"


def build_caption_filter(caption: Caption, cfg: Config, tmp_dir: Path, idx: int) -> str:
    caption_file = tmp_dir / f"caption_{idx}.txt"
    caption_file.write_text(caption.text, encoding="utf-8")

    if caption.position == "top":
        y = "60"
    elif caption.position == "center":
        y = "(h-text_h)/2"
    else:
        y = "h-text_h-60"

    font_opt = f"fontfile='{esc(str(cfg.font_file))}'" if cfg.font_file else f"font='{esc(cfg.font)}'"
    return (
        f"drawtext=textfile='{esc(str(caption_file))}':{font_opt}:"
        f"fontcolor=white:fontsize=54:box=1:boxcolor=black@0.55:boxborderw=16:"
        f"x=(w-text_w)/2:y={y}"
    )


def build_title_filter(slide: Slide, cfg: Config, tmp_dir: Path, idx: int) -> str:
    title_file = tmp_dir / f"title_{idx}.txt"
    title_file.write_text(slide.title_text or "", encoding="utf-8")

    font_opt = f"fontfile='{esc(str(cfg.font_file))}'" if cfg.font_file else f"font='{esc(cfg.font)}'"
    return (
        f"drawtext=textfile='{esc(str(title_file))}':{font_opt}:"
        f"fontcolor=white:fontsize=88:box=1:boxcolor=black@0.45:boxborderw=28:"
        f"x=(w-text_w)/2:y=(h-text_h)/2"
    )


def build_focus_in_chain(slide: Slide, idx: int, cfg: Config, tmp_dir: Path, label: str) -> tuple[str, str]:
    """ぼかし→鮮明にピントが合っていく登場効果。

    scaleフィルタの出力サイズを時間で変化させるとフレームサイズが毎フレーム
    変わってしまい、一部の環境でストリームが1フレーム目で止まる問題があるため、
    サイズは常に一定に保ち、ぼかした版とシャープな版を fade(alpha) でクロス
    ディゾルブする方式にしている。
    """
    w, h, fps = cfg.width, cfg.height, cfg.fps
    focus_dur = min(1.2, max(slide.duration * 0.5, 0.3))

    sharp, blur_src, blurred, sharp_fade = (
        f"fi_sharp{idx}", f"fi_blursrc{idx}", f"fi_blurred{idx}", f"fi_sharpfade{idx}",
    )

    parts = [
        f"[{idx}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"fps={fps},format=rgba,split=2[{sharp}][{blur_src}]",
        f"[{blur_src}]boxblur=24:2,format=rgba[{blurred}]",
        f"[{sharp}]fade=t=in:st=0:d={focus_dur:.3f}:alpha=1[{sharp_fade}]",
    ]

    final_label = f"fi_base{idx}" if slide.caption else label
    parts.append(f"[{blurred}][{sharp_fade}]overlay=0:0,format=yuv420p,setsar=1[{final_label}]")
    if slide.caption:
        cap_filter = build_caption_filter(slide.caption, cfg, tmp_dir, idx)
        parts.append(f"[{final_label}]{cap_filter}[{label}]")

    return ";\n".join(parts), label


def build_frame_slide_up_chain(slide: Slide, idx: int, cfg: Config, tmp_dir: Path, label: str) -> tuple[str, str]:
    """白フチ+影のシンプルな額装写真が、画面の下から真っ直ぐスライドインしてくる登場効果。"""
    w, h, fps = cfg.width, cfg.height, cfg.fps
    duration = slide.duration
    border = 18
    frame_w, frame_h = round(w * 0.8), round(h * 0.8)
    slide_dur = min(1.0, max(duration * 0.5, 0.3))

    bg, framed, shadow_src, shadow, withshadow = (
        f"fs_bg{idx}", f"fs_framed{idx}", f"fs_shadowsrc{idx}", f"fs_shadow{idx}", f"fs_withshadow{idx}",
    )

    u_expr = f"clip(t/{slide_dur:.3f},0,1)"
    ease_expr = f"(1-pow(1-{u_expr},2))"
    y_expr = f"{h}*(1-{ease_expr})+(({h}-overlay_h)/2)*{ease_expr}"

    parts = [
        f"color=c={cfg.frame_effect_background}:s={w}x{h}:d={duration:.3f}:r={fps}[{bg}]",
        f"[{idx}:v]scale={frame_w}:{frame_h}:force_original_aspect_ratio=decrease,"
        f"pad=iw+{border * 2}:ih+{border * 2}:{border}:{border}:white,"
        f"scale=trunc(iw/2)*2:trunc(ih/2)*2,format=rgba,split=2[{framed}][{shadow_src}]",
        f"[{shadow_src}]colorchannelmixer="
        f"rr=0:rg=0:rb=0:ra=0:gr=0:gg=0:gb=0:ga=0:br=0:bg=0:bb=0:ba=0:ar=0:ag=0:ab=0:aa=0.45,"
        f"boxblur=12:3[{shadow}]",
        f"[{bg}][{shadow}]overlay=x='(main_w-overlay_w)/2+10':y='{y_expr}+14'[{withshadow}]",
    ]

    final_label = f"fs_photo{idx}" if slide.caption else label
    parts.append(f"[{withshadow}][{framed}]overlay=x='(main_w-overlay_w)/2':y='{y_expr}'[{final_label}]")
    if slide.caption:
        cap_filter = build_caption_filter(slide.caption, cfg, tmp_dir, idx)
        parts.append(f"[{final_label}]{cap_filter}[{label}]")

    return ";\n".join(parts), label


def build_shake_exit_chain(
    slide: Slide, idx: int, cfg: Config, tmp_dir: Path, label: str, direction: str
) -> tuple[str, str]:
    """表示の終盤で左右に素早くぶれたあと、画面外へ滑るように消えていく退場効果。

    大部分の時間は静止表示のままで、durationの終わり側の短い時間帯だけ
    ぶれ→加速しながら画面外へフェードアウト、という2段階の動きにしている。
    次のスライドとは transition: none（ハードカット）で組み合わせるのが
    自然（フェードアウトの直後にクロスフェードが重なると見た目が濁るため）。
    """
    w, h, fps = cfg.width, cfg.height, cfg.fps
    duration = slide.duration
    exit_window = min(0.5, duration * 0.6)
    shake_dur = max(exit_window * 0.4, 0.05)
    fly_dur = max(exit_window - shake_dur, 0.05)
    exit_start = max(duration - exit_window, 0)
    shake_end = exit_start + shake_dur

    amplitude = 18  # ぶれ幅(px)
    freq = 9  # ぶれの速さ(Hz相当)
    sign = -1 if direction == "left" else 1

    bg, photo, faded = f"se_bg{idx}", f"se_photo{idx}", f"se_faded{idx}"

    shake_wave = f"{amplitude}*sin(2*3.14159265*{freq}*(t-{exit_start:.3f}))"
    fly_ease = f"pow(clip((t-{shake_end:.3f})/{fly_dur:.3f},0,1),2)"
    fly_pos = f"{sign}*{w}*{fly_ease}"
    x_expr = f"if(lt(t,{exit_start:.3f}),0,if(lt(t,{shake_end:.3f}),{shake_wave},{fly_pos}))"

    parts = [
        f"color=c={cfg.frame_effect_background}:s={w}x{h}:d={duration:.3f}:r={fps}[{bg}]",
        f"[{idx}:v]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},"
        f"fps={fps},format=rgba[{photo}]",
        f"[{photo}]fade=t=out:st={shake_end:.3f}:d={fly_dur:.3f}:alpha=1[{faded}]",
    ]

    moved = f"se_moved{idx}"
    parts.append(f"[{bg}][{faded}]overlay=x='{x_expr}':y=0[{moved}]")

    # tmixで直近フレームを合成すると、静止部分は無変化のまま、動いている部分にだけ
    # 自然な残像状のモーションブラーが掛かる(ぶれの速さに応じてブラー量も変わる)。
    blur_out = label if not slide.caption else f"se_blur{idx}"
    parts.append(f"[{moved}]tmix=frames=5:weights='1 1 1 1 1'[{blur_out}]")
    if slide.caption:
        cap_filter = build_caption_filter(slide.caption, cfg, tmp_dir, idx)
        parts.append(f"[{blur_out}]{cap_filter}[{label}]")

    return ";\n".join(parts), label


def build_segment_filter(
    slide: Slide, idx: int, cfg: Config, tmp_dir: Path, next_transition_duration: float = 0.0
) -> tuple[str, str]:
    label = f"v{idx}"
    w, h, fps = cfg.width, cfg.height, cfg.fps
    steps: list[str] = []

    if slide.kind == "photo" and slide.effect == "focus-in":
        return build_focus_in_chain(slide, idx, cfg, tmp_dir, label)
    if slide.kind == "photo" and slide.effect == "frame-slide-up":
        return build_frame_slide_up_chain(slide, idx, cfg, tmp_dir, label)
    if slide.kind == "photo" and slide.effect in ("shake-exit-left", "shake-exit-right"):
        direction = "left" if slide.effect == "shake-exit-left" else "right"
        return build_shake_exit_chain(slide, idx, cfg, tmp_dir, label, direction)

    if slide.kind == "photo":
        frames = max(1, round(slide.duration * fps))
        zp = zoompan_expr(slide.effect, frames, fps, next_transition_duration)
        if zp:
            steps.append(f"scale={w * 2}:{h * 2}:force_original_aspect_ratio=increase")
            steps.append(f"crop={w * 2}:{h * 2}")
            steps.append(f"zoompan={zp}:d={frames}:s={w}x{h}:fps={fps}")
            # -loop 1 の静止画はデフォルトフレームレート(25fps)で複製入力されるため、
            # zoompan はその入力フレームごとに d フレームを生成してしまい、
            # 本来の再生時間より大幅に長い(かつ同じ動きを繰り返す)クリップになる。
            # trim で意図したフレーム数に強制的に切り詰めて防ぐ。
            steps.append(f"trim=start_frame=0:end_frame={frames}")
            steps.append("setpts=PTS-STARTPTS")
            steps.append(f"fps={fps}")
        else:
            steps.append(f"scale={w}:{h}:force_original_aspect_ratio=increase")
            steps.append(f"crop={w}:{h}")
            steps.append(f"fps={fps}")
    elif slide.kind == "title":
        # 背景画像がある場合は敷き詰め、ない場合は単色(すでにWxH丁度のcolorソース)
        steps.append(f"scale={w}:{h}:force_original_aspect_ratio=increase")
        steps.append(f"crop={w}:{h}")
        steps.append(f"fps={fps}")
        steps.append(build_title_filter(slide, cfg, tmp_dir, idx))
        steps.append(f"fade=t=in:st=0:d={slide.title_fade_duration}")
    else:
        steps.append(f"scale={w}:{h}:force_original_aspect_ratio=decrease")
        steps.append(f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black")
        steps.append(f"trim=0:{slide.duration}")
        steps.append("setpts=PTS-STARTPTS")
        steps.append(f"fps={fps}")

    steps.append("format=yuv420p")
    steps.append("setsar=1")

    if slide.caption:
        steps.append(build_caption_filter(slide.caption, cfg, tmp_dir, idx))

    chain = f"[{idx}:v]" + ",".join(steps) + f"[{label}]"
    return chain, label


def build_filter_complex(
    cfg: Config, tmp_dir: Path
) -> tuple[str, float, bool, list[tuple[Path, float]]]:
    w, h, fps = cfg.width, cfg.height, cfg.fps
    extra_inputs: list[tuple[Path, float]] = []

    filters: list[str] = []
    labels: list[str] = []
    for i, slide in enumerate(cfg.slides):
        next_transition_duration = 0.0
        if i + 1 < len(cfg.slides):
            next_slide = cfg.slides[i + 1]
            if next_slide.transition != "none" and next_slide.transition_duration > 0:
                next_transition_duration = next_slide.transition_duration
        chain, label = build_segment_filter(slide, i, cfg, tmp_dir, next_transition_duration)
        filters.append(chain)
        labels.append(label)

    acc_label = labels[0]
    acc_duration = cfg.slides[0].duration
    # ハードカットが連続する区間を溜めておき、1回の多入力concatにまとめて処理する。
    pending_labels = [labels[0]]

    def flush_pending() -> None:
        # 1組ずつ連鎖concatする(n=2を何度も繋ぐ)と、後段にxfadeが続いた場合に
        # フレームがサイレントに欠落することがある(検証済みの既知のffmpeg不具合)。
        # 常に1回の多入力concatにまとめてから次段(xfade等)へ渡すことでこれを避ける。
        nonlocal acc_label
        if len(pending_labels) > 1:
            flush_label = f"accgroup{len(filters)}"
            inputs = "".join(f"[{lbl}]" for lbl in pending_labels)
            filters.append(
                f"{inputs}concat=n={len(pending_labels)}:v=1:a=0,settb=1/{fps}[{flush_label}]"
            )
            acc_label = flush_label
        else:
            acc_label = pending_labels[0]
        pending_labels.clear()

    for i in range(1, len(cfg.slides)):
        slide = cfg.slides[i]
        next_label = labels[i]
        out_label = f"acc{i}"
        if slide.transition == "none" or slide.transition_duration <= 0:
            pending_labels.append(next_label)
            acc_duration = acc_duration + slide.duration
            continue
        elif slide.transition == "filmstrip":
            if not FILM_STRIP_PATH.exists():
                fail(f"フィルムストリップ画像が見つかりません: {FILM_STRIP_PATH}")
            flush_pending()
            offset = max(acc_duration - slide.transition_duration, 0)
            push_label = f"acc{i}push"
            filters.append(
                f"[{acc_label}][{next_label}]xfade=transition=fade:"
                f"duration={slide.transition_duration}:offset={offset:.3f},fps={fps}[{push_label}]"
            )
            extra_idx = len(cfg.slides) + len(extra_inputs)
            extra_inputs.append((FILM_STRIP_PATH, slide.transition_duration))
            fs_label = f"fs{i}"
            filters.append(f"[{extra_idx}:v]scale={w}:{h},format=rgba[{fs_label}]")
            filters.append(
                f"[{push_label}][{fs_label}]overlay=(main_w-overlay_w)/2:(main_h-overlay_h)/2:"
                f"enable='between(t,{offset:.3f},{offset + slide.transition_duration:.3f})'[{out_label}]"
            )
            acc_duration = acc_duration + slide.duration - slide.transition_duration
            pending_labels = [out_label]
        else:
            flush_pending()
            offset = max(acc_duration - slide.transition_duration, 0)
            filters.append(
                f"[{acc_label}][{next_label}]xfade=transition={slide.transition}:"
                f"duration={slide.transition_duration}:offset={offset:.3f},fps={fps}[{out_label}]"
            )
            acc_duration = acc_duration + slide.duration - slide.transition_duration
            pending_labels = [out_label]

    flush_pending()

    if cfg.look == "vintage":
        filters.append(f"[{acc_label}]{VINTAGE_LOOK_FILTER}[vout]")
    else:
        filters.append(f"[{acc_label}]null[vout]")

    has_audio = cfg.bgm is not None
    if cfg.bgm:
        bgm_idx = len(cfg.slides) + len(extra_inputs)
        fade_out_start = max(acc_duration - cfg.bgm.fade_out, 0)
        filters.append(
            f"[{bgm_idx}:a]atrim=0:{acc_duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={cfg.bgm.volume},"
            f"afade=t=in:st=0:d={cfg.bgm.fade_in},"
            f"afade=t=out:st={fade_out_start:.3f}:d={cfg.bgm.fade_out}"
            f"[aout]"
        )

    return ";\n".join(filters), acc_duration, has_audio, extra_inputs


def build_photo_pile_filter_complex(
    cfg: Config, tmp_dir: Path
) -> tuple[str, float, bool, list[tuple[Path, float]]]:
    """印刷写真を1枚ずつ置いていくようなパイル演出のフィルタグラフを組み立てる。

    各写真は白フチ付きでランダムな角度に固定回転し、上からドロップして
    静止する。以前に置かれた写真はそのまま画面に残り続け、パイルが蓄積する。
    """
    w, h, fps = cfg.width, cfg.height, cfg.fps
    n = len(cfg.slides)
    rnd = random.Random(42)

    for i, slide in enumerate(cfg.slides):
        if slide.kind != "photo":
            fail(f"slides[{i}]: style=photo_pile では type=video のスライドは使用できません")

    step = cfg.pile_interval
    place = cfg.pile_place_duration
    total_duration = (n - 1) * step + place + cfg.pile_hold_at_end if n > 1 else place + cfg.pile_hold_at_end

    max_w = max(int(w * cfg.pile_max_coverage) // 2 * 2, 2)
    max_h = max(int(h * cfg.pile_max_coverage) // 2 * 2, 2)
    border = 14
    start_scale = max(cfg.pile_start_scale, 1.0)
    drop_distance = h  # 画面の高さ以上上から落とせば確実に枠外からスタートする

    safe_x0 = int(w * 0.08)
    safe_x1 = max(int(w * 0.92) - max_w, safe_x0)
    safe_y0 = int(h * 0.08)
    safe_y1 = max(int(h * 0.92) - max_h, safe_y0)

    filters: list[str] = [f"color=c={cfg.pile_background}:s={w}x{h}:d={total_duration:.3f}:r={fps}[bg0]"]

    acc_label = "bg0"
    for i, slide in enumerate(cfg.slides):
        t0 = i * step
        angle = rnd.uniform(-18, 18) * 3.141592653589793 / 180
        tx = rnd.randint(safe_x0, safe_x1)
        ty = rnd.randint(safe_y0, safe_y1)

        # 写真ごとに実際の縦横比で縮小後サイズが変わるため、実寸をffprobeで取得してから
        # 回転後(等倍時)のサイズを計算する。中心を (cx, cy) に固定し、そこを基準に
        # 拡大縮小・落下させる。
        photo_w, photo_h = probe_dimensions(slide.path)
        photo_scale = min(max_w / photo_w, max_h / photo_h)
        scaled_w = max(round(photo_w * photo_scale), 2)
        scaled_h = max(round(photo_h * photo_scale), 2)
        framed_w = ((scaled_w + border * 2) // 2) * 2
        framed_h = ((scaled_h + border * 2) // 2) * 2
        base_w = abs(framed_w * math.cos(angle)) + abs(framed_h * math.sin(angle))
        base_h = abs(framed_w * math.sin(angle)) + abs(framed_h * math.cos(angle))
        cx, cy = tx + base_w / 2, ty + base_h / 2

        framed_label = f"pf{i}"
        filters.append(
            f"[{i}:v]scale={max_w}:{max_h}:force_original_aspect_ratio=decrease,"
            f"pad=iw+{border * 2}:ih+{border * 2}:{border}:{border}:white,"
            f"scale=trunc(iw/2)*2:trunc(ih/2)*2,format=rgba[{framed_label}]"
        )

        rot_label = f"pr{i}"
        filters.append(
            f"[{framed_label}]rotate={angle:.5f}:ow=rotw({angle:.5f}):oh=roth({angle:.5f}):c=none[{rot_label}]"
        )

        # イージング(ease-out): 停止位置に近づくほどゆっくりになる
        u_expr = f"clip((t-{t0:.3f})/{place:.3f},0,1)"
        ease_expr = f"(1-pow(1-{u_expr},2))"
        scale_expr = f"({start_scale:.3f}-{start_scale - 1:.3f}*{ease_expr})"

        # w/h は入力(rotate後)の実寸 iw/ih を基準に同じ倍率で拡大縮小するため、
        # 写真の縦横比が実際と食い違っていても引き伸ばされない。
        scaled_label = f"ps{i}"
        filters.append(
            f"[{rot_label}]scale=w='iw*{scale_expr}':h='ih*{scale_expr}':"
            f"eval=frame[{scaled_label}]"
        )

        out_label = f"pile{i}"
        x_expr = f"{cx:.2f}-overlay_w/2"
        y_expr = f"{cy:.2f}-{drop_distance}*(1-{ease_expr})-overlay_h/2"
        filters.append(
            f"[{acc_label}][{scaled_label}]overlay=x='{x_expr}':y='{y_expr}':"
            f"enable='gte(t,{t0:.3f})'[{out_label}]"
        )
        acc_label = out_label

    if cfg.look == "vintage":
        filters.append(f"[{acc_label}]{VINTAGE_LOOK_FILTER}[vout]")
    else:
        filters.append(f"[{acc_label}]null[vout]")

    has_audio = cfg.bgm is not None
    if cfg.bgm:
        fade_out_start = max(total_duration - cfg.bgm.fade_out, 0)
        filters.append(
            f"[{n}:a]atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={cfg.bgm.volume},"
            f"afade=t=in:st=0:d={cfg.bgm.fade_in},"
            f"afade=t=out:st={fade_out_start:.3f}:d={cfg.bgm.fade_out}"
            f"[aout]"
        )

    return ";\n".join(filters), total_duration, has_audio, []


def build_collage_filter_complex(
    cfg: Config, tmp_dir: Path
) -> tuple[str, float, bool, list[tuple[Path, float]]]:
    """複数枚を同時に並べるグリッド(コラージュ)演出のフィルタグラフを組み立てる。

    写真を rows x cols のページに分割し、ページ内で1枚ずつフェードインして
    グリッドが埋まっていく。ページが揃ったら次のページへクロスフェードする。
    """
    w, h, fps = cfg.width, cfg.height, cfg.fps
    n = len(cfg.slides)

    for i, slide in enumerate(cfg.slides):
        if slide.kind == "video":
            fail(f"slides[{i}]: style=collage では type=video のスライドは使用できません")

    rows, cols = cfg.collage_rows, cfg.collage_cols
    cells_per_page = rows * cols
    if cells_per_page < 1:
        fail("collage.rows / collage.cols は1以上にしてください")

    gap = cfg.collage_gap
    cell_w = max(int((w - gap * (cols + 1)) / cols) // 2 * 2, 2)
    cell_h = max(int((h - gap * (rows + 1)) / rows) // 2 * 2, 2)

    pages = [list(range(p, min(p + cells_per_page, n))) for p in range(0, n, cells_per_page)]

    interval = cfg.collage_interval
    place = cfg.collage_place_duration

    filters: list[str] = []
    page_labels: list[str] = []
    page_durations: list[float] = []

    for page_idx, indices in enumerate(pages):
        page_duration = (len(indices) - 1) * interval + place + cfg.collage_hold_page

        bg_label = f"cbg{page_idx}"
        filters.append(
            f"color=c={cfg.collage_background}:s={w}x{h}:d={page_duration:.3f}:r={fps}[{bg_label}]"
        )

        acc_label = bg_label
        for pos, slide_idx in enumerate(indices):
            row, col = divmod(pos, cols)
            cx = gap + col * (cell_w + gap)
            cy = gap + row * (cell_h + gap)
            t0 = pos * interval

            cfg.slides[slide_idx].duration = page_duration

            tile_label = f"ctile{page_idx}_{pos}"
            filters.append(
                f"[{slide_idx}:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=increase,"
                f"crop={cell_w}:{cell_h},format=rgba,"
                f"fade=t=in:st={t0:.3f}:d={place:.3f}:alpha=1[{tile_label}]"
            )

            out_label = f"cpage{page_idx}_{pos}"
            filters.append(
                f"[{acc_label}][{tile_label}]overlay=x={cx}:y={cy}:enable='gte(t,{t0:.3f})'[{out_label}]"
            )
            acc_label = out_label

        page_labels.append(acc_label)
        page_durations.append(page_duration)

    acc_label = page_labels[0]
    acc_duration = page_durations[0]
    for i in range(1, len(page_labels)):
        t = cfg.collage_transition_duration
        offset = max(acc_duration - t, 0)
        out_label = f"cacc{i}"
        filters.append(
            f"[{acc_label}][{page_labels[i]}]xfade=transition={cfg.collage_transition}:"
            f"duration={t}:offset={offset:.3f}[{out_label}]"
        )
        acc_duration = acc_duration + page_durations[i] - t
        acc_label = out_label

    if cfg.look == "vintage":
        filters.append(f"[{acc_label}]{VINTAGE_LOOK_FILTER}[vout]")
    else:
        filters.append(f"[{acc_label}]null[vout]")

    has_audio = cfg.bgm is not None
    if cfg.bgm:
        fade_out_start = max(acc_duration - cfg.bgm.fade_out, 0)
        filters.append(
            f"[{n}:a]atrim=0:{acc_duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={cfg.bgm.volume},"
            f"afade=t=in:st=0:d={cfg.bgm.fade_in},"
            f"afade=t=out:st={fade_out_start:.3f}:d={cfg.bgm.fade_out}"
            f"[aout]"
        )

    return ";\n".join(filters), acc_duration, has_audio, []


def render_film_scroll_lane_canvases(
    cfg: Config, tmp_dir: Path, vertical: bool
) -> tuple[list[Path], int, int, int, int]:
    """写真をフィルムのコマにはめ込んだ帯を、レーンごとに個別の画像として作る。

    レーンを個別の画像にしておくことで、後段でレーンごとに逆方向へ
    (互い違いに)スクロールさせられる。レーン方向(縦スクロールなら列、
    横・斜めスクロールなら行。既定2本)は画面を隙間なく分割するサイズにし、
    もう一方の辺はフィルム枠の自然な縦横比(1280:720)から決める。
    """
    n = len(cfg.slides)
    gap = cfg.film_scroll_gap
    lanes = max(cfg.film_scroll_lanes, 1)
    bg = cfg.film_scroll_background

    # 縦スクロールはスプロケットが左右(送り方向と平行)に来る専用素材を使う
    # (回転+非等倍拡縮だと穴が楕円に潰れるため、最初から横長で描いたものを使う)。
    border_path = FILM_STRIP_VERTICAL_PATH if vertical else FILM_STRIP_PATH

    if vertical:
        cell_w = max(round((cfg.width - gap * (lanes - 1)) / lanes), 2)
        cell_h = max(round(cell_w * 720 / 1280), 2)
    else:
        cell_h = max(round((cfg.height - gap * (lanes - 1)) / lanes), 2)
        cell_w = max(round(cell_h * 1280 / 720), 2)

    groups = -(-n // lanes)  # ceil(n / lanes)
    step = (cell_h + gap) if vertical else (cell_w + gap)
    lane_len = max(groups * step - gap, 2)

    canvas_w = (cell_w if vertical else lane_len)
    canvas_h = (lane_len if vertical else cell_h)
    canvas_w = max(canvas_w, 2) // 2 * 2
    canvas_h = max(canvas_h, 2) // 2 * 2

    lane_indices: list[list[int]] = [[] for _ in range(lanes)]
    for i in range(n):
        lane_indices[i % lanes].append(i)

    canvas_paths: list[Path] = []
    for lane in range(lanes):
        indices = lane_indices[lane]
        m = len(indices)

        cmd = ["ffmpeg", "-y"]
        for idx in indices:
            cmd += ["-i", str(cfg.slides[idx].path)]
        for _ in indices:
            cmd += ["-i", str(border_path)]

        filters = [f"color=c={bg}:s={canvas_w}x{canvas_h}:d=1[canvas0]"]
        acc = "canvas0"
        for k, idx in enumerate(indices):
            group = idx // lanes
            pos = group * step
            px, py = (0, pos) if vertical else (pos, 0)
            border_in = m + k
            photo_label, border_label, framed_label = f"fp{k}", f"fb{k}", f"ff{k}"
            filters.append(
                f"[{k}:v]scale={cell_w}:{cell_h}:force_original_aspect_ratio=decrease,"
                f"pad={cell_w}:{cell_h}:(ow-iw)/2:(oh-ih)/2:color=black,format=rgba[{photo_label}]"
            )
            filters.append(f"[{border_in}:v]scale={cell_w}:{cell_h},format=rgba[{border_label}]")
            filters.append(f"[{photo_label}][{border_label}]overlay=0:0[{framed_label}]")
            out_label = f"canvas{k + 1}"
            filters.append(f"[{acc}][{framed_label}]overlay=x={px}:y={py}[{out_label}]")
            acc = out_label

        filters.append(f"[{acc}]format=rgb24[final]")

        canvas_path = tmp_dir / f"film_scroll_lane{lane}.png"
        filter_script = tmp_dir / f"film_scroll_lane{lane}_filter.txt"
        filter_script.write_text(";\n".join(filters), encoding="utf-8")

        cmd += ["-filter_complex_script", str(filter_script), "-map", "[final]", "-frames:v", "1", str(canvas_path)]
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0 or not canvas_path.exists():
            print(f"エラー: フィルムスクロール用の画像を合成できませんでした。\n{result.stderr}", file=sys.stderr)
            sys.exit(1)
        canvas_paths.append(canvas_path)

    return canvas_paths, canvas_w, canvas_h, cell_w, cell_h


def build_film_scroll_filter_complex(cfg: Config, tmp_dir: Path) -> tuple[str, float, bool, list[Path]]:
    for i, slide in enumerate(cfg.slides):
        if slide.kind == "video":
            fail(f"slides[{i}]: style=film_scroll では type=video のスライドは使用できません")

    w, h, fps = cfg.width, cfg.height, cfg.fps
    direction = cfg.film_scroll_direction
    vertical = direction == "vertical"
    gap = cfg.film_scroll_gap
    pace = max(cfg.film_scroll_pace, 0.1)
    lanes = max(cfg.film_scroll_lanes, 1)
    bg = cfg.film_scroll_background

    # diagonalは横スクロールの帯を後段で一定角度だけ傾けて実現する
    canvas_paths, canvas_w, canvas_h, cell_w, cell_h = render_film_scroll_lane_canvases(cfg, tmp_dir, vertical)

    step = (cell_h + gap) if vertical else (cell_w + gap)
    speed = step / pace  # 1コマ分の幅(高さ)が pace 秒で流れる速度
    lane_out_w = cell_w if vertical else w
    lane_out_h = h if vertical else cell_h
    distance = max(canvas_h - lane_out_h, 0) if vertical else max(canvas_w - lane_out_w, 0)
    total_duration = max(distance / speed, pace) if speed > 0 else pace

    # zoompanはzoomが実質的に変化しないと内部キャッシュにより x/y の更新を
    # 反映しないクセがあるため、時間ベースの式をそのまま使える crop で実装する。
    # レーンごとに移動方向を互い違いにする。
    filters: list[str] = []
    lane_labels = []
    for lane in range(lanes):
        lane_reverse = cfg.film_scroll_reverse != (lane % 2 == 1)
        max_x = max(canvas_w - lane_out_w, 0) if not vertical else 0
        max_y = max(canvas_h - lane_out_h, 0) if vertical else 0
        if lane_reverse:
            x_expr = f"{max_x}-{max_x}*min(t,{total_duration:.3f})/{total_duration:.3f}"
            y_expr = f"{max_y}-{max_y}*min(t,{total_duration:.3f})/{total_duration:.3f}"
        else:
            x_expr = f"{max_x}*min(t,{total_duration:.3f})/{total_duration:.3f}"
            y_expr = f"{max_y}*min(t,{total_duration:.3f})/{total_duration:.3f}"
        label = f"lane{lane}"
        filters.append(
            f"[{lane}:v]crop={lane_out_w}:{lane_out_h}:x='{x_expr}':y='{y_expr}',format=yuv420p[{label}]"
        )
        lane_labels.append(label)

    if lanes == 1:
        stacked_label = lane_labels[0]
    else:
        stack_filter = "hstack" if vertical else "vstack"
        gap_labels: list[str] = []
        if gap > 0:
            if vertical:
                filters.append(f"color=c={bg}:s={gap}x{h}:d={total_duration:.3f}:r={fps}[gapsrc]")
            else:
                filters.append(f"color=c={bg}:s={w}x{gap}:d={total_duration:.3f}:r={fps}[gapsrc]")
            n_gaps = lanes - 1
            if n_gaps > 1:
                filters.append(f"[gapsrc]split={n_gaps}" + "".join(f"[gap{i}]" for i in range(n_gaps)))
            else:
                filters.append("[gapsrc]null[gap0]")
            gap_labels = [f"gap{i}" for i in range(n_gaps)]

        parts = []
        for idx, label in enumerate(lane_labels):
            parts.append(f"[{label}]")
            if idx < len(gap_labels):
                parts.append(f"[{gap_labels[idx]}]")
        stacked_label = "stacked"
        filters.append("".join(parts) + f"{stack_filter}=inputs={len(parts)}[{stacked_label}]")

    post_steps = []
    if direction == "diagonal":
        angle = cfg.film_scroll_angle * 3.141592653589793 / 180
        post_steps.append(f"rotate={angle:.5f}:ow=rotw({angle:.5f}):oh=roth({angle:.5f}):c={bg}")
        post_steps.append(f"crop={w}:{h}")

    if post_steps:
        filters.append(f"[{stacked_label}]" + ",".join(post_steps) + "[vraw]")
        final_label = "vraw"
    else:
        final_label = stacked_label

    if cfg.look == "vintage":
        filters.append(f"[{final_label}]{VINTAGE_LOOK_FILTER}[vout]")
    else:
        filters.append(f"[{final_label}]null[vout]")

    has_audio = cfg.bgm is not None
    if cfg.bgm:
        fade_out_start = max(total_duration - cfg.bgm.fade_out, 0)
        filters.append(
            f"[{lanes}:a]atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
            f"volume={cfg.bgm.volume},"
            f"afade=t=in:st=0:d={cfg.bgm.fade_in},"
            f"afade=t=out:st={fade_out_start:.3f}:d={cfg.bgm.fade_out}"
            f"[aout]"
        )

    return ";\n".join(filters), total_duration, has_audio, canvas_paths


def build_film_scroll_ffmpeg_command(
    cfg: Config, filter_script: Path, total_duration: float, has_audio: bool, canvas_paths: list[Path],
) -> list[str]:
    cmd = ["ffmpeg", "-y"]
    for canvas_path in canvas_paths:
        cmd += ["-loop", "1", "-t", f"{total_duration:.3f}", "-i", str(canvas_path)]
    if cfg.bgm:
        cmd += ["-stream_loop", "-1", "-i", str(cfg.bgm.path)]

    cmd += ["-filter_complex_script", str(filter_script)]
    cmd += ["-map", "[vout]"]
    if has_audio:
        cmd += ["-map", "[aout]"]
    else:
        cmd += ["-an"]

    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(cfg.fps),
        "-preset", "medium", "-crf", "18",
        "-profile:v", "high", "-level:v", "4.1", "-bf", "2",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", "-t", f"{total_duration:.3f}", str(cfg.output_file)]
    return cmd


def build_ffmpeg_command(
    cfg: Config, filter_script: Path, total_duration: float, has_audio: bool,
    extra_inputs: list[tuple[Path, float]],
) -> list[str]:
    cmd = ["ffmpeg", "-y"]
    for slide in cfg.slides:
        if slide.kind == "photo":
            cmd += ["-loop", "1", "-t", f"{slide.duration}", "-i", str(slide.path)]
        elif slide.kind == "title":
            if slide.path is not None:
                cmd += ["-loop", "1", "-t", f"{slide.duration}", "-i", str(slide.path)]
            else:
                cmd += [
                    "-f", "lavfi", "-i",
                    f"color=c={slide.title_bg_color}:s={cfg.width}x{cfg.height}:d={slide.duration}",
                ]
        else:
            cmd += ["-i", str(slide.path)]
    for path, duration in extra_inputs:
        cmd += ["-loop", "1", "-t", f"{duration}", "-i", str(path)]
    if cfg.bgm:
        cmd += ["-stream_loop", "-1", "-i", str(cfg.bgm.path)]

    cmd += ["-filter_complex_script", str(filter_script)]
    cmd += ["-map", "[vout]"]
    if has_audio:
        cmd += ["-map", "[aout]"]
    else:
        cmd += ["-an"]

    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(cfg.fps),
        "-preset", "medium", "-crf", "18",
        "-profile:v", "high", "-level:v", "4.1", "-bf", "2",
    ]
    if has_audio:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += ["-movflags", "+faststart", "-t", f"{total_duration:.3f}", str(cfg.output_file)]
    return cmd


def build_particle_overlay_filter(
    particles: str, w: int, h: int
) -> tuple[str, list[Path]]:
    """桜吹雪・キラキラなど、画面を漂うパーティクルの重ね合わせフィルタを作る。

    各パーティクルは mod(t, lifetime) で無限ループしながら、上から下へ
    ふわふわ左右に揺れつつ回転して流れていく。ベース動画([0:v])の上に
    1個ずつ overlay で重ねていく。
    """
    profile = PARTICLE_PROFILES[particles]
    rnd = random.Random(123)
    n = profile["count"]
    size_lo, size_hi = profile["size_range"]
    fall_lo, fall_hi = profile["fall_range"]
    sway_lo, sway_hi = profile["sway_range"]
    rot_lo, rot_hi = profile["rotate_range"]

    filters = ["[0:v]null[pbase0]"]
    acc = "pbase0"
    for i in range(n):
        size = rnd.uniform(size_lo, size_hi)
        lifetime = rnd.uniform(fall_lo, fall_hi)
        t0 = rnd.uniform(0, lifetime)
        spawn_x = rnd.uniform(0, w)
        sway_amp = rnd.uniform(sway_lo, sway_hi)
        sway_period = rnd.uniform(3.0, 6.0)
        sway_phase = rnd.uniform(0, 6.28318)
        rot_speed = rnd.uniform(rot_lo, rot_hi) * (1 if rnd.random() < 0.5 else -1)
        rot_box = round(size * 1.5)

        local_t = f"mod(t+{t0:.3f},{lifetime:.3f})"
        y_expr = f"-{size:.1f}+{local_t}*({h}+{2 * size:.1f})/{lifetime:.3f}"
        x_expr = (
            f"{spawn_x:.1f}+{sway_amp:.1f}*sin(2*3.14159265*{local_t}/"
            f"{sway_period:.3f}+{sway_phase:.3f})"
        )
        angle_expr = f"{local_t}*{rot_speed:.4f}"

        scaled, rotated, out = f"pt_s{i}", f"pt_r{i}", f"pt_a{i}"
        filters.append(f"[{i + 1}:v]scale={size:.0f}:{size:.0f},format=rgba[{scaled}]")
        filters.append(f"[{scaled}]rotate='{angle_expr}':c=none:ow={rot_box}:oh={rot_box}[{rotated}]")
        filters.append(f"[{acc}][{rotated}]overlay=x='{x_expr}':y='{y_expr}'[{out}]")
        acc = out

    filters.append(f"[{acc}]null[postout]")
    return ";\n".join(filters), [profile["asset"]] * n


def build_light_leak_filter(w: int, h: int, total_duration: float) -> str:
    """柔らかい光が画面を斜めに横切っていくライトリーク風の後処理フィルタ。

    帯の明るさをアルファ値のフェードで表現し、通常のoverlay(アルファ合成)で
    ベース動画に重ねる(blendフィルタのall_opacityは想定と異なる挙動をしたため
    不採用)。
    """
    band_w = round(max(w, h) * 1.6)
    band_h = round(h * 2.2)
    sweep_dur = min(max(total_duration * 0.6, 4.0), 10.0)
    x_start, x_end = -band_w, w + band_w
    half = band_w / 2
    beam_half = w * 0.15  # 明るく見える部分の半幅(帯全体band_wよりずっと狭い、本物の光の筋にする)
    max_alpha = 150  # 255段階での最大不透明度(控えめな強さにする)

    falloff = f"max(0,1-abs((X-{half:.1f})/{beam_half:.1f}))"
    return (
        f"color=c=black:s={band_w}x{band_h}:d={total_duration:.3f}[lg_canvas];"
        f"[lg_canvas]format=rgba,geq="
        f"r='255':g='235':b='190':a='{max_alpha}*{falloff}'[lg_band];"
        f"[lg_band]rotate=-0.45:c=none[lg_tilted];"
        f"[0:v][lg_tilted]overlay="
        f"x='{x_start}+mod(t,{sweep_dur:.3f})/{sweep_dur:.3f}*{x_end - x_start}':"
        f"y='{-round(h * 0.6)}'[postout]"
    )


def run_post_effects(cfg: Config, base_video: Path, total_duration: float, tmp_dir: Path) -> None:
    """粒子演出・ライトリークなど、完成した動画にかける追加の後処理パスを実行する。"""
    w, h, fps = cfg.width, cfg.height, cfg.fps

    cmd = ["ffmpeg", "-y", "-i", str(base_video)]
    extra_paths: list[Path] = []
    stage_label = "0:v"

    filter_parts: list[str] = []
    if cfg.particles != "none":
        particle_filters, extra_paths = build_particle_overlay_filter(cfg.particles, w, h)
        filter_parts.append(particle_filters)
        stage_label = "postout"
    if cfg.light_leak:
        # light_leakがパーティクルの後段になるよう、直前の出力を[0:v]の代わりに使う
        chain = build_light_leak_filter(w, h, total_duration)
        if stage_label != "0:v":
            chain = chain.replace("[0:v]", f"[{stage_label}]")
        filter_parts.append(chain)
        stage_label = "postout"

    for path in extra_paths:
        cmd += ["-loop", "1", "-t", f"{total_duration:.3f}", "-i", str(path)]

    filter_script = tmp_dir / "post_effects_filter.txt"
    filter_script.write_text(";\n".join(filter_parts), encoding="utf-8")

    cmd += ["-filter_complex_script", str(filter_script)]
    cmd += ["-map", f"[{stage_label}]", "-map", "0:a?"]
    cmd += [
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(fps),
        "-preset", "medium", "-crf", "18",
        "-profile:v", "high", "-level:v", "4.1", "-bf", "2", "-c:a", "copy",
        "-movflags", "+faststart", "-t", f"{total_duration:.3f}", str(cfg.output_file),
    ]

    result = subprocess.run(cmd)
    if result.returncode != 0:
        print("エラー: 後処理(パーティクル/ライトリーク)の合成に失敗しました。", file=sys.stderr)
        sys.exit(result.returncode)


def concat_list_escape(path: Path) -> str:
    """ffmpeg concatデマルサのlistファイル用にパスをエスケープする。"""
    return "'" + str(path).replace("'", "'\\''") + "'"


def run_concat_job(config_path: Path, raw: dict, allow_outside_assets: bool, dry_run: bool) -> None:
    """既にbuild_video.pyで生成済みの動画ファイル(パーツ)を1本に結合するモード。

    5分を超えるような長い動画を1回のslides.yamlで作ろうとすると、写真の指定枚数が
    膨大になり差し替え時の見通しが悪くなる。そこで、パーツごとに通常通り
    (`slides:`を使って)動画を作っておき、それらをこのモードで結合する2段階構成を
    取れるようにする。既定ではハードカットのみ(ffmpegのconcatデマルサで
    再エンコードなしにストリームコピー結合)。パーツ間クロスフェードは非対応。
    """
    base_dir = config_path.resolve().parent.parent
    output_root = (base_dir / "output").resolve()

    out = raw.get("output")
    if not out or not out.get("file"):
        fail("output.file が指定されていません")
    output_file = resolve_config_path(base_dir, out["file"], output_root, allow_outside_assets, "output.file")

    parts_raw = raw.get("concat", [])
    if not parts_raw:
        fail("concat が1件も指定されていません")

    parts: list[Path] = []
    for i, p in enumerate(parts_raw):
        file_val = p if isinstance(p, str) else p.get("file")
        if not file_val:
            fail(f"concat[{i}]: file が指定されていません")
        path = resolve_config_path(base_dir, file_val, output_root, allow_outside_assets, f"concat[{i}].file")
        if not path.exists():
            fail(f"concat[{i}]: ファイルが見つかりません: {path}")
        parts.append(path)

    if len(parts) < 2:
        fail("concat には2件以上のファイルを指定してください")

    ref_w, ref_h = probe_dimensions(parts[0])
    ref_fps = probe_fps(parts[0])
    ref_has_audio = probe_has_audio(parts[0])
    for path in parts[1:]:
        w, h = probe_dimensions(path)
        fps = probe_fps(path)
        has_audio = probe_has_audio(path)
        if (w, h) != (ref_w, ref_h):
            fail(
                f"concatする動画の解像度が揃っていません: {parts[0]}({ref_w}x{ref_h}) "
                f"vs {path}({w}x{h})"
            )
        if abs(fps - ref_fps) > 0.01:
            fail(f"concatする動画のfpsが揃っていません: {parts[0]}({ref_fps}) vs {path}({fps})")
        if has_audio != ref_has_audio:
            fail(
                f"concatする動画の音声トラックの有無が揃っていません: "
                f"{parts[0]}({'あり' if ref_has_audio else 'なし'}) vs {path}({'あり' if has_audio else 'なし'})"
            )

    output_file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="slideshow_concat_") as tmp:
        list_file = Path(tmp) / "concat_list.txt"
        list_file.write_text(
            "\n".join(f"file {concat_list_escape(p)}" for p in parts) + "\n", encoding="utf-8"
        )

        cmd = [
            "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file),
            "-c", "copy", "-movflags", "+faststart", str(output_file),
        ]

        print(f"結合するパーツ数: {len(parts)}")
        print(f"出力先: {output_file}")

        if dry_run:
            print("\n--- concat list ---")
            print(list_file.read_text(encoding="utf-8"))
            print("--- ffmpeg command ---")
            print(" ".join(cmd))
            return

        result = subprocess.run(cmd)
        if result.returncode != 0:
            print("エラー: ffmpeg の実行に失敗しました。", file=sys.stderr)
            sys.exit(result.returncode)

        print(f"完成しました: {output_file}")


def main() -> None:
    parser = argparse.ArgumentParser(description="YAML設定からスライドショー動画を生成します")
    parser.add_argument("--config", default="config/slides.yaml", type=Path)
    parser.add_argument("--dry-run", action="store_true", help="ffmpegコマンドを表示するだけで実行しない")
    parser.add_argument(
        "--allow-outside-assets", action="store_true",
        help="写真/動画/BGM/フォント/出力先が assets/・output/ の外を指すパスでも許可する"
             "（既定では拒否。第三者から受け取ったconfigを実行する場合は指定しないこと）",
    )
    args = parser.parse_args()

    if shutil.which("ffmpeg") is None:
        print(
            "エラー: ffmpeg が見つかりません。"
            "OSのパッケージマネージャでインストールするか、Docker実行（README参照）を利用してください。",
            file=sys.stderr,
        )
        sys.exit(1)

    config_path = args.config.resolve()
    if not config_path.exists():
        fail(f"設定ファイルが見つかりません: {config_path}")

    with config_path.open("r", encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}
    if "concat" in raw:
        run_concat_job(config_path, raw, args.allow_outside_assets, args.dry_run)
        return

    cfg = load_config(config_path, allow_outside_assets=args.allow_outside_assets)

    if cfg.style == "photo_pile":
        for slide in cfg.slides:
            if slide.kind != "photo":
                fail("style=photo_pile では type=photo 以外のスライドは使用できません")
    elif cfg.style == "collage":
        for slide in cfg.slides:
            if slide.kind != "photo":
                fail("style=collage では type=photo 以外のスライドは使用できません")
    elif cfg.style == "film_scroll":
        for slide in cfg.slides:
            if slide.kind != "photo":
                fail("style=film_scroll では type=photo 以外のスライドは使用できません")
    else:
        for slide in cfg.slides:
            if slide.kind == "video" and slide.duration is None:
                slide.duration = probe_duration(slide.path)
        validate_durations(cfg)

    cfg.output_file.parent.mkdir(parents=True, exist_ok=True)

    with tempfile.TemporaryDirectory(prefix="slideshow_") as tmp:
        tmp_dir = Path(tmp)

        for i, slide in enumerate(cfg.slides):
            if slide.kind == "photo" or (slide.kind == "title" and slide.path is not None):
                slide.path = normalize_photo(slide.path, tmp_dir, i)

        if cfg.style == "photo_pile":
            filter_complex, total_duration, has_audio, extra_inputs = build_photo_pile_filter_complex(cfg, tmp_dir)
            for slide in cfg.slides:
                slide.duration = total_duration  # パイル演出の間、写真をループし続ける
        elif cfg.style == "collage":
            filter_complex, total_duration, has_audio, extra_inputs = build_collage_filter_complex(cfg, tmp_dir)
        elif cfg.style == "film_scroll":
            filter_complex, total_duration, has_audio, canvas_paths = build_film_scroll_filter_complex(cfg, tmp_dir)
        else:
            filter_complex, total_duration, has_audio, extra_inputs = build_filter_complex(cfg, tmp_dir)

        filter_script = tmp_dir / "filter_complex.txt"
        filter_script.write_text(filter_complex, encoding="utf-8")

        needs_post = cfg.particles != "none" or cfg.light_leak
        base_output = (tmp_dir / "base_render.mp4") if needs_post else cfg.output_file

        if cfg.style == "film_scroll":
            cmd = build_film_scroll_ffmpeg_command(cfg, filter_script, total_duration, has_audio, canvas_paths)
        else:
            cmd = build_ffmpeg_command(cfg, filter_script, total_duration, has_audio, extra_inputs)
        cmd[-1] = str(base_output)

        print(f"スライド数: {len(cfg.slides)} / 合計時間: {total_duration:.1f}秒")
        print(f"出力先: {cfg.output_file}")

        if args.dry_run:
            print("\n--- filter_complex ---")
            print(filter_complex)
            print("\n--- ffmpeg command ---")
            print(" ".join(cmd))
            if needs_post:
                print("\n(この後、particles/light_leak の後処理パスが実行されます)")
            return

        result = subprocess.run(cmd)
        if result.returncode != 0:
            print("エラー: ffmpeg の実行に失敗しました。", file=sys.stderr)
            sys.exit(result.returncode)

        if needs_post:
            run_post_effects(cfg, base_output, total_duration, tmp_dir)

        print(f"完成しました: {cfg.output_file}")


if __name__ == "__main__":
    main()
