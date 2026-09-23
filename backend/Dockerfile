FROM python:3.12-slim-bookworm

# No model runs in this container: retrieval (nemotron-3-embed) is on
# OpenRouter and generation on Groq/Gemini, so there is no PyTorch, no model
# weights and no warm-up. The image holds the API, its Python dependencies, and
# the local read models it falls back to when Supabase cannot answer.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /workspace/backend

# Dependency layer stays cached when only application code changes.
COPY backend/requirements.docker.txt ./requirements.docker.txt
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.docker.txt \
    && find /usr/local/lib/python3.12/site-packages -depth -type d -name tests \
         -exec rm -rf {} +

# Preserve the repository layout expected by app/rag/config.py:
# /workspace/backend and /workspace/Laws/main.
COPY backend/app ./app
COPY Laws/main/catalog.sqlite /workspace/Laws/main/catalog.sqlite
COPY Laws/main/parsed /workspace/Laws/main/parsed
# Local fallback for meaning search: the chunk records and their nemotron-3-embed
# vectors (built with `python scripts/build_index.py`).
COPY Laws/main/index/chunks.json /workspace/Laws/main/index/chunks.json
COPY Laws/main/index/embeddings.nemotron-3-embed.npy /workspace/Laws/main/index/embeddings.nemotron-3-embed.npy
COPY Laws/main/index/meta.nemotron-3-embed.json /workspace/Laws/main/index/meta.nemotron-3-embed.json

# Retrieval profile (E21) — override at deploy time with RAG_RETRIEVAL:
#   nemotron-embed          search only (default)
#   nemotron-embed+rerank   + the Nemotron reranker, a second request a question
# Each question costs OpenRouter requests against the free-model daily cap.
# When OpenRouter cannot serve, meaning search answers 503 with a plain message;
# exact section lookups still work. Source management is not available in this
# image (no scripts/, PyMuPDF or Docling): /api/sources reports it and refuses.
ENV RAG_RETRIEVAL=nemotron-embed

EXPOSE 8080

# One worker, on purpose: answers waiting for their context update
# (pending_memory), quota records and key rotation live in process memory, and a
# second worker would not see them — /api/memory would answer 410.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
