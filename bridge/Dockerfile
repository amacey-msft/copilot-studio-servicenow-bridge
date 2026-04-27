# Build from the repo root so we can include both bridge/ and web/.
# docker-compose.yml does this with `context: ..` and `dockerfile: bridge/Dockerfile`.
FROM python:3.12-slim

WORKDIR /app

COPY bridge/requirements.txt /app/bridge/
RUN pip install --no-cache-dir -r /app/bridge/requirements.txt \
 && pip install --no-cache-dir gevent gevent-websocket

COPY bridge/ /app/bridge/
COPY web/    /app/web/

WORKDIR /app/bridge

ENV PYTHONPATH=/app
ENV PORT=5000
EXPOSE 5000

# flask-sock requires a worker that supports websockets; use gevent.
CMD ["gunicorn", "-k", "geventwebsocket.gunicorn.workers.GeventWebSocketWorker", \
     "-w", "1", "-b", "0.0.0.0:5000", "app:app", "--timeout", "120"]
