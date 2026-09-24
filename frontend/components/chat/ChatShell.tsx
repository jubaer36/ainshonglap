"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, fetchSection, sendQuery, updateMemory } from "@/lib/api";
import {
  deleteConversation,
  nameConversation,
  renameConversation,
  getConversation,
  listConversations,
  newConversationId,
  type SavedConversation,
} from "@/lib/conversations";
import { useAuth } from "@/components/auth/AuthProvider";
import { WelcomeGate } from "@/components/auth/WelcomeGate";
import type { AssistantMessage, ChatMessage, Citation, UserMessage, ConversationMemory } from "@/lib/types";
import { useMediaQuery } from "@/lib/useMediaQuery";
import { Header } from "../layout/Header";
import { ReferencesDrawer } from "../layout/ReferencesDrawer";
import { Sidebar } from "../layout/Sidebar";
import { FirstTimeNotice } from "./FirstTimeNotice";
import { InputDock } from "./InputDock";
import { MessageAssistant } from "./MessageAssistant";
import { disclaimerMessageId } from "@/lib/disclaimer";
import { MessageUser } from "./MessageUser";
import { SuggestedQuestions } from "./SuggestedQuestions";
import { ThinkingBubble } from "./ThinkingBubble";

function newId(): string {
  return typeof crypto !== "undefined" && "randomUUID" in crypto
    ? crypto.randomUUID()
    : `${Date.now()}-${Math.random().toString(36).slice(2)}`;
}

/**
 * Failure messages are split by cause because they call for different actions.
 * A refusal is NOT one of these — the backend returns that as a normal 200 with
 * `unsupported: true`, and it is an answer, not an error.
 */
function errorAssistantMessage(error: unknown, question?: string): AssistantMessage {
  let subheading = "Connection issue";
  let text =
    "Couldn't reach the backend API. Confirm it's running (see backend/README.md) and try again.";

  if (error instanceof DOMException && error.name === "AbortError") {
    subheading = "Timed out";
    text =
      "The answer took too long. Reasoning models can be slow — try a faster one from the model picker, or ask again.";
  } else if (error instanceof ApiError) {
    if (error.status === 502) {
      subheading = "Generator unavailable";
      text =
        "Retrieval worked, but the language model could not be reached — often a rate limit on a free route. Try another model, or retry shortly.";
    } else if (error.status === 422) {
      subheading = "Empty question";
      text = "Type a question before sending.";
    } else {
      subheading = `API error ${error.status}`;
      text = "The backend rejected the request. Check its logs for the cause.";
    }
  }

  return {
    id: newId(),
    role: "assistant",
    heading: "আইনসংলাপ Plain Legal Guide",
    subheading,
    // A transport failure renders like a refusal — amber, "can't answer". It is
    // not out_of_scope (we never reached the corpus) and not clarify (there is
    // nothing for the reader to disambiguate).
    kind: "refused",
    unsupported: true,
    memory: null,
    resolvedQuestion: null,
    generator: null,
    generatorSubstituted: null,
    retryQuestion: question ?? null,
    conversationId: null,
    notice: null,
    suggestedModel: null,
    blocks: [{ type: "paragraph", segments: [{ type: "text", text }] }],
    citations: {},
    references: [],
    quickActions: [],
  };
}

/** Remembers only that the welcome screen was answered — never any content. */
const GUEST_CHOICE_KEY = "ainshonglap.guest";

// How long a new question waits for the previous answer's context update
// before going ahead with the context it has.
const CONTEXT_WAIT_MS = 25_000;

// Where each side panel stops overlaying the chat and becomes a column. The
// references drawer needs a wider screen than the sidebar: with both docked at
// 1024px the chat was left about 350px wide.
const SIDEBAR_DOCKS = "(min-width: 1024px)";
const DRAWER_DOCKS = "(min-width: 1280px)";

/**
 * A panel the reader opened or closed, and the layout they did it in.
 *
 * Until they touch it, a panel follows the screen: docked and open on a wide
 * one, collapsed on a phone — decided by CSS alone, so a phone never flashes an
 * open panel before the script runs. A choice made in one layout is dropped
 * when the screen crosses into the other (a window narrowed, a tablet rotated):
 * a sidebar opened on a laptop has no business staying open over a phone-width
 * chat.
 */
type PanelChoice = { open: boolean; docked: boolean } | null;

export function ChatShell() {
  const { user, ready: authReady, configured: authConfigured } = useAuth();
  const [guest, setGuest] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // The rolling conversation summary. Lives here rather than on the server:
  // these are personal legal questions, and keeping nothing server-side is a
  // privacy property. Cleared by "New chat", which is what makes that button
  // actually start a new conversation rather than just empty the screen.
  const [memory, setMemoryState] = useState<ConversationMemory | null>(null);
  // The memory as of right now, readable inside an async flow. A question that
  // waits for a context update must send the memory that update produced, not
  // the value captured when Enter was pressed.
  const memoryRef = useRef<ConversationMemory | null>(null);
  const setMemory = (next: ConversationMemory | null) => {
    memoryRef.current = next;
    setMemoryState(next);
  };
  // The context update runs AFTER an answer is shown, as its own request. The
  // answer never waits for it; only the NEXT question might — briefly.
  // `contextGen` numbers each update, so one that finishes after it stopped
  // mattering (the question went ahead without it, or the conversation
  // changed) is recognised as stale and never applied.
  const pendingContext = useRef<Promise<ConversationMemory | null> | null>(null);
  const contextGen = useRef(0);
  const [contextUpdating, setContextUpdating] = useState(false);
  const [waitingForContext, setWaitingForContext] = useState(false);
  // Saved conversations live in localStorage (see lib/conversations.ts): these
  // are people's legal questions, and a server that never receives them cannot
  // mishandle them. `conversationId` is allocated up front so the first answer
  // lands in a record rather than creating one retroactively.
  const [conversations, setConversations] = useState<SavedConversation[]>([]);
  // Conversations whose title is settled — named, or already the reader's.
  // Only those stop being asked: asking after every answer until then is one
  // small request, and stopping earlier is how naming never happened at all.
  const namingSettled = useRef<Set<string>>(new Set());
  const [conversationId, setConversationId] = useState<string>(() => newConversationId());
  // The server's id for this conversation, once it has stored one. Distinct
  // from `conversationId`, which is a local key that also exists for guests.
  const [storedId, setStoredId] = useState<string | null>(null);
  const [inputValue, setInputValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [noticeDismissed, setNoticeDismissed] = useState(false);
  const sidebarDocked = useMediaQuery(SIDEBAR_DOCKS);
  const drawerDocked = useMediaQuery(DRAWER_DOCKS);
  const [sidebarChoice, setSidebarChoice] = useState<PanelChoice>(null);
  const [drawerChoice, setDrawerChoice] = useState<PanelChoice>(null);
  // null = no choice that applies to this layout, so the screen decides.
  const sidebarPick = sidebarChoice?.docked === sidebarDocked ? sidebarChoice.open : null;
  const drawerPick = drawerChoice?.docked === drawerDocked ? drawerChoice.open : null;
  const sidebarOpen = sidebarPick ?? sidebarDocked;
  const drawerOpen = drawerPick ?? drawerDocked;
  const setSidebarOpen = (open: boolean) => setSidebarChoice({ open, docked: sidebarDocked });
  const setDrawerOpen = (open: boolean) => setDrawerChoice({ open, docked: drawerDocked });
  const [citationLibrary, setCitationLibrary] = useState<Record<string, Citation>>({});
  const [activeCitationKey, setActiveCitationKey] = useState<string | null>(null);
  const [model, setModel] = useState<string | null>(null);

  useEffect(() => {
    // Sections are fetched one at a time, when something asks to see one. The
    // previous version pulled the whole citation library on mount, which was
    // fine for five canned entries and is not for ~1,300 real sections.
    if (!activeCitationKey || citationLibrary[activeCitationKey]) return;
    let cancelled = false;
    fetchSection(activeCitationKey)
      .then((section) => {
        if (cancelled) return;
        setCitationLibrary((current) => ({
          ...current,
          [section.key]: {
            key: section.key,
            title: `${section.act} Section ${section.section}${
              section.sectionTitle ? ` • ${section.sectionTitle}` : ""
            }`,
            badge: section.actNo ? `${section.act} ${section.actNo}` : section.act,
            source: section.sourcePdf
              ? `${section.sourcePdf}${section.pageStart ? ` • p. ${section.pageStart}` : ""}`
              : section.act,
            text: section.text,
            note: section.chapterTitle ?? section.chapter ?? "",
          },
        }));
      })
      .catch(() => {
        // Nothing to preview; the drawer shows its empty state.
      });
    return () => {
      cancelled = true;
    };
  }, [activeCitationKey, citationLibrary]);

  useEffect(() => {
    // The guest choice survives a reload so the gate does not reappear every
    // time. It is a single boolean — no questions, no answers, nothing about
    // what was asked.
    try {
      const chosen =
        window.sessionStorage.getItem(GUEST_CHOICE_KEY) === "1" ||
        new URLSearchParams(window.location.search).get("guest") === "1";
      if (chosen) {
        setGuest(true);
        window.sessionStorage.setItem(GUEST_CHOICE_KEY, "1");
      }
    } catch {
      /* storage blocked: the gate simply shows again */
    }
  }, []);

  useEffect(() => {
    // Signed in: read history from the API. Guest: there is nothing to read.
    if (!user) {
      setConversations([]);
      return;
    }
    let cancelled = false;
    listConversations().then((list) => !cancelled && setConversations(list));
    return () => {
      cancelled = true;
    };
  }, [user]);

  /**
   * Ask a question. `retryOf` re-runs one that failed.
   *
   * A retry drops the failed answer and re-sends the same question against the
   * memory as it was BEFORE that attempt. Both halves matter: appending a second
   * copy of the question would make the transcript lie about what was asked, and
   * carrying memory forward from a failed attempt would feed the next one
   * whatever the failure left behind.
   */
  async function submitQuestion(question: string, retryOf?: string, withModel?: string) {
    const trimmed = question.trim();
    if (!trimmed || loading) return;

    let historyForRequest: ChatMessage[];
    if (retryOf) {
      // Cut the failed answer and everything after it, keeping the question.
      const index = messages.findIndex((m) => m.id === retryOf);
      historyForRequest = index === -1 ? messages : messages.slice(0, index);
    } else {
      historyForRequest = [...messages, { id: newId(), role: "user", text: trimmed }];
      setInputValue("");
    }
    setMessages(historyForRequest);
    setLoading(true);

    // The previous answer's context may still be updating. Wait for it — up to
    // CONTEXT_WAIT_MS — so a follow-up is read against the exchange it follows.
    // Past that, go ahead with the context as it stands and say so under the
    // question; the late update is then discarded rather than applied over
    // whatever comes next.
    const pending = pendingContext.current;
    if (pending) {
      setWaitingForContext(true);
      const outcome = await Promise.race([
        pending.then((m) => (m ? "updated" : "failed")),
        new Promise<"timeout">((resolve) => setTimeout(() => resolve("timeout"), CONTEXT_WAIT_MS)),
      ]);
      setWaitingForContext(false);
      if (outcome !== "updated") {
        dropPendingContext();
        const note = outcome === "timeout"
          ? "Sent with the earlier context — updating it from your last answer took too long."
          : "Sent with the earlier context — it could not be updated from your last answer.";
        historyForRequest = historyForRequest.map((m, i) =>
          i === historyForRequest.length - 1 && m.role === "user" ? { ...m, contextNote: note } : m);
        setMessages(historyForRequest);
      }
    }

    try {
      const assistantMessage = await sendQuery(trimmed, historyForRequest, {
        model: withModel ?? model ?? undefined,
        memory: memoryRef.current,
        // Null on the first turn; the server returns the id it stored under.
        conversationId: user ? storedId : null,
      });
      const nextMessages = [...historyForRequest, assistantMessage];
      setMessages(nextMessages);
      // An older server still returns the updated memory with the answer.
      if (assistantMessage.memory) setMemory(assistantMessage.memory);
      // Storage happens server-side inside /api/query for a signed-in user, so
      // nothing is written here. Refresh the list so a new conversation appears
      // in the sidebar as soon as it has a title.
      if (assistantMessage.conversationId) setStoredId(assistantMessage.conversationId);
      if (user) void listConversations().then(setConversations);
      setCitationLibrary((current) => ({ ...current, ...assistantMessage.citations }));
      // The answer is on screen; now update the context, on its own.
      if (assistantMessage.memoryPending) {
        startContextUpdate(assistantMessage.id, assistantMessage.conversationId ?? null);
      }
    } catch (error) {
      // A transport failure never reaches the summariser, so memory is
      // untouched here by construction — the next attempt starts from the same
      // context this one did.
      setMessages((current) => [...current, errorAssistantMessage(error, trimmed)]);
    } finally {
      setLoading(false);
    }
  }

  /** Update the context from one answer, without anyone waiting on it. */
  function startContextUpdate(messageId: string, storedConversationId: string | null) {
    const gen = ++contextGen.current;
    setContextUpdating(true);
    const promise = updateMemory(messageId, user ? storedConversationId : null)
      .catch(() => null)
      .then((next) => {
        if (gen !== contextGen.current) return null;      // stale: never applied
        if (next) setMemory(next);
        pendingContext.current = null;
        setContextUpdating(false);
        return next;
      });
    pendingContext.current = promise;

    // Naming reads the summary, so it follows the update. Asked after every
    // update until the server says the title is settled ("named" or "kept");
    // "not_yet" and "failed" leave it for the next turn.
    void promise.then((next) => {
      const id = storedConversationId;
      if (!next || !user || !id || namingSettled.current.has(id)) return;
      void nameConversation(id, next).then(({ status, list }) => {
        if (status === "named" || status === "kept") namingSettled.current.add(id);
        if (list) setConversations(list);
      });
    });
  }

  /** Forget an update in flight — the conversation it belonged to has moved on. */
  function dropPendingContext() {
    contextGen.current++;
    pendingContext.current = null;
    setContextUpdating(false);
  }

  function handleRetry(messageId: string, withModel?: string) {
    const index = messages.findIndex((m) => m.id === messageId);
    if (index <= 0) return;
    const asked = messages[index - 1];
    if (asked.role !== "user") return;
    // Switching the picker too, so the choice sticks for the rest of the
    // conversation rather than applying to one answer and silently reverting.
    if (withModel) setModel(withModel);
    void submitQuestion(asked.text, messageId, withModel);
  }

  function handleNewChat() {
    setConversationId(newConversationId());
    setStoredId(null);
    setMessages([]);
    dropPendingContext();
    setMemory(null);
    setInputValue("");
    setActiveCitationKey(null);
    if (!sidebarDocked) setSidebarOpen(false);
  }

  async function handleOpenConversation(id: string) {
    const saved = await getConversation(id);
    if (!saved) return;
    setConversationId(saved.id);
    setStoredId(saved.id);
    setMessages(saved.messages);
    dropPendingContext();
    setMemory(saved.memory);
    setInputValue("");
    setActiveCitationKey(null);
    // Citations from the reopened conversation, so its pills resolve without
    // re-fetching every section.
    const restored: Record<string, Citation> = {};
    for (const message of saved.messages) {
      if (message.role === "assistant") Object.assign(restored, message.citations);
    }
    setCitationLibrary((current) => ({ ...current, ...restored }));
    if (!sidebarDocked) setSidebarOpen(false);
  }

  async function handleRenameConversation(id: string, title: string) {
    // The server normalises the title (trimmed, capped, blank -> default), so
    // the refreshed list is the truth rather than the string typed here.
    setConversations(await renameConversation(id, title));
  }

  async function handleDeleteConversation(id: string) {
    setConversations(await deleteConversation(id));
    // Deleting the conversation on screen leaves it open but detached; start a
    // fresh one so the view matches what is stored.
    if (id === conversationId) handleNewChat();
  }

  function handleActivateCitation(key: string) {
    setActiveCitationKey(key);
    setDrawerOpen(true);
    if (!sidebarDocked) setSidebarOpen(false);
  }

  const latestAssistantMessage = [...messages]
    .reverse()
    .find((message): message is AssistantMessage => message.role === "assistant");
  const currentReferences = latestAssistantMessage?.references ?? [];
  const activeCitation = activeCitationKey ? citationLibrary[activeCitationKey] ?? null : null;
  const firstUserMessage = messages.find(
    (message): message is UserMessage => message.role === "user",
  );

  // The gate stands in front of everything until the visitor has chosen. It is
  // skipped entirely when Supabase is not configured, so a deployment without
  // auth behaves as it always did.
  if (authConfigured && authReady && !user && !guest) {
    return (
      <WelcomeGate
        onContinueAsGuest={() => {
          setGuest(true);
          try {
            window.sessionStorage.setItem(GUEST_CHOICE_KEY, "1");
          } catch {
            /* nothing to remember; the gate reappears next visit */
          }
        }}
      />
    );
  }

  // Retry re-asks the question and replaces that answer in place, so it only
  // makes sense on the last message. Offered mid-conversation it would rewrite
  // an answer that later turns were already built on — and the memory summary
  // that followed it would still describe the answer it replaced. `canRetry`
  // stays the transient "not while a request is in flight" guard.
  const lastMessageId = messages[messages.length - 1]?.id ?? null;
  // The first answer to a legal question carries the disclaimer, once.
  const disclaimerId = disclaimerMessageId(messages);

  return (
    <div className="flex-1 flex flex-col h-full min-h-0">
      <Header
        activeTopic={firstUserMessage?.text ?? null}
        drawerOpen={drawerOpen}
        sidebarOpen={sidebarOpen}
        onToggleSidebar={() => setSidebarOpen(!sidebarOpen)}
        onToggleDrawer={() => setDrawerOpen(!drawerOpen)}
      />
      <div className="flex-1 flex overflow-hidden relative">
        {/* A dimmed backdrop behind whichever panel is overlaying the chat.
            One per panel, because they overlay at different widths: the
            sidebar below lg, the references drawer below xl. */}
        {sidebarOpen && !sidebarDocked && (
          <button
            type="button"
            aria-label="Close conversations"
            className="lg:hidden fixed top-14 bottom-0 inset-x-0 z-30 bg-slate-900/30"
            onClick={() => {
              setSidebarOpen(false);
              setDrawerOpen(false);
            }}
          />
        )}
        {drawerOpen && !drawerDocked && (
          <button
            type="button"
            aria-label="Close references"
            className="xl:hidden fixed top-14 bottom-0 inset-x-0 z-30 bg-slate-900/30"
            onClick={() => setDrawerOpen(false)}
          />
        )}
        <Sidebar
          open={sidebarPick}
          onNewChat={handleNewChat}
          onActivateCitation={handleActivateCitation}
          conversations={conversations}
          activeConversationId={conversationId}
          onOpenConversation={handleOpenConversation}
          onDeleteConversation={handleDeleteConversation}
          onRenameConversation={handleRenameConversation}
        />
        <main className="flex-1 flex flex-col h-full bg-white relative min-w-0 overflow-hidden">
          <div className="flex-1 overflow-y-auto px-4 md:px-12 py-6 space-y-6">
            <div className="max-w-3xl mx-auto space-y-6">
              {!noticeDismissed && (
                <FirstTimeNotice onDismiss={() => setNoticeDismissed(true)} />
              )}

              {messages.length === 0 && <SuggestedQuestions onPick={submitQuestion} />}

              {messages.map((message) =>
                message.role === "user" ? (
                  <MessageUser key={message.id} message={message} />
                ) : (
                  <MessageAssistant
                    key={message.id}
                    message={message}
                    onActivateCitation={handleActivateCitation}
                    onFollowup={submitQuestion}
                    onRetry={
                      // Withheld, not disabled, on anything but the last
                      // message: `disabled` greys out a button that will come
                      // back, and this one never will. Both retry affordances
                      // gate on `onRetry` being present, so this hides them.
                      message.id === lastMessageId
                        ? (withModel?: string) => handleRetry(message.id, withModel)
                        : undefined
                    }
                    canRetry={!loading}
                    showDisclaimer={message.id === disclaimerId}
                  />
                ),
              )}

              {loading && (
                // Keyed, so its seconds counter restarts when the wait for the
                // context gives way to the wait for the answer.
                <ThinkingBubble
                  key={waitingForContext ? "context" : "answer"}
                  waitingForContext={waitingForContext}
                />
              )}
            </div>
          </div>
          <InputDock
            value={inputValue}
            onChange={setInputValue}
            onSubmit={() => submitQuestion(inputValue)}
            disabled={loading}
            model={model}
            onModelChange={setModel}
            memory={memory}
            contextUpdating={contextUpdating}
            conversationId={storedId}
            onForgetMemory={() => {
              dropPendingContext();
              setMemory(null);
            }}
            onActivateCitation={handleActivateCitation}
          />
        </main>
        <ReferencesDrawer
          open={drawerPick}
          references={currentReferences}
          activeCitation={activeCitation}
        />
      </div>
    </div>
  );
}
