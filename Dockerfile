# syntax=docker/dockerfile:1
FROM python:3.11-slim AS builder
WORKDIR /app
COPY pyproject.toml README.md ./
COPY app ./app
RUN pip install --no-cache-dir --prefix=/install .

FROM python:3.11-slim
LABEL org.opencontainers.image.title="dockerfile-optimizer"
LABEL org.opencontainers.image.source="https://github.com/Oruwe/Stow"
COPY --from=builder /install /usr/local
WORKDIR /work
# A linter is a short-lived process; liveness is the caller's concern, and
# declaring NONE stops a base image healthcheck being inherited silently.
HEALTHCHECK NONE
USER 1001
ENTRYPOINT ["stow"]
CMD ["analyze"]
