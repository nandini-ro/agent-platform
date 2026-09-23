# Backend image: FastAPI + the bundled stdio MCP servers.
#
# Single stage on purpose. Every pinned dependency ships a cp312 manylinux
# wheel, so there is nothing to compile and a builder stage would only add a
# layer to maintain. Garak is deliberately absent - it lives in its own venv
# for development and has no business in a production image.
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY mcp_servers ./mcp_servers
COPY seed.py relink_mcp.py ./

# stdio MCP servers are spawned as child processes of this one, so they inherit
# this user - nothing in the stack wants root. HOME must exist and be writable:
# MCPManager passes it through to every subprocess as a base environment key.
RUN useradd --create-home --uid 10001 appuser \
    && mkdir -p /data \
    && chown -R appuser:appuser /app /data
USER appuser

# The SQLite file lives on a volume mounted here, never in the image layer.
ENV DATABASE_URL=sqlite:////data/chatbot.db

EXPOSE 8000

# No curl in slim, and adding one to run a health check is not worth the bytes.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
