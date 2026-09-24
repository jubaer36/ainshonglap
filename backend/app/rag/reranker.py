"""Cross-encoder reranking of the retrieved sections (bge-reranker-v2-m3).
"""

from __future__ import annotations

import functools

import torch

from app.rag import config


@functools.lru_cache(maxsize=2)
def load_reranker(name: str | None = None, device: str | None = None):
    """(tokenizer, model, device, scoring convention).

    The convention matters as much as the model. `mxbai-rerank-base-v2` first
    measured -26.8pp — worse than random — purely because a decoder-based
    scorer was read with the encoder convention. A delta that large is a
    harness bug, not a bad model.

      single   one regression logit IS the relevance score (BGE, jina)
      binary   two logits; logit[1] is the RELEVANT class, so the score is
               logit[1] - logit[0]. Reading logit[0] inverts the ranking.
      decoder  a generative head; relevance is the yes/no token logit margin
    """
    from transformers import (AutoModelForCausalLM,
                              AutoModelForSequenceClassification, AutoTokenizer)

    # Defaults are resolved here, not in the signature: scripts override
    # config.RERANK_MODEL at runtime, and a default bound at def time would
    # silently ignore them.
    name = name or config.RERANK_MODEL
    device = device or config.device()
    tok = AutoTokenizer.from_pretrained(name, trust_remote_code=True)
    if tok.pad_token is None:                  # decoder tokenizers often lack one
        tok.pad_token = tok.eos_token or tok.unk_token

    # NOTE: a causal LM loaded through AutoModelForSequenceClassification gets a
    # randomly-initialised head and scores like noise without raising. That is
    # what invalidated E6's Qwen3-Reranker numbers, so check the declared
    # architecture rather than trusting the load to fail.
    from transformers import AutoConfig

    arch = (AutoConfig.from_pretrained(name, trust_remote_code=True).architectures or [""])[0]
    if "CausalLM" in arch:
        model = AutoModelForCausalLM.from_pretrained(
            name, trust_remote_code=True).to(device).eval()
        kind = "decoder"
    else:
        model = AutoModelForSequenceClassification.from_pretrained(
            name, trust_remote_code=True).to(device).eval()
        kind = "single" if getattr(model.config, "num_labels", 1) == 1 else "binary"
        if model.config.pad_token_id is None:
            model.config.pad_token_id = tok.pad_token_id
    return tok, model, device, kind


@torch.no_grad()
def rerank_scores(pairs, batch: int | None = None, max_len: int = 512,
                  model_name: str | None = None) -> list[float]:
    """Relevance score per (query, passage) pair — higher is better."""
    batch = batch or config.RERANK_BATCH
    tok, model, device, kind = load_reranker(model_name or config.RERANK_MODEL)
    out: list[float] = []
    if kind == "decoder":
        yes = tok.encode("yes", add_special_tokens=False)[0]
        no = tok.encode("no", add_special_tokens=False)[0]
    for i in range(0, len(pairs), batch):
        b = pairs[i : i + batch]
        if kind in ("single", "binary"):
            enc = tok([p[0] for p in b], [p[1] for p in b], padding=True,
                      truncation=True, max_length=max_len,
                      return_tensors="pt").to(device)
            logits = model(**enc).logits.float()
            if kind == "binary" and logits.dim() > 1 and logits.shape[-1] >= 2:
                sc = logits[:, 1] - logits[:, 0]
            else:
                sc = logits.view(-1)
            out.extend(sc.cpu().tolist())
        else:
            prompts = [
                "Judge whether the passage answers the query. Answer yes or no.\n"
                f"Query: {q}\nPassage: {d}\nAnswer:"
                for q, d in b
            ]
            enc = tok(prompts, padding=True, truncation=True, max_length=max_len,
                      return_tensors="pt").to(device)
            logits = model(**enc).logits[:, -1, :].float()      # next-token
            out.extend((logits[:, yes] - logits[:, no]).cpu().tolist())
    return out
