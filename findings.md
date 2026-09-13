Biggest finding: generator model matter less than you think
Legal RAG Bench (4,876 passages Victorian Criminal Charge Book, 100 hand-crafted criminal-law/procedure questions — closest published benchmark to your exact usecase) found:

Embedding model is primary driver. Swapping general embedder (OpenAI text-embedding-3-large) → legal-domain embedder (Kanon 2) gained ~19 accuracy points.
Frontier generators (Gemini 3.1 Pro vs GPT-5.2) differed only moderately on correctness/groundedness.
"Hallucinations often triggered by retrieval failures. Fix retrieval, you fix hallucinations."
bLLeQA adds: open-weight models competitive on retrieval + citation extraction, lag on generation quality. And refusal capability weak across all models — matters for your "unsupported question" requirement. Even best setup ~20% flawed responses.

So: spend budget on embedder + reranker. Generator can be cheap.

Recommended stack
Layer	Pick	Cost
Embedder (dev)	BAAI/bge-m3 local, or Gemini gemini-embedding	free
Embedder (upgrade experiment)	Voyage voyage-law-2 / voyage-3-large	cheap, legal-tuned
Reranker	BAAI/bge-reranker-v2-m3 (Apache 2.0, 0.6B, ~12ms/pair)	free, local
Generator (dev loop)	Gemini 2.5 Flash-Lite or Groq Llama 3.3 70B	free tier
Generator (demo/final)	Gemini 2.5 Flash-Lite paid, or Claude Haiku 4.5	~$0.001–0.01/query
Eval judge	stronger model (Claude Sonnet 5 / Gemini Pro)	only on eval set
Avoid jina-reranker-v2-base-multilingual — CC-BY-NC-4.0, non-commercial.

Free tiers
Provider	Models	Limits	Catch
Google AI Studio	Gemini 2.5 Flash-Lite	15 RPM, 1000 RPD	prompts train Google (non-EU/UK)
Gemini 2.5 Flash	10 RPM, 250 RPD	Pro removed from free Apr 2026
gemini-embedding	10M TPM	free — embeds your whole corpus instantly
Groq	Llama 3.3 70B	30 RPM, 1000 RPD, 100k TPD	fastest inference; 100k TPD is the real ceiling
Cerebras	Llama 3.3 70B	30 RPM, ~1M tok/day	good for batch eval runs
Mistral	Small/Large	~1B tok/month	must opt into data training
OpenRouter	20+	20 RPM, 50 RPD (1000 if $10 credit)	good for cross-model A/B
GitHub Models	GPT-4o, Claude 3.5 Sonnet, Llama	15 RPM, 150–1000 RPD	GitHub account only
Groq 100k tokens/day is tight: ~15 RAG queries/day at 6k tokens each. Gemini free tier is the better dev driver.
