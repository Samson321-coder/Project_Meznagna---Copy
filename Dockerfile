# syntax=docker/dockerfile:1
# Hugging Face Docker Spaces run the container as UID 1000. Files must be owned by
# that user or SQLite (and other writes) fail during startup.
FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    HOME=/home/user

# Writable at runtime for optional SQLite persistence when Space storage mounts here
RUN mkdir -p /data && chmod 1777 /data

RUN useradd --create-home --uid 1000 user

USER user
WORKDIR /home/user/app

ENV PATH=/home/user/.local/bin:$PATH

# Dependencies layer (rebuilds only when requirements.txt changes)
COPY --chown=user requirements.txt .
RUN python -m pip install --upgrade pip && \
    python -m pip install -r requirements.txt

COPY --chown=user . .

# HF Spaces: PORT is set at runtime (bot.py defaults to 7860)
EXPOSE 7860

CMD ["python", "bot.py"]
