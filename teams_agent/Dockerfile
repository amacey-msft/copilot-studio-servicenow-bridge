FROM python:3.12-slim

WORKDIR /app

# System deps for cryptography wheels (msal/azure-core).
RUN apt-get update \
    && apt-get install -y --no-install-recommends build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY teams_agent/requirements.txt /app/teams_agent/requirements.txt
RUN pip install --no-cache-dir -r /app/teams_agent/requirements.txt

COPY teams_agent /app/teams_agent

ENV PORT=3978
EXPOSE 3978

CMD ["python", "-m", "teams_agent.app"]
