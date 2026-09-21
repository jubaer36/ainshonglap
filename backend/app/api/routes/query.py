"""The assistant endpoint: question in, cited answer or refusal out."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Query

from app.models.schemas import AssistantMessage, ConversationMemory, QueryRequest
from app.rag import config, trace
from app.rag.present import to_message

log = logging.getLogger(__name__)
router = APIRouter(tags=["assistant"])


# Deliberately a plain `def`, not `async def`.
#
# FastAPI runs an `async def` handler ON the event loop, so any blocking call
# inside it freezes the whole server for its duration. This handler blocks for
# seconds at a time — cross-encoder inference on the GPU, then a synchronous
# HTTPS call to the generator — and while it did, every other request was
# stuck behind it. The visible symptom was the model picker reporting
# "unavailable": its 15s fetch timeout expired waiting for an event loop that
# was busy answering someone's question.
#
# A plain `def` is handed to a threadpool instead, so slow work no longer
# starves fast requests.
@router.post("/api/query", response_model=AssistantMessage)
def query(payload: QueryRequest,
          authorization: str | None = Header(None)) -> AssistantMessage:
    """Plan, execute, verify citations, present.

    Two paths, chosen by `RAG_PLAN`:

      on   a model plans the question into a category and a set of coordinates,
           and validated code executes it (`app/rag/plan.py`, `execute.py`)
      off  the older pattern-first router (`app/rag/refine.py`)

    The switch exists so the planner can be turned off in one place if a Groq
    outage makes planning unavailable — not as a permanent fork. The old router
    goes once the planner has run in production for a while.

    A refusal is a 200 with `unsupported=true`, not an error: "the Acts do not
    cover this" is an answer, and the frontend renders it as one. A 502 means
    the generator itself was unreachable — a different failure, and retryable.
    """
    q = payload.question.strip()
    if not q:
        raise HTTPException(422, "question is empty")
    model = (payload.model or "").strip() or None
    trace.question(q, user=bool(authorization))

    # A model known to be out of quota is swapped before the call rather than
    # after a failed one: the caller gets an answer instead of a 502, and the
    # response says which route actually produced it. Silently substituting
    # without saying so would be worse than failing.
    model, substituted = _usable(model)

    try:
        if config.PLAN_QUERIES:
            from app.rag import memory as mem
            from app.rag.execute import answer_question

            before = mem.Memory.from_dict(
                payload.memory.model_dump() if payload.memory else None)
            try:
                result = answer_question(q, model=model, memory=before)
            except Exception as exc:
                # The FIRST request to hit a model's daily cap discovers it the
                # hard way: nothing was known to be blocked, so the call went
                # out and failed. By now `quota` has recorded it, so retrying
                # once on the fallback turns that request into an answer rather
                # than making one unlucky user eat the 502.
                retry, replaced = _after_failure(exc, model)
                if not retry:
                    raise
                log.info("retrying on %s after quota failure on %s", retry, replaced)
                result = answer_question(q, model=retry, memory=before)
                substituted = replaced
            message = to_message(result)

            # Summarise AFTER answering, never before: a summariser outage must
            # cost the conversation its memory, not cost the user their answer.
            after = mem.update(before, q, result)
            message.memory = ConversationMemory(**after.to_dict())
            plan = result.plan
            if plan is not None and getattr(plan, "follow_up", False):
                message.resolved_question = plan.resolved_question
            message.generator = result.model or None
            message.generator_substituted = substituted

            # A pasted document answered on a smaller model: say so and offer a
            # bigger one. Suggested rather than applied, because the reader
            # chose the model and an answer that silently came from a different
            # one is the problem the substitution banner exists to avoid.
            if plan is not None and getattr(plan, "category", "") == "document":
                bigger = config.suggests_larger_model(model)
                if bigger:
                    message.suggested_model = bigger
                    message.notice = (
                        f"That looks like a pasted document ({len(q):,} characters). "
                        f"It was answered with {message.generator or 'the default model'}, "
                        f"which is tuned for short questions. A larger model such as "
                        f"{bigger} handles long passages better — you can ask again with it.")

            # Persist only for a signed-in user. A guest's questions are never
            # written anywhere — that is what they chose on the way in, and the
            # absence of a token is the whole enforcement.
            message.conversation_id = _persist(
                authorization, payload.conversation_id, q, message, after)
            return message

        from app.rag.answer import answer

        return to_message(answer(q, model=model, refine_query=payload.refine))
    except Exception as exc:
        # A model the provider does not have is the caller's mistake, not an
        # outage, and 502 "generator unavailable" sends whoever sees it looking
        # for a broken service. Unknown ids are allowed through to the provider
        # on purpose (config.resolve_generator), so this is where a typo
        # surfaces — including Swagger UI's own "string" placeholder.
        if _unknown_model(exc):
            known = ", ".join(sorted(config.GENERATORS))
            raise HTTPException(
                422, f"unknown model {model!r} — use an alias from "
                     f"GET /api/models ({known}), a 'backend:model' pair, or "
                     f"omit the field for the server default") from exc
        log.exception("generation failed")
        raise HTTPException(502, f"generator unavailable: {type(exc).__name__}") from exc


def _persist(authorization: str | None, conversation_id: str | None,
             question: str, message: AssistantMessage, memory) -> str | None:
    """Store one exchange, when there is a verified user to store it for.

    Never raises: storage is a convenience, and losing it must not cost someone
    the answer they already have. A failure is logged and the response goes out
    without a conversation id.
    """
    from app.db import supabase

    if not supabase.configured():
        return None
    token = ""
    if authorization and authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1]
    user = supabase.user_id(token)
    if not user:
        return None                     # guest: nothing is written, by design

    try:
        from app.db import conversations as store

        saved = store.save_turn(
            user, conversation_id,
            {"id": f"q-{message.id}", "role": "user", "text": question},
            message.model_dump(mode="json"),
            memory.to_dict() if memory else None)
        trace.step("saved", str(saved)[:8])
        return saved
    except Exception:                               # pragma: no cover - defensive
        log.exception("saving the conversation failed")
        return None


def _usable(alias: str | None) -> tuple[str | None, str | None]:
    """(model to use, the alias it replaced).

    Returns the request's own choice untouched when it is available, or when
    nothing is configured to fall back to.
    """
    from app.rag import quota

    wanted = alias or config.DEFAULT_GENERATOR
    _, model_id = config.resolve_generator(wanted)
    if quota.available(model_id):
        return alias, None

    fallback = config.FALLBACK_GENERATOR
    if not fallback or fallback == wanted:
        return alias, None
    _, fallback_id = config.resolve_generator(fallback)
    if not quota.available(fallback_id):
        return alias, None            # both out: let the real error surface
    log.info("generator %s out of quota; using %s", wanted, fallback)
    return fallback, wanted


def _after_failure(exc: Exception, model: str | None) -> tuple[str | None, str | None]:
    """(fallback alias, the alias it replaces) when a call died on quota."""
    from app.rag import quota

    if not quota.looks_like_quota(str(exc)):
        return None, None
    wanted = model or config.DEFAULT_GENERATOR
    fallback = config.FALLBACK_GENERATOR
    if not fallback or fallback == wanted:
        return None, None
    _, fallback_id = config.resolve_generator(fallback)
    if not quota.available(fallback_id):
        return None, None
    return fallback, wanted


def _unknown_model(exc: Exception) -> bool:
    """Did the provider reject the model id itself?

    Matched on the provider's own wording rather than on the HTTP status: a 404
    from these endpoints is always about the model, but the message is what
    distinguishes "no such model" from "no access to this model", and both are
    the caller's problem rather than ours.
    """
    text = str(exc).lower()
    return "model_not_found" in text or "does not exist or you do not have access" in text


@router.get("/api/plan", tags=["assistant"])
def plan_only(q: str = Query(..., min_length=1),
                    model: str | None = None) -> dict:
    """What the planner decided, without answering.

    The planner picks the machine that answers every question, so when an answer
    looks wrong the first thing worth seeing is whether it was *planned* wrong.
    This returns that, and `notes` records everything validation changed — a
    chapter renumbered to the Act's own spelling, a coordinate rejected for not
    existing. Read-only and cheap: one small model call, no retrieval, no
    generation.
    """
    from app.rag.plan import make_plan

    model = (model or "").strip() or None
    try:
        p = make_plan(q.strip(), model=model)
    except Exception as exc:                        # pragma: no cover - defensive
        if _unknown_model(exc):
            known = ", ".join(sorted(config.GENERATORS))
            raise HTTPException(422, f"unknown model {model!r} — use one of "
                                     f"{known}, or omit the field") from exc
        log.exception("planning failed")
        raise HTTPException(502, f"planner unavailable: {type(exc).__name__}") from exc

    # make_plan never raises — a planner outage degrades to semantic retrieval
    # and records why in `error`. So a bad model id arrives here as a fallback
    # plan rather than an exception, and has to be caught on the way out.
    if p.error and _unknown_model(Exception(p.error)):
        known = ", ".join(sorted(config.GENERATORS))
        raise HTTPException(422, f"unknown model {model!r} — use one of "
                                 f"{known}, or omit the field")

    return {
        "question": p.question,
        "category": p.category,
        "operation": p.operation or None,
        "act": p.act,
        "chapter": p.chapter,
        "sections": p.sections,
        "range": ([p.section_from, p.section_to]
                  if p.section_from and p.section_to else None),
        "search_query": p.search_query or None,
        "terms": p.terms,
        "needs_clarification": p.needs_clarification,
        "clarification": p.clarification or None,
        # How the plan was produced and what validation did to it. `by` is
        # "fallback" when the planner call failed and the question degraded to
        # plain semantic retrieval.
        "by": p.by,
        "notes": p.notes,
        "error": p.error,
    }
