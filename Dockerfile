FROM python:3.14-slim

COPY --from=ghcr.io/astral-sh/uv:0.12.13 /uv /usr/local/bin/uv

# ffmpeg: 動画生成本体 / fontconfig+fonts-noto-cjk: 字幕(drawtext)の日本語表示に必要
RUN apt-get update && apt-get install -y --no-install-recommends \
        ffmpeg \
        fontconfig \
        fonts-noto-cjk \
    && rm -rf /var/lib/apt/lists/* \
    && fc-cache -f

# root権限では動かさない。ホストのUID/GIDとファイル権限を合わせたい場合(主にLinux)は
# `UID=$(id -u) GID=$(id -g) docker compose build` のように上書きする。
ARG UID=1000
ARG GID=1000
RUN groupadd -g "${GID}" appuser && useradd -m -u "${UID}" -g "${GID}" appuser

WORKDIR /work
ENV UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:${PATH}"
RUN mkdir -p /opt/venv && chown -R appuser:appuser /opt/venv /work

USER appuser

COPY --chown=appuser:appuser pyproject.toml uv.lock .
RUN uv sync --locked --no-install-project

COPY --chown=appuser:appuser build_video.py .

ENTRYPOINT ["python", "build_video.py"]
CMD ["--config", "config/slides.yaml"]
