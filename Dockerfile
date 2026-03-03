# ── ShadowDev Agentic IDE — Production Dockerfile ──────────────
# Multi-stage build: keeps final image lean (~400MB vs ~1GB)
#
# Build:  docker build -t shadowdev .
# Run:    docker run -p 8000:8000 -e LLM_PROVIDER=openai -e OPENAI_API_KEY=sk-... shadowdev
# ──────────────────────────────────────────────────────────────

# Stage 1: Builder — install Python deps
FROM python:3.12-slim AS builder

WORKDIR /app

# Install build tools needed by some packages (chromadb, aiosqlite)
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        git \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt \
 && pip install --no-cache-dir pyright


# Stage 2: Runtime — lean final image
FROM python:3.12-slim AS runtime

WORKDIR /app

# Install runtime-only system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ripgrep \
    && rm -rf /var/lib/apt/lists/*

# Copy installed packages from builder
COPY --from=builder /usr/local/lib/python3.12 /usr/local/lib/python3.12
COPY --from=builder /usr/local/bin /usr/local/bin

# Copy application source
COPY . .

# Create data and workspace directories with correct permissions
RUN mkdir -p /app/data /app/workspace \
 && chmod 777 /app/data /app/workspace

# Expose port
EXPOSE 8000

# Default environment (override via docker run -e or .env mount)
ENV LLM_PROVIDER=ollama \
    OLLAMA_BASE_URL=http://host.docker.internal:11434 \
    OLLAMA_MODEL=qwen2.5-coder:14b \
    HOST=0.0.0.0 \
    PORT=8000 \
    WORKSPACE_DIR=/app/workspace \
    VECTOR_BACKEND=chroma

# Health check
HEALTHCHECK --interval=30s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/api/health')"

# Start the server
CMD ["python", "-m", "server.main"]
