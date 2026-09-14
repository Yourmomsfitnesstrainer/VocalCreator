FROM python:3.12-slim

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg rubberband-cli \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install --no-cache-dir '.[ml,web,studio,separation]'

EXPOSE 8080
ENTRYPOINT ["karaoke-gen"]
CMD ["web", "--host", "0.0.0.0", "--port", "8080"]

