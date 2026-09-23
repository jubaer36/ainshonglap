FROM python:3.12-slim-bookworm

# Cloud Run is CPU-only for this service. Keep the Hugging Face cache in a
# known image layer so a cold instance loads locally instead of downloading
# roughly 4.5 GB of model weights.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    HF_HOME=/opt/huggingface \
    TOKENIZERS_PARALLELISM=false

# FAISS and PyTorch use OpenMP at runtime. All Python dependencies have Linux
# wheels, so no compiler toolchain is needed in the final image.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /workspace/backend

# Dependency and model layers stay cached when only application code changes.
COPY backend/requirements.docker.txt ./requirements.docker.txt
# Pruned in the SAME layer, or the files would still ship in the one below:
# packages' own test suites (~145 MB) and PyTorch's C++ headers (62 MB), used
# only to compile extensions. __pycache__ stays — without it every cold start
# recompiles the imports.
RUN python -m pip install --upgrade pip \
    && python -m pip install -r requirements.docker.txt \
    && find /usr/local/lib/python3.12/site-packages -depth -type d -name tests \
         -exec rm -rf {} + \
    && rm -rf /usr/local/lib/python3.12/site-packages/torch/include

# Model weights, downloaded file by file so exactly one copy of each ships:
#   bge-m3                 pytorch_model.bin (the only format its repo has)
#   bge-reranker-v2-m3     model.safetensors
# plus configs and tokenizers. Not from_pretrained: loading a .bin from the Hub
# makes Transformers fetch an auto-converted model.safetensors in a background
# thread — a second 2.3 GB copy of BGE-M3 that offline loading never even finds
# (measured: 12.7 GB image). Also skipped: bge-m3's ONNX export and its
# ColBERT/sparse heads, which the API does not use.
# The same step then loads both OFFLINE, exactly as the API will, so a missing
# file fails the build here rather than the first question in production. An
# optional BuildKit secret `hf_token` raises Hugging Face's download limits
# without being stored in the layer.
RUN --mount=type=secret,id=hf_token,required=false python - <<'PY'
from pathlib import Path
from huggingface_hub import snapshot_download

secret = Path("/run/secrets/hf_token")
# Empty when HF_TOKEN is unset: the secret file still exists, and an empty
# token would be sent as the header "Bearer " and refused.
token = (secret.read_text().strip() if secret.exists() else "") or None
small = ["*.json", "*.model"]
snapshot_download("BAAI/bge-m3", token=token, ignore_patterns=["onnx/*"],
                  allow_patterns=small + ["pytorch_model.bin"])
snapshot_download("BAAI/bge-reranker-v2-m3", token=token,
                  allow_patterns=small + ["model.safetensors"])
PY
RUN HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1 python -c "from transformers import AutoConfig, AutoModel, AutoModelForSequenceClassification, AutoTokenizer; e='BAAI/bge-m3'; r='BAAI/bge-reranker-v2-m3'; AutoTokenizer.from_pretrained(e); AutoModel.from_pretrained(e); AutoConfig.from_pretrained(r, trust_remote_code=True); AutoTokenizer.from_pretrained(r, trust_remote_code=True); AutoModelForSequenceClassification.from_pretrained(r, trust_remote_code=True); print('both models load offline')"

# Preserve the repository layout expected by app/rag/config.py:
# /workspace/backend and /workspace/Laws/main.
COPY backend/app ./app
COPY Laws/main/catalog.sqlite /workspace/Laws/main/catalog.sqlite
COPY Laws/main/parsed /workspace/Laws/main/parsed
COPY Laws/main/index/chunks.faiss /workspace/Laws/main/index/chunks.faiss
COPY Laws/main/index/chunks.json /workspace/Laws/main/index/chunks.json
COPY Laws/main/index/meta.json /workspace/Laws/main/index/meta.json
# nemotron-3-embed vectors (E21), for the `nemotron-embed` retrieval profile when
# Supabase cannot serve law_alt_chunks. Build them first if missing:
#   python scripts/build_index.py --embedder nemotron-3-embed
COPY Laws/main/index/embeddings.nemotron-3-embed.npy /workspace/Laws/main/index/embeddings.nemotron-3-embed.npy
COPY Laws/main/index/meta.nemotron-3-embed.json /workspace/Laws/main/index/meta.nemotron-3-embed.json

ENV HF_HUB_OFFLINE=1 \
    TRANSFORMERS_OFFLINE=1

# Retrieval profile (E21) — override at deploy time with RAG_RETRIEVAL.
#   bge+bge-rerank       all local: BGE-M3 + reranking 20 sections on this CPU
#   bge+nemotron-rerank  BGE-M3 local, reranking on OpenRouter
#   nemotron-embed       search on OpenRouter, no local model work per question
# The OpenRouter two cost one request per question against the free-model daily
# cap and fall back to bge+bge-rerank, which is why both local models stay baked
# in whichever is chosen. Source management is not available in this image
# (no scripts/, PyMuPDF or Docling): /api/sources reports it and refuses with 503.
ENV RAG_RETRIEVAL=bge+bge-rerank

EXPOSE 8080

# One worker is intentional: every worker would load another copy of both
# transformer models into memory.
CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8080} --workers 1"]
