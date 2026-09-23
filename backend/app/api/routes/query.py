"""The assistant endpoint: question in, cited answer or refusal out."""

from __future__ import annotations

import logging

from fastapi import APIRouter, Header, HTTPException, Query
from pydantic import BaseModel

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
                # Whatever took the chosen model down — its daily cap, an
                # outage, a timeout — retry once on the system fallback rather
                # than handing the reader a 502. The answer says it came from
                # a substitute (`generator_substituted`).
                retry, replaced = _after_failure(exc, model)
                if not retry:
                    raise
                log.warning("FALLBACK  generator %s failed (%s); retrying on %s",
                            replaced, type(exc).__name__, retry)
                result = answer_question(q, model=retry, memory=before)
                substituted = replaced
            message = to_message(result)

            # The context update is NOT done here. It used to be — between the
            # answer being ready and the answer being sent — so every reader
            # waited for the summariser, a minute at a time when Gemini was
            # slow. The answer goes out now; the client asks for the update
            # separately (POST /api/memory), which collects what is parked here.
            from app.rag import pending_memory

            pending_memory.park(message.id, q, result, before)
            message.memory_pending = True
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
            # Stored with the context the question was asked against; the
            # memory request replaces it with the updated one when it lands.
            message.conversation_id = _persist(
                authorization, payload.conversation_id, q, message, before)
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
            known = ", ".join(sorted(config.selectable()))
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
    if quota.available(config.quota_name(wanted)):
        return alias, None

    fallback = config.FALLBACK_GENERATOR
    if not fallback or fallback == wanted:
        return alias, None
    if not quota.available(config.quota_name(fallback)):
        return alias, None            # both out: let the real error surface
    log.info("generator %s out of quota; using %s", wanted, fallback)
    return fallback, wanted


def _after_failure(exc: Exception, model: str | None) -> tuple[str | None, str | None]:
    """(fallback alias, the alias it replaces) when the generator call failed.

    Any provider failure — quota, an outage, a timeout, a malformed reply — is
    retried on the fallback route. It used to be quota only, so a provider
    having a bad minute reached the reader as a 502. The one exception is an
    unknown model id: that is the caller's mistake, and retrying elsewhere
    would hide it.
    """
    from app.rag import quota

    if _unknown_model(exc):
        return None, None
    wanted = model or config.DEFAULT_GENERATOR
    fallback = config.FALLBACK_GENERATOR
    if not fallback or fallback == wanted:
        return None, None
    if not quota.available(config.quota_name(fallback)):
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


class MemoryRequest(BaseModel):
    """Which answer to fold into the context, and where it is stored."""

    message_id: str
    conversation_id: str | None = None


@router.post("/api/memory", response_model=ConversationMemory)
def update_memory(payload: MemoryRequest, authorization: str | None = Header(None)):
    """The context update for one answer — asked for after the answer is shown.

    Runs the summariser on the question, the answer and the context the
    question was asked against, all parked by `POST /api/query` under the
    message id. Returns the new memory for the client to send with its next
    question; for a signed-in user it also replaces the stored context.

    Separate from the answer on purpose: the summariser is Gemini, which has
    taken 60-90s under load, and no reader should wait for it to see an answer
    that was ready in two. 410 when the answer is no longer parked (expired, or
    the server restarted) — the client then keeps the context it already has.
    """
    from app.rag import memory as mem
    from app.rag import pending_memory

    entry = pending_memory.collect(payload.message_id)
    if entry is None:
        raise HTTPException(410, "that answer is no longer waiting for a context update")
    after = mem.update(entry.before, entry.question, entry.answer)
    result = ConversationMemory(**after.to_dict())

    if payload.conversation_id:
        from app.db import supabase

        token = ""
        if authorization and authorization.lower().startswith("bearer "):
            token = authorization.split(" ", 1)[1]
        user = supabase.user_id(token) if supabase.configured() else None
        if user:
            try:
                from app.db import conversations as store

                store.update_memory(user, payload.conversation_id,
                                    payload.message_id, result.model_dump())
            except Exception:                   # storage is a convenience
                log.exception("storing the context update failed")
    return result


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
            known = ", ".join(sorted(config.selectable()))
            raise HTTPException(422, f"unknown model {model!r} — use one of "
                                     f"{known}, or omit the field") from exc
        log.exception("planning failed")
        raise HTTPException(502, f"planner unavailable: {type(exc).__name__}") from exc

    # make_plan never raises — a planner outage degrades to semantic retrieval
    # and records why in `error`. So a bad model id arrives here as a fallback
    # plan rather than an exception, and has to be caught on the way out.
    if p.error and _unknown_model(Exception(p.error)):
        known = ", ".join(sorted(config.selectable()))
        raise HTTPException(422, f"unknown model {model!r} — use one of "
                                 f"{known}, or omit the field")

    return {
        "question": p.question,
        "category": p.category,
        "operation": p.operation or None,
        # `act` is the first named Act, kept because most coordinates concern
        # one. `acts` is every Act named, which is what a request spanning two
        # of them needs — "the first five sections of the Evidence Act and the
        # CrPC" is one plan, not a choice between them.
        "act": p.act,
        "acts": p.acts,
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
