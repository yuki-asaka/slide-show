#!/usr/bin/env python3
"""config/senario_config.yaml の内容からスライドショー動画を組み立てる。

シーンは「型」(intro / crossfade_gallery / hard_cut_gallery / grid_climax /
outro_photo_pan)で表現され、各シーンの文言・素材フォルダ・タイムスタンプ等
すべての具体的な値はYAML側にある。別の楽曲・別の写真セットで作り直す場合は
config/senario_config.yaml を差し替えるだけでよい(型を追加する場合のみ
このファイルの編集が要る)。
"""

from __future__ import annotations

import math
import subprocess
import shutil
import sys
from pathlib import Path

import yaml

PROJECT_DIR = Path(__file__).resolve().parent
CONFIG_PATH = Path(sys.argv[1]) if len(sys.argv) > 1 else PROJECT_DIR / "config" / "senario_config.yaml"

UNITS_DIR = PROJECT_DIR / ".render_units"
CAPTIONS_DIR = PROJECT_DIR / ".captions"
HEIC_CACHE_DIRNAME = ".heic_converted"
IMAGE_GLOB_PATTERNS = ("*.jpg", "*.jpeg", "*.png", "*.heic", "*.heif")

# main()がconfigから設定する(スクリプト全体で参照する共通パラメータ)。
WIDTH = HEIGHT = FPS = None
MATERIALS_ROOT: Path
OUTPUT_FILE: Path

_SIPS_PATH = shutil.which("sips")


# ---------------------------------------------------------------------------
# 共通ユーティリティ
# ---------------------------------------------------------------------------

def esc(value: str) -> str:
    """ffmpegフィルタのシングルクォート値に埋め込むための最小限のエスケープ。"""
    return str(value).replace("\\", "\\\\").replace("'", "\\'")


def wrap_japanese_text(text: str, max_line_chars: int = 20) -> list[str]:
    """長い一文を1〜2行に分割する。「。」があればそこで区切り、無ければ
    中央に最も近い「、」で区切る。"""
    if "。" in text[:-1]:
        idx = text.index("。")
        return [text[: idx + 1], text[idx + 1 :]]
    if len(text) <= max_line_chars:
        return [text]
    if "、" in text:
        positions = [i for i, c in enumerate(text) if c == "、"]
        mid = len(text) / 2
        best = min(positions, key=lambda p: abs(p - mid))
        return [text[: best + 1], text[best + 1 :]]
    return [text]


def write_caption_file(lines: list[str], name: str) -> Path:
    """drawtextのtextfile=に渡す一時テキストファイルを書き出す。

    drawtextのtext=にリテラルの "\\n" を埋め込んでも改行として解釈されない
    (実機で確認済み)。実際の改行文字を含むファイルをtextfile=で読ませる
    方式のみが確実に複数行表示できる。
    """
    CAPTIONS_DIR.mkdir(parents=True, exist_ok=True)
    path = CAPTIONS_DIR / f"{name}.txt"
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def fit_fontsize(lines: list[str], margin: float = 0.82, min_size: int = 36, max_size: int = 68) -> int:
    """行の最大文字数から画面幅に収まる程度のフォントサイズを概算する
    (日本語の全角グリフはおよそ正方形なので、文字数xフォントサイズを幅の目安にする)。"""
    max_len = max((len(l) for l in lines), default=1)
    est = int(WIDTH * margin / max(max_len, 1))
    return max(min_size, min(max_size, est))


def run(cmd: list[str], desc: str = "") -> None:
    if desc:
        print(f"--- {desc} ---")
    result = subprocess.run(cmd, capture_output=True, text=True)
    if result.returncode != 0:
        print(result.stdout, file=sys.stderr)
        print(result.stderr, file=sys.stderr)
        raise SystemExit(f"コマンドが失敗しました (exit={result.returncode})")


def shade(base: int, i: int, n: int) -> str:
    """base_colorを中心に、枚数に応じて明度を少しずつ変えたRGB16進文字列を返す。"""
    r, g, b = (base >> 16) & 0xFF, (base >> 8) & 0xFF, base & 0xFF
    factor = 0.65 + 0.35 * (i / max(n - 1, 1))
    r, g, b = (min(255, round(c * factor)) for c in (r, g, b))
    return f"0x{r:02X}{g:02X}{b:02X}"


def list_existing_images(folder: Path) -> list[Path]:
    """folder内の対応拡張子の画像を全て列挙する。

    実写真は拡張子が大文字であることが多く(例: "DSC02833.JPG")、macOSは
    大文字小文字を区別しないファイルシステムが既定だが、pathlib.Path.glob
    自体のパターンマッチは大文字小文字を区別するため、小文字パターンだけ
    では取りこぼす。取りこぼすと「フォルダが空」と誤判定してダミー画像を
    上書き生成してしまうため、必ずこの関数を経由すること。
    """
    if not folder.exists():
        return []
    files: set[Path] = set()
    for pattern in IMAGE_GLOB_PATTERNS:
        files.update(folder.glob(pattern))
        files.update(folder.glob(pattern.upper()))
    return sorted(files)


def generate_dummy_images_if_missing(folder: Path, count: int, label: str, base_color: int) -> None:
    """素材フォルダが空の場合のみ、FFmpegでダミー画像を生成する。実素材があれば何もしない。"""
    existing = list_existing_images(folder)
    if existing:
        print(f"素材フォルダに画像があります: {folder} ({len(existing)} 枚) → ダミー生成をスキップ")
        return

    print(f"素材フォルダが空のため、ダミー画像を {count} 枚生成します: {folder}")
    folder.mkdir(parents=True, exist_ok=True)
    for i in range(count):
        out_path = folder / f"{i + 1:03d}.png"
        color = shade(base_color, i, count)
        caption = f"{label} #{i + 1}"
        drawtext = (
            f"drawtext=text='{esc(caption)}':font='Arial':"
            f"fontcolor=white:fontsize=56:box=1:boxcolor=black@0.35:boxborderw=16:"
            f"x=(w-text_w)/2:y=(h-text_h)/2"
        )
        run([
            "ffmpeg", "-y",
            "-f", "lavfi", "-i", f"color=c={color}:s={WIDTH}x{HEIGHT}:d=1",
            "-vf", drawtext,
            "-frames:v", "1",
            str(out_path),
        ])


def load_images(folder: Path, count: int) -> list[Path]:
    """folder内の画像をちょうどcount枚に揃えて返す(不足はループ、超過は先頭count枚)。"""
    sorted_files = list_existing_images(folder)
    if not sorted_files:
        raise SystemExit(f"素材フォルダに画像がありません: {folder}")
    if len(sorted_files) >= count:
        return sorted_files[:count]
    return [sorted_files[i % len(sorted_files)] for i in range(count)]


def convert_heic_if_needed(path: Path) -> Path:
    """HEIC/HEIF入力を、事前にPNGへ変換したキャッシュファイルに差し替える。

    理由は2つ(いずれも実機で確認済み):
    1. ffmpegに直接-loop 1で読ませると、ファイルによって内部的に異なる
       デマルサで検出され、"Option loop not found" で失敗する個体差がある。
    2. iPhoneの高解像度HEICの一部は512x512タイルの格子(grid)形式で保存され、
       libheifなしのffmpegビルドではタイル1枚(=写真の一部分)だけを
       取り出してしまう(画質が荒く見える不具合の原因)。
    macOS標準のsips(Appleの正規HEIFデコーダ)があれば最優先で使い、無ければ
    ffmpeg単体にフォールバックする(その場合は2の不具合が再発しうる)。
    """
    if path.suffix.lower() not in (".heic", ".heif"):
        return path

    cache_dir = path.parent / HEIC_CACHE_DIRNAME
    converted = cache_dir / f"{path.stem}.png"
    if converted.exists():
        return converted

    cache_dir.mkdir(parents=True, exist_ok=True)
    if _SIPS_PATH:
        run([_SIPS_PATH, "-s", "format", "png", str(path), "--out", str(converted)],
            desc=f"HEIC変換(sips): {path.name}")
    else:
        print(f"警告: sipsが見つかりません。ffmpegでHEICを変換します"
              f"(grid形式だと一部分だけが取り出される既知の問題あり): {path.name}")
        run(["ffmpeg", "-y", "-i", str(path), "-frames:v", "1", "-update", "1", str(converted)],
            desc=f"HEIC変換(ffmpeg): {path.name}")
    return converted


class InputBuilder:
    """ffmpegコマンドの -i 群を順番に積み上げ、filter_complexで参照するインデックスを払い出す。"""

    def __init__(self) -> None:
        self.args: list[str] = []
        self.count = 0

    def add_image(self, path: Path, duration: float) -> int:
        path = convert_heic_if_needed(path)
        self.args += ["-loop", "1", "-r", str(FPS), "-t", f"{duration:.6f}", "-i", str(path)]
        idx = self.count
        self.count += 1
        return idx

    def add_lavfi(self, spec: str) -> int:
        self.args += ["-f", "lavfi", "-i", spec]
        idx = self.count
        self.count += 1
        return idx


def zoompan_ease(frames: int, hold_frames: int) -> str:
    """終盤hold_framesを静止させ、それまでの区間をイーズアウト(3乗)で動かす式を返す。"""
    hold_frames = min(hold_frames, max(frames - 1, 0))
    motion_frames = max(frames - hold_frames, 1)
    last = max(motion_frames - 1, 1)
    u = f"min(on/{last},1)"
    return f"(1-pow(1-{u},3))"


def build_zoompan_clip(
    idx: int, duration: float, zoom_expr: str, x_expr: str, y_expr: str,
    target_w: int | None = None, target_h: int | None = None,
) -> tuple[str, str]:
    """[idx:v] を zoompan + trim + setpts + fps で正確な尺のクリップにする。

    -loop 1 の静止画は複数の入力フレームを供給するため、zoompan直後に
    trim/setpts/fpsで意図したフレーム数に確定させないと、尺が伸びて
    同じアニメーションが繰り返される(既知のffmpeg不具合)。
    """
    target_w = target_w or WIDTH
    target_h = target_h or HEIGHT
    label = f"zp{idx}"
    frames = max(1, round(duration * FPS))
    chain = (
        f"[{idx}:v]scale={target_w * 2}:{target_h * 2}:force_original_aspect_ratio=increase,"
        f"crop={target_w * 2}:{target_h * 2},"
        f"zoompan=z='{zoom_expr}':x='{x_expr}':y='{y_expr}':d={frames}:s={target_w}x{target_h}:fps={FPS},"
        f"trim=start_frame=0:end_frame={frames},"
        f"setpts=PTS-STARTPTS,"
        f"fps={FPS}[{label}]"
    )
    return chain, label


def build_static_clip(idx: int, duration: float, target_w: int | None = None, target_h: int | None = None) -> tuple[str, str]:
    """zoompanを使わない、単純な拡大/クロップのみの静止クリップ。"""
    target_w = target_w or WIDTH
    target_h = target_h or HEIGHT
    label = f"st{idx}"
    frames = max(1, round(duration * FPS))
    chain = (
        f"[{idx}:v]scale={target_w}:{target_h}:force_original_aspect_ratio=increase,"
        f"crop={target_w}:{target_h},"
        f"trim=start_frame=0:end_frame={frames},"
        f"setpts=PTS-STARTPTS,"
        f"fps={FPS}[{label}]"
    )
    return chain, label


def concat_group(filters: list[str], labels: list[str], tag: str) -> str:
    """ハードカットで連続する複数クリップを1回の多入力concatにまとめる。

    1組ずつ鎖状にconcatしてからxfadeへ渡すと、フレームがサイレントに欠落する
    既知のffmpeg不具合があるため、必ずこの関数で1回にまとめること。
    """
    if len(labels) == 1:
        return labels[0]
    out = f"cg_{tag}"
    inputs = "".join(f"[{lbl}]" for lbl in labels)
    filters.append(f"{inputs}concat=n={len(labels)}:v=1:a=0,settb=1/{FPS}[{out}]")
    return out


def compensated_clip_duration(segment_seconds: float, n: int, transition_duration: float) -> float:
    """絶対タイムスタンプに厳密に収まるよう、クロスフェードの重なり分を
    見込んだ1枚あたりの尺を逆算する(d = (segment_seconds + (n-1)*td) / n)。"""
    return (segment_seconds + (n - 1) * transition_duration) / n


def apply_trailing_title_overlay(
    filters: list[str], label: str, scene_duration: float, text: str,
    overlay_duration: float = 3.0, tag: str = "trail",
) -> str:
    """シーン本編の末尾overlay_duration秒に、次シーンへのタイトルカードを
    「被せる」(ブラー+暗転+ドロップシャドウ付き文字)。gblur/eqの`enable`
    タイムラインオプションで、この区間だけブラー+暗転をかける。
    """
    window_start = max(scene_duration - overlay_duration, 0)
    window_end = scene_duration
    enable_expr = f"between(t,{window_start:.3f},{window_end:.3f})"

    darkened_label = f"{tag}_dark"
    filters.append(
        f"[{label}]gblur=sigma=20:enable='{enable_expr}',"
        f"eq=brightness=-0.3:enable='{enable_expr}'[{darkened_label}]"
    )

    lines = wrap_japanese_text(text)
    caption_path = write_caption_file(lines, tag)
    fontsize = fit_fontsize(lines)

    slide_seconds = min(0.6, overlay_duration / 2)
    fade_in_end = window_start + slide_seconds
    fade_out_start = window_end - slide_seconds
    alpha_expr = (
        f"if(lt(t,{window_start:.3f}),0,"
        f"if(lt(t,{fade_in_end:.3f}),(t-{window_start:.3f})/{slide_seconds},"
        f"if(lt(t,{fade_out_start:.3f}),1,"
        f"if(lt(t,{window_end:.3f}),({window_end:.3f}-t)/{slide_seconds},0))))"
    )
    y_expr = f"(h-text_h)/2+50*(1-min(max(t-{window_start:.3f},0)/{slide_seconds},1))"

    shadow_label = f"{tag}_shadow"
    filters.append(
        f"[{darkened_label}]drawtext=textfile='{caption_path}':font='Hiragino Sans':"
        f"fontcolor=black@0.6:fontsize={fontsize}:line_spacing=8:"
        f"x=(w-text_w)/2+4:y='({y_expr})+4':alpha='{alpha_expr}'[{shadow_label}]"
    )
    out_label = f"{tag}_out"
    filters.append(
        f"[{shadow_label}]drawtext=textfile='{caption_path}':font='Hiragino Sans':"
        f"fontcolor=white:fontsize={fontsize}:line_spacing=8:"
        f"x=(w-text_w)/2:y='{y_expr}':alpha='{alpha_expr}'[{out_label}]"
    )
    return out_label


def chain_xfade(
    filters: list[str], labels: list[str], clip_duration: float,
    transition_duration: float, transition: str, tag: str,
) -> tuple[str, float]:
    """複数クリップを順にxfadeで繋ぐ。各クリップは同じ長さclip_durationを持つ前提で、
    隣接クリップがtransition_duration秒だけ重なるため、全体尺は
    sum(clip_duration) - (n-1)*transition_duration になる。xfade直後には
    必ずfpsを付ける(既知のffmpeg不具合対策)。
    """
    acc_label = labels[0]
    acc_duration = clip_duration
    for i, label in enumerate(labels[1:], start=1):
        out = f"{tag}_x{i}"
        offset = max(acc_duration - transition_duration, 0)
        filters.append(
            f"[{acc_label}][{label}]xfade=transition={transition}:"
            f"duration={transition_duration}:offset={offset:.3f},fps={FPS}[{out}]"
        )
        acc_label = out
        acc_duration = acc_duration + clip_duration - transition_duration
    return acc_label, acc_duration


def build_grid_segment(
    ib: InputBuilder, filters: list[str], photos: list[Path], duration: float,
    tag: str, appear_interval: float, fade_dur: float,
) -> str:
    """NxNグリッドセグメントを1つ作る(枚数の平方根がNになる)。各タイルは時間差でポップアップする。"""
    n = len(photos)
    grid_size = round(math.sqrt(n))
    if grid_size * grid_size != n:
        raise ValueError(f"build_grid_segmentへの写真枚数は平方数である必要があります: {n}")
    tile_w, tile_h = WIDTH // grid_size, HEIGHT // grid_size

    tile_labels = []
    for i, path in enumerate(photos):
        idx = ib.add_image(path, duration)
        appear_at = i * appear_interval
        label = f"{tag}_tile{i}"
        chain, base_label = build_zoompan_clip(
            idx, duration, "1", "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)",
            target_w=tile_w, target_h=tile_h,
        )
        filters.append(chain)
        # alpha=1のフェードだけではformat=yuv420p変換時にアルファ情報が失われ
        # 「フェードインして見えない」ため、黒背景に一度overlayしてから確定させる。
        bg_label = f"{label}bg"
        faded_label = f"{label}f"
        filters.append(f"color=c=black:s={tile_w}x{tile_h}:d={duration:.3f}:r={FPS}[{bg_label}]")
        filters.append(
            f"[{base_label}]format=rgba,"
            f"fade=t=in:st={appear_at:.3f}:d={fade_dur}:alpha=1[{faded_label}]"
        )
        filters.append(
            f"[{bg_label}][{faded_label}]overlay=0:0,"
            f"trim=0:{duration:.3f},setpts=PTS-STARTPTS,fps={FPS}[{label}]"
        )
        tile_labels.append(label)

    grid_label = f"{tag}_grid"
    inputs = "".join(f"[{lbl}]" for lbl in tile_labels)
    filters.append(f"{inputs}xstack=inputs={n}:grid={grid_size}x{grid_size},format=yuv420p[{grid_label}]")
    return grid_label


def build_solo_segment(ib: InputBuilder, filters: list[str], path: Path, duration: float, tag: str) -> str:
    """全画面表示の1枚。冒頭に軽いフェードインポップと、控えめなkenburns-inを付ける。"""
    idx = ib.add_image(path, duration)
    frames = max(1, round(duration * FPS))
    ease = zoompan_ease(frames, hold_frames=round(0.5 * FPS))
    zoom_expr = f"1+0.05*{ease}"
    chain, base_label = build_zoompan_clip(idx, duration, zoom_expr, "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)")
    filters.append(chain)
    label = f"{tag}_solo"
    bg_label = f"{label}bg"
    faded_label = f"{label}f"
    filters.append(f"color=c=black:s={WIDTH}x{HEIGHT}:d={duration:.3f}:r={FPS}[{bg_label}]")
    filters.append(f"[{base_label}]format=rgba,fade=t=in:st=0:d=0.3:alpha=1[{faded_label}]")
    filters.append(
        f"[{bg_label}][{faded_label}]overlay=0:0,"
        f"trim=0:{duration:.3f},setpts=PTS-STARTPTS,fps={FPS}[{label}]"
    )
    return label


# ---------------------------------------------------------------------------
# 素材読み込み(シーン間の写真参照も扱う)
# ---------------------------------------------------------------------------

class RenderContext:
    def __init__(self, materials_root: Path):
        self.materials_root = materials_root
        self.photos: dict[str, list[Path]] = {}

    def load(self, cfg: dict) -> list[Path]:
        scene_id = cfg["id"]
        if scene_id in self.photos:
            return self.photos[scene_id]
        folder = self.materials_root / cfg["folder"]
        count = cfg["count"]
        generate_dummy_images_if_missing(folder, count, cfg.get("dummy_label", scene_id), cfg.get("dummy_color", 0x888888))

        if cfg.get("select") == "first_last":
            # 集合写真がフォルダ内で最後にソートされる運用を前提に、
            # 「先頭からcount枚」ではなく先頭と末尾を明示的に選ぶ
            # (スナップがcount-1枚を超えて存在しても集合写真を失わない)。
            available = list_existing_images(folder)
            photos = [available[0], available[-1]] if len(available) >= 2 else load_images(folder, count)
        else:
            photos = load_images(folder, count)

        self.photos[scene_id] = photos
        return photos

    def photo_ref(self, ref: dict) -> Path:
        return self.photos[ref["scene"]][ref["index"]]


# ---------------------------------------------------------------------------
# シーンの型
# ---------------------------------------------------------------------------

def build_type_intro(cfg: dict, ctx: RenderContext) -> list[tuple[str, callable]]:
    photos = ctx.load(cfg)
    intro_duration = cfg["intro_duration"]
    snap_duration = cfg["snap_duration"]
    group_duration = cfg["group_duration"]
    intro_fade = cfg.get("intro_fade", 1.5)
    total_duration = intro_duration + snap_duration + group_duration
    next_title = cfg.get("next_title")

    def build(ib: InputBuilder, filters: list[str]) -> tuple[str, float]:
        intro_lines = wrap_japanese_text(cfg["intro_text"])
        intro_caption = write_caption_file(intro_lines, f"{cfg['id']}_intro")
        intro_fontsize = fit_fontsize(intro_lines, max_size=68)
        fade_out_start = intro_duration - intro_fade
        intro_label = f"{cfg['id']}_introbg"
        filters.append(
            f"color=c=black:s={WIDTH}x{HEIGHT}:d={intro_duration:.3f}:r={FPS},"
            f"drawtext=textfile='{intro_caption}':font='Hiragino Sans':"
            f"fontcolor=white:fontsize={intro_fontsize}:line_spacing=8:"
            f"x=(w-text_w)/2:y=(h-text_h)/2:"
            f"alpha='if(lt(t,{intro_fade}),t/{intro_fade},"
            f"if(gt(t,{fade_out_start:.3f}),max(0,({intro_duration:.3f}-t)/{intro_fade}),1))',"
            f"trim=0:{intro_duration:.3f},setpts=PTS-STARTPTS,fps={FPS}[{intro_label}]"
        )

        idx_snap = ib.add_image(photos[0], snap_duration)
        ease_snap = zoompan_ease(max(1, round(snap_duration * FPS)), hold_frames=round(1.0 * FPS))
        chain_snap, snap_label = build_zoompan_clip(
            idx_snap, snap_duration, f"1.3-0.3*{ease_snap}",
            "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)",
        )
        filters.append(chain_snap)

        idx_group = ib.add_image(photos[1], group_duration)
        ease_group = zoompan_ease(max(1, round(group_duration * FPS)), hold_frames=round(1.0 * FPS))
        chain_group, group_label = build_zoompan_clip(
            idx_group, group_duration, "1.1", f"(iw-iw/zoom)*{ease_group}", "ih/2-(ih/zoom/2)",
        )
        filters.append(chain_group)

        body = concat_group(filters, [intro_label, snap_label, group_label], cfg["id"])
        if next_title:
            body = apply_trailing_title_overlay(filters, body, total_duration, next_title, tag=f"{cfg['id']}trail")
        out_label = cfg["id"]
        filters.append(f"[{body}]null[{out_label}]")
        return out_label, total_duration

    return [("", build)]


def build_type_crossfade_gallery(cfg: dict, ctx: RenderContext) -> list[tuple[str, callable]]:
    photos = ctx.load(cfg)
    ts = cfg["timestamp"]
    body_seconds = ts["end"] - ts["start"]
    transition_duration = cfg["transition_duration"]
    clip_duration = compensated_clip_duration(body_seconds, len(photos), transition_duration)
    zoom_amount = cfg.get("zoom")  # None = zoompan不使用(単純な拡大/クロップのみ)
    contrast = cfg.get("contrast")
    next_title = cfg.get("next_title")

    def build(ib: InputBuilder, filters: list[str]) -> tuple[str, float]:
        labels = []
        for path in photos:
            idx = ib.add_image(path, clip_duration)
            if zoom_amount:
                frames = max(1, round(clip_duration * FPS))
                ease = zoompan_ease(frames, hold_frames=round(1.0 * FPS))
                chain, label = build_zoompan_clip(
                    idx, clip_duration, f"1+{zoom_amount}*{ease}",
                    "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)",
                )
            else:
                chain, label = build_static_clip(idx, clip_duration)
            filters.append(chain)
            if contrast:
                contrast_label = f"{label}c"
                filters.append(f"[{label}]eq=contrast={contrast}[{contrast_label}]")
                label = contrast_label
            labels.append(label)

        body, duration = chain_xfade(filters, labels, clip_duration, transition_duration, "fade", cfg["id"])
        if next_title:
            body = apply_trailing_title_overlay(filters, body, duration, next_title, tag=f"{cfg['id']}trail")
        out_label = cfg["id"]
        filters.append(f"[{body}]null[{out_label}]")
        return out_label, duration

    return [("", build)]


def build_type_hard_cut_gallery(cfg: dict, ctx: RenderContext) -> list[tuple[str, callable]]:
    photos = ctx.load(cfg)
    ts = cfg["timestamp"]
    body_seconds = ts["end"] - ts["start"]
    per_photo = body_seconds / len(photos)
    zoom = cfg.get("zoom", 1.05)
    saturation = cfg.get("saturation")
    white_flash_out = cfg.get("white_flash_out")
    white_flash_in = cfg.get("white_flash_in")
    split_cfg = cfg.get("split")
    next_title = cfg.get("next_title")

    def build(ib: InputBuilder, filters: list[str]) -> tuple[str, float]:
        labels = []
        for i, path in enumerate(photos):
            if split_cfg and i == split_cfg["index"]:
                half_w = WIDTH // 2
                left_photo = ctx.photo_ref({"scene": split_cfg["other_scene"], "index": -1})
                left_idx = ib.add_image(left_photo, per_photo)
                chain_left, left_label = build_zoompan_clip(
                    left_idx, per_photo, str(split_cfg.get("other_zoom", zoom)),
                    "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)", target_w=half_w, target_h=HEIGHT,
                )
                right_idx = ib.add_image(path, per_photo)
                chain_right, right_label = build_zoompan_clip(
                    right_idx, per_photo, str(zoom),
                    "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)", target_w=half_w, target_h=HEIGHT,
                )
                filters.append(chain_left)
                filters.append(chain_right)
                split_label = f"{cfg['id']}split{i}"
                filters.append(f"[{left_label}][{right_label}]hstack=inputs=2[{split_label}]")
                label = split_label
            else:
                idx = ib.add_image(path, per_photo)
                chain, label = build_zoompan_clip(
                    idx, per_photo, str(zoom), "iw/2-(iw/zoom/2)", "ih/2-(ih/zoom/2)",
                )
                filters.append(chain)

            if saturation is not None:
                sat_label = f"{label}s"
                filters.append(f"[{label}]eq=saturation={saturation}[{sat_label}]")
                label = sat_label
            labels.append(label)

        body = concat_group(filters, labels, cfg["id"])
        if next_title:
            body = apply_trailing_title_overlay(filters, body, body_seconds, next_title, tag=f"{cfg['id']}trail")

        if white_flash_in:
            flashed = f"{cfg['id']}_flashin"
            filters.append(f"[{body}]fade=t=in:st=0:d={white_flash_in}:color=white[{flashed}]")
            body = flashed
        if white_flash_out:
            flash_start = body_seconds - white_flash_out
            flashed = f"{cfg['id']}_flashout"
            filters.append(f"[{body}]fade=t=out:st={flash_start:.3f}:d={white_flash_out}:color=white[{flashed}]")
            body = flashed

        out_label = cfg["id"]
        filters.append(f"[{body}]null[{out_label}]")
        return out_label, body_seconds

    return [("", build)]


def build_type_grid_climax(cfg: dict, ctx: RenderContext) -> list[tuple[str, callable]]:
    photos = ctx.load(cfg)
    ts = cfg["timestamp"]
    total_seconds = ts["end"] - ts["start"]
    rounds = cfg["rounds"]
    tiles_per_grid = cfg["tiles_per_grid"]
    solos_per_round = cfg["solos_per_round"]
    grid_duration = cfg["grid_duration"]
    appear_interval = cfg.get("appear_interval", 0.2)
    round_seconds = total_seconds / rounds
    solo_duration = (round_seconds - grid_duration) / solos_per_round
    photos_per_round = tiles_per_grid + solos_per_round
    if len(photos) != rounds * photos_per_round:
        raise ValueError(f"{cfg['id']}: 写真枚数({len(photos)})がrounds*photos_per_round({rounds * photos_per_round})と一致しません")

    white_flash = cfg.get("white_flash")

    units = []
    for round_i in range(rounds):
        round_photos = photos[round_i * photos_per_round:(round_i + 1) * photos_per_round]
        grid_photos = round_photos[:tiles_per_grid]
        solo_photos = round_photos[tiles_per_grid:]

        def build(ib: InputBuilder, filters: list[str], grid_photos=grid_photos, solo_photos=solo_photos, round_i=round_i) -> tuple[str, float]:
            tag = f"{cfg['id']}r{round_i}"
            segments = [build_grid_segment(ib, filters, grid_photos, grid_duration, tag, appear_interval, appear_interval)]
            for solo_i, solo_photo in enumerate(solo_photos):
                segments.append(build_solo_segment(ib, filters, solo_photo, solo_duration, f"{tag}s{solo_i}"))
            body = concat_group(filters, segments, tag)
            duration = grid_duration + solo_duration * len(solo_photos)

            out_label = f"{cfg['id']}round{round_i}"
            if white_flash and white_flash["round_index"] == round_i:
                pulse_in = white_flash["at"]
                pulse_dur = white_flash["duration"]
                flash_label = f"{tag}flash"
                filters.append(
                    f"color=c=white:s={WIDTH}x{HEIGHT}:d={duration:.3f}:r={FPS},format=rgba,"
                    f"fade=t=in:st={pulse_in:.3f}:d={pulse_dur}:alpha=1,"
                    f"fade=t=out:st={pulse_in + pulse_dur:.3f}:d={pulse_dur}:alpha=1[{flash_label}]"
                )
                filters.append(
                    f"[{body}][{flash_label}]overlay=0:0,"
                    f"trim=0:{duration:.3f},setpts=PTS-STARTPTS,fps={FPS}[{out_label}]"
                )
            else:
                filters.append(f"[{body}]null[{out_label}]")
            return out_label, duration

        units.append((f"_round{round_i}", build))
    return units


def build_type_outro_photo_pan(cfg: dict, ctx: RenderContext) -> list[tuple[str, callable]]:
    ts = cfg["timestamp"]
    duration = ts["end"] - ts["start"]
    text_start = cfg["text_start"]
    fade_in_duration = cfg["fade_in"]
    fade_out_duration = cfg["fade_out"]
    fade_out_start = duration - fade_out_duration
    bg_photo = ctx.photo_ref(cfg["bg_source"])

    def build(ib: InputBuilder, filters: list[str]) -> tuple[str, float]:
        lines = wrap_japanese_text(cfg["text"])
        caption_path = write_caption_file(lines, f"{cfg['id']}_message")
        fontsize = fit_fontsize(lines, max_size=72)

        idx = ib.add_image(bg_photo, duration)
        ease = zoompan_ease(max(1, round(duration * FPS)), hold_frames=round(1.0 * FPS))
        # 下から上へ(空を見上げるように): yが最大値(下側を表示)から0(上側を表示)へ移動する。
        y_expr = f"(ih-ih/zoom)*(1-{ease})"
        bg_chain, bg_label = build_zoompan_clip(idx, duration, "1.1", "iw/2-(iw/zoom/2)", y_expr)
        filters.append(bg_chain)

        alpha_expr = (
            f"if(lt(t,{text_start}),0,"
            f"if(lt(t,{text_start + fade_in_duration}),(t-{text_start})/{fade_in_duration},1))"
        )
        texted_label = f"{cfg['id']}_texted"
        filters.append(
            f"[{bg_label}]drawtext=textfile='{caption_path}':font='Hiragino Sans':"
            f"fontcolor=white:fontsize={fontsize}:line_spacing=8:"
            f"box=1:boxcolor=black@0.4:boxborderw=20:"
            f"x=(w-text_w)/2:y=h-text_h-90:alpha='{alpha_expr}'[{texted_label}]"
        )

        out_label = cfg["id"]
        filters.append(
            f"[{texted_label}]fade=t=out:st={fade_out_start:.3f}:d={fade_out_duration}:color=black,"
            f"trim=0:{duration:.3f},setpts=PTS-STARTPTS,fps={FPS}[{out_label}]"
        )
        return out_label, duration

    return [("", build)]


SCENE_TYPES = {
    "intro": build_type_intro,
    "crossfade_gallery": build_type_crossfade_gallery,
    "hard_cut_gallery": build_type_hard_cut_gallery,
    "grid_climax": build_type_grid_climax,
    "outro_photo_pan": build_type_outro_photo_pan,
}


# ---------------------------------------------------------------------------
# レンダリング(ユニット単位) / 結合 / 分割
#
# シーン全部を1つのfilter_complexにまとめると、写真枚数が多い構成
# (実測: 177枚)ではグラフ初期化時にメモリ不足でffmpegがOSに強制終了させ
# られる(exit=-9)。そのためシーンごと(grid_climaxはラウンドごと)に個別の
# ffmpegプロセスでレンダリングしてから、concatデマルサ(-c copy、無劣化)で
# 結合する。
# ---------------------------------------------------------------------------

def render_unit(name: str, ib: InputBuilder, filters: list[str], out_label: str, duration: float) -> Path:
    UNITS_DIR.mkdir(parents=True, exist_ok=True)
    unit_filters = [*filters, f"[{out_label}]format=yuv420p,setsar=1[vout]"]
    filter_script = UNITS_DIR / f"{name}.filter.txt"
    filter_script.write_text(";\n".join(unit_filters), encoding="utf-8")

    out_path = UNITS_DIR / f"{name}.mp4"
    cmd = ["ffmpeg", "-y"] + ib.args
    cmd += ["-filter_complex_threads", "1", "-filter_complex_script", str(filter_script)]
    cmd += ["-map", "[vout]", "-an"]
    cmd += [
        # 既定のスレッド数のままだとlookaheadバッファ等でメモリ不足になり
        # OS(jetsam)にkillされたことがある(実測)ため絞る。
        "-threads", "4",
        "-c:v", "libx264", "-pix_fmt", "yuv420p", "-r", str(FPS),
        "-preset", "veryfast", "-crf", "18",
        "-x264-params", "threads=4:lookahead_threads=1",
    ]
    cmd += ["-t", f"{duration:.3f}", str(out_path)]
    run(cmd, desc=f"レンダリング: {name} ({duration:.2f}秒)")
    return out_path


def find_bgm_file() -> Path | None:
    """materials/直下のBGMファイルを探す。著作権のある楽曲はこちらで用意
    できないため、ユーザーが配置したファイルを使う。無ければNoneを返す。"""
    for ext in ("*.mp3", "*.m4a", "*.wav", "*.aac"):
        matches = sorted(MATERIALS_ROOT.glob(ext))
        if matches:
            return matches[0]
    return None


def concat_units_and_finalize(unit_paths: list[Path], total_duration: float, audio_fade_out_duration: float) -> None:
    """全ユニットをconcatデマルサ(-c copy)で結合し、音声トラックを合成する。

    materials/直下にBGMファイル(mp3/m4a/wav/aac)があればそれを使い、無ければ
    無音にフォールバックする(その場合は実際の楽曲との同期は未検証になる)。
    """
    concat_list_path = UNITS_DIR / "concat_list.txt"
    concat_list_path.write_text("\n".join(f"file '{p.resolve()}'" for p in unit_paths), encoding="utf-8")

    video_only_path = UNITS_DIR / "concatenated_video.mp4"
    run(
        ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(concat_list_path), "-c", "copy", str(video_only_path)],
        desc="全ユニットをconcatデマルサで結合(映像)",
    )

    OUTPUT_FILE.parent.mkdir(parents=True, exist_ok=True)
    audio_fade_start = max(total_duration - audio_fade_out_duration, 0)
    bgm_path = find_bgm_file()
    if bgm_path is not None:
        print(f"BGMファイルを使用します: {bgm_path}")
        audio_input = ["-i", str(bgm_path)]
    else:
        print(
            f"警告: BGMファイルが {MATERIALS_ROOT} に見つかりません。"
            "無音トラックで代用するため、実際の楽曲とのシーン同期は未検証です。",
            file=sys.stderr,
        )
        audio_input = ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
    audio_filter = (
        f"[1:a]atrim=0:{total_duration:.3f},asetpts=PTS-STARTPTS,"
        f"afade=t=out:st={audio_fade_start:.3f}:d={audio_fade_out_duration}[aout]"
    )
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_only_path),
        *audio_input,
        "-filter_complex", audio_filter,
        "-map", "0:v", "-map", "[aout]",
        "-c:v", "copy", "-c:a", "aac", "-b:a", "192k",
        "-movflags", "+faststart", "-t", f"{total_duration:.3f}",
        str(OUTPUT_FILE),
    ]
    run(cmd, desc="オーディオトラックを合成して最終出力")


def find_keyframe_near(video_path: Path, target_seconds: float) -> float:
    """video_path内でtarget_secondsに最も近いキーフレームの時刻(秒)を返す。

    各ユニットは独立にエンコードしてからconcatデマルサ(-c copy)で結合して
    いるため、ユニットごとの端数が累積し、実際のタイムスタンプは理論値から
    数百ミリ秒ずれることがある(実測)。無劣化(-c copy)で分割する際は、
    理論値ではなく実際のキーフレーム位置を検出してそこで切る。
    """
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-select_streams", "v:0",
         "-show_entries", "frame=pts_time,pict_type", "-of", "csv=p=0", str(video_path)],
        capture_output=True, text=True,
    )
    keyframes = []
    for line in result.stdout.strip().splitlines():
        # 一部の行は末尾に空フィールドが付く("pts_time,I,")ため完全一致では判定しない。
        parts = line.split(",")
        if len(parts) >= 2 and parts[1] == "I":
            try:
                keyframes.append(float(parts[0]))
            except ValueError:
                continue
    if not keyframes:
        return target_seconds
    return min(keyframes, key=lambda k: abs(k - target_seconds))


def split_output_in_two(source: Path, target_split_seconds: float) -> None:
    """完成した動画を、target_split_seconds付近の実際のキーフレームで無劣化2分割する。

    一部の動画編集ソフト(Filmora等)で長尺(4分超)動画のインポート時に
    末尾が切れる問題への対策(ユーザー報告により追加)。
    """
    split_at = find_keyframe_near(source, target_split_seconds)
    print(f"動画を2分割します(境界: 目標{target_split_seconds:.3f}秒 → 実際のキーフレーム{split_at:.3f}秒)")

    part1_path = source.with_name(f"{source.stem}_part1{source.suffix}")
    part2_path = source.with_name(f"{source.stem}_part2{source.suffix}")
    run(["ffmpeg", "-y", "-i", str(source), "-t", f"{split_at:.6f}", "-c", "copy", str(part1_path)],
        desc=f"分割 (前半): {part1_path.name}")
    run(["ffmpeg", "-y", "-ss", f"{split_at:.6f}", "-i", str(source), "-c", "copy", str(part2_path)],
        desc=f"分割 (後半): {part2_path.name}")
    verify_no_dropped_frames(part1_path)
    verify_no_dropped_frames(part2_path)


def verify_no_dropped_frames(video_path: Path) -> None:
    print(f"検証: 全編デコードしてフレーム欠落(drop)が無いか確認します: {video_path}")
    result = subprocess.run(["ffmpeg", "-v", "warning", "-i", str(video_path), "-f", "null", "-"],
                             capture_output=True, text=True)
    warnings = result.stderr.strip()
    if warnings:
        print("--- ffmpeg警告 ---", file=sys.stderr)
        print(warnings, file=sys.stderr)
        if "drop" in warnings.lower():
            raise SystemExit("フレーム欠落(drop)の警告が検出されました。フィルタグラフを見直してください。")
    else:
        print("警告なし。フレーム欠落は検出されませんでした。")


def main() -> None:
    global WIDTH, HEIGHT, FPS, MATERIALS_ROOT, OUTPUT_FILE

    if not CONFIG_PATH.exists():
        raise SystemExit(f"設定ファイルが見つかりません: {CONFIG_PATH}")
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))

    res = config["resolution"]
    WIDTH, HEIGHT, FPS = res["width"], res["height"], res["fps"]
    MATERIALS_ROOT = PROJECT_DIR / config.get("materials_root", "materials")
    OUTPUT_FILE = PROJECT_DIR / "output" / config["output"]["filename"]

    print(f"作業ディレクトリ: {PROJECT_DIR}")
    print(f"設定ファイル: {CONFIG_PATH}")

    ctx = RenderContext(MATERIALS_ROOT)
    split_cfg = config.get("split")

    unit_paths: list[Path] = []
    total_duration = 0.0
    split_target_seconds: float | None = None
    scene6_fade_out = 3.0  # BGMのafade秒数(outro_photo_panシーンのfade_outで上書きされる)

    for scene_cfg in config["scenes"]:
        builder = SCENE_TYPES.get(scene_cfg["type"])
        if builder is None:
            raise SystemExit(f"未知のシーン型です: {scene_cfg['type']} (id={scene_cfg['id']})")
        if scene_cfg["type"] == "outro_photo_pan":
            scene6_fade_out = scene_cfg["fade_out"]

        for suffix, build_fn in builder(scene_cfg, ctx):
            ib = InputBuilder()
            filters: list[str] = []
            label, dur = build_fn(ib, filters)
            unit_paths.append(render_unit(f"{scene_cfg['id']}{suffix}", ib, filters, label, dur))
            total_duration += dur

        if split_cfg and split_cfg.get("after_scene") == scene_cfg["id"]:
            split_target_seconds = total_duration

    concat_units_and_finalize(unit_paths, total_duration, scene6_fade_out)

    print(f"動画を生成しました: {OUTPUT_FILE} (想定尺: {total_duration:.2f}秒 = {total_duration/60:.2f}分)")
    verify_no_dropped_frames(OUTPUT_FILE)

    if split_target_seconds is not None:
        split_output_in_two(OUTPUT_FILE, split_target_seconds)


if __name__ == "__main__":
    main()
