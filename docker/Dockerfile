FROM ghcr.io/astral-sh/uv:latest AS uv

FROM python:3.12-slim-bookworm

LABEL org.opencontainers.image.description="Magazarr manages magazine downloads through Quasarr and exposes OPDS."

COPY --from=uv /uv /usr/local/bin/uv
COPY dist/*.whl /tmp/

ENV UV_TOOL_DIR=/opt/uv-tools
ENV UV_TOOL_BIN_DIR=/usr/local/bin

RUN --mount=type=cache,target=/root/.cache/uv \
    uv tool install /tmp/*.whl --force && rm /tmp/*.whl

VOLUME /config
VOLUME /library
VOLUME /output
EXPOSE 8090

ENV PYTHONUNBUFFERED=1
ENV DOCKER="true"
ENV LISTEN="0.0.0.0"
ENV PORT="8090"

ENTRYPOINT ["magazarr"]
