FROM python:3.13-slim

LABEL org.opencontainers.image.source=https://github.com/brk3/brigid
LABEL org.opencontainers.image.description="Fetches missing subtitles for Ogma's media libraries"

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1

COPY requirements.txt /app/requirements.txt
RUN pip install --no-cache-dir -r /app/requirements.txt

COPY health.py subtitles.py watch.py /app/

ENV HOME=/tmp
RUN rm -rf /tmp/.cache

WORKDIR /app
USER 1000:1000
EXPOSE 8080
ENTRYPOINT ["python3", "/app/watch.py"]
