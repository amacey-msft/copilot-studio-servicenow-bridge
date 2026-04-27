FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    LOG_LEVEL=INFO \
    PORT=3979

WORKDIR /app

# Install deps first for layer caching
COPY teams_skill/requirements.txt /app/teams_skill/requirements.txt
RUN pip install -r /app/teams_skill/requirements.txt

# Copy package
COPY teams_skill/ /app/teams_skill/

EXPOSE 3979

CMD ["python", "-m", "teams_skill.app"]
