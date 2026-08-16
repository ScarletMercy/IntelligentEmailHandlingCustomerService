# syntax=docker/dockerfile:1

# Base image: python:3.13-slim matches the development machine (Python 3.13.9).
# The -slim variant keeps the image small while still providing a usable Debian
# toolchain (useradd, ca-certificates, etc.).
# NOTE: 未按 digest 固定（tag 可变）。如需严格可复现，请在有 Docker Hub 访问的
# 环境执行 `docker pull python:3.13-slim` 后以 `docker inspect --format
# '{{index .RepoDigests 0}}'` 获取 digest 替换下方 tag。
FROM python:3.13-slim

# Install uv from the official distroless image (pinned for reproducibility).
COPY --from=ghcr.io/astral-sh/uv:0.9.10 /uv /uvx /bin/

# --- Build/runtime ergonomics ---
# PYTHONDONTWRITEBYTECODE: don't emit .pyc (smaller image, no stale bytecode).
# PYTHONUNBUFFERED: flush stdout/stderr immediately so logs aren't buffered
#   while the process runs as PID 1 inside the container.
# HOME: ensure pathlib.Path.home() / "~" resolve to the runtime user's home,
#   which is where the app expects its config (~/.ai-email/.env).
# UV_LINK_MODE=copy: the cache dir and the venv can live on different
#   filesystems in some storage drivers; copy avoids hardlink warnings.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    HOME=/home/aiemail \
    UV_LINK_MODE=copy

WORKDIR /app

# --- Layer 1: dependency manifest + lockfile (Docker layer cache) ---
# Copy pyproject.toml + uv.lock first so the dependency layer is reused from
# cache when only the source code changes (not the dependency list).
# --frozen: install exactly what uv.lock pins (no re-resolution).
# --no-dev: skip the dev group (pytest / pyinstaller are not needed at runtime).
# --no-install-project: install third-party deps only; the project itself is
#   installed in the next layer.
COPY pyproject.toml uv.lock ./
# --mount=cache：uv 缓存跨构建复用（BuildKit），依赖未变时秒级命中
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

# --- Layer 2: application source + install ---
# `uv sync` installs the project into /app/.venv and registers the console
# entry point `ai-email` (-> ai_email:main).
COPY ai_email ./ai_email
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev

# Put the venv on PATH so the ENTRYPOINT below resolves `ai-email`.
ENV PATH="/app/.venv/bin:$PATH"

# --- Non-root runtime user (principle of least privilege) ---
RUN useradd --create-home aiemail

# The app stores configuration (~/.ai-email/.env), the SQLite DB, the PID file
# and logs under ~/.ai-email. Pre-create it and grant ownership to the runtime
# user so the process can read/write when this path is mounted as a volume.
RUN mkdir -p /home/aiemail/.ai-email \
    && chown -R aiemail:aiemail /home/aiemail

USER aiemail

# Mount point for configuration & persistent state (.env config + SQLite + logs).
# At run time, bind-mount your local config directory, e.g.:
#   docker run -v "$HOME/.ai-email:/home/aiemail/.ai-email" <image>
VOLUME ["/home/aiemail/.ai-email"]

# Run the email listener in the FOREGROUND as PID 1.
# Deliberately NOT `ai-email daemon`: double-daemonizing (the process daemonizing
# itself while already supervised by the container runtime) is an anti-pattern.
# It breaks signal handling, restart policies and clean shutdown. The container
# runtime (Docker / Kubernetes) is the supervisor; the process must stay in the
# foreground. With no subcommand, `ai-email` runs the listener in the foreground.
#
# No HEALTHCHECK: this service is an IMAP poller and does not listen on any port,
# so there is no cheap liveness probe. A `ai-email status`-based check relies on
# the PID file, whose PID is invalidated on every container restart; therefore a
# meaningful HEALTHCHECK cannot be expressed and is intentionally omitted.
ENTRYPOINT ["ai-email"]
