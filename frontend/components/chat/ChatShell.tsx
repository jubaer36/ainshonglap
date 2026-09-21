"use client";

import { useEffect, useState } from "react";
import { ApiError, fetchSection, sendQuery } from "@/lib/api";
import {
  deleteConversation,
  getConversation,
  listConversations,
  newConversationId,
  saveConversation,
  type SavedConversation,
} from "@/lib/conversations";
import type { AssistantMessage, ChatMessage, Citation, UserMessage, ConversationMemory } from "@/lib/types";
import { Header } from "../layout/Header";
import { ReferencesDrawer } from "../layout/ReferencesDrawer";
import { Sidebar } from "../layout/Sidebar";
import { FirstTimeNotice } from "./FirstTimeNotice";
import { InputDock } from "./InputDock";
import { MessageAssistant } from "./MessageAssistant";
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
function errorAssistantMessage(error: unknown): AssistantMessage {
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
    heading: "AinShonglap Plain Legal Guide",
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
    blocks: [{ type: "paragraph", segments: [{ type: "text", text }] }],
    citations: {},
    references: [],
    quickActions: [],
  };
}

export function ChatShell() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  // The rolling conversation summary. Lives here rather than on the server:
  // these are personal legal questions, and keeping nothing server-side is a
  // privacy property. Cleared by "New chat", which is what makes that button
  // actually start a new conversation rather than just empty the screen.
  const [memory, setMemory] = useState<ConversationMemory | null>(null);
  // Saved conversations live in localStorage (see lib/conversations.ts): these
  // are people's legal questions, and a server that never receives them cannot
  // mishandle them. `conversationId` is allocated up front so the first answer
  // lands in a record rather than creating one retroactively.
  const [conversations, setConversations] = useState<SavedConversation[]>([]);
  const [conversationId, setConversationId] = useState<string>(() => newConversationId());
  const [inputValue, setInputValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [noticeDismissed, setNoticeDismissed] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(true);
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
    setConversations(listConversations());
  }, []);

  useEffect(() => {
    // Below lg, sidebar/drawer are fixed overlays (see Sidebar/ReferencesDrawer)
    // rather than flex-row columns, so default them closed there. Deferred to
    // an effect (rather than the useState initializer) so server and client
    // render the same first pass and avoid a hydration mismatch.
    const isMobile = window.matchMedia("(max-width: 1023px)").matches;
    if (isMobile) {
      setSidebarOpen(false);
      setDrawerOpen(false);
    }
  }, []);

  async function submitQuestion(question: string) {
    const trimmed = question.trim();
    if (!trimmed || loading) return;

    const userMessage: ChatMessage = { id: newId(), role: "user", text: trimmed };
    const historyForRequest = [...messages, userMessage];
    setMessages(historyForRequest);
    setInputValue("");
    setLoading(true);

    try {
      const assistantMessage = await sendQuery(trimmed, historyForRequest, {
        model: model ?? undefined,
        memory,
      });
      const nextMessages = [...historyForRequest, assistantMessage];
      setMessages(nextMessages);
      // Carry the updated summary into the next turn. Left untouched on error,
      // so a failed request does not erase the conversation's memory.
      const nextMemory = assistantMessage.memory ?? memory;
      if (assistantMessage.memory) setMemory(assistantMessage.memory);
      // Saved with the memory, not just the messages: reopening a conversation
      // has to restore what the assistant remembered, or the next follow-up
      // silently loses its context.
      setConversations(saveConversation(conversationId, nextMessages, nextMemory));
      setCitationLibrary((current) => ({ ...current, ...assistantMessage.citations }));
    } catch (error) {
      setMessages((current) => [...current, errorAssistantMessage(error)]);
    } finally {
      setLoading(false);
    }
  }

  function handleNewChat() {
    setConversationId(newConversationId());
    setMessages([]);
    setMemory(null);
    setInputValue("");
    setActiveCitationKey(null);
    if (window.matchMedia("(max-width: 1023px)").matches) {
      setSidebarOpen(false);
    }
  }

  function handleOpenConversation(id: string) {
    const saved = getConversation(id);
    if (!saved) return;
    setConversationId(saved.id);
    setMessages(saved.messages);
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
    if (window.matchMedia("(max-width: 1023px)").matches) setSidebarOpen(false);
  }

  function handleDeleteConversation(id: string) {
    setConversations(deleteConversation(id));
    // Deleting the conversation on screen leaves it open but detached; start a
    // fresh one so the view matches what is stored.
    if (id === conversationId) handleNewChat();
  }

  function handleActivateCitation(key: string) {
    setActiveCitationKey(key);
    setDrawerOpen(true);
    if (window.matchMedia("(max-width: 1023px)").matches) {
      setSidebarOpen(false);
    }
  }

  const latestAssistantMessage = [...messages]
    .reverse()
    .find((message): message is AssistantMessage => message.role === "assistant");
  const currentReferences = latestAssistantMessage?.references ?? [];
  const activeCitation = activeCitationKey ? citationLibrary[activeCitationKey] ?? null : null;
  const firstUserMessage = messages.find(
    (message): message is UserMessage => message.role === "user",
  );

  return (
    <div className="flex-1 flex flex-col h-full min-h-0">
      <Header
        activeTopic={firstUserMessage?.text ?? null}
        drawerOpen={drawerOpen}
        onToggleSidebar={() => setSidebarOpen((open) => !open)}
        onToggleDrawer={() => setDrawerOpen((open) => !open)}
      />
      <div className="flex-1 flex overflow-hidden relative">
        {(sidebarOpen || drawerOpen) && (
          <button
            type="button"
            aria-label="Close panel"
            className="lg:hidden fixed top-14 bottom-0 inset-x-0 z-30 bg-slate-900/30"
            onClick={() => {
              setSidebarOpen(false);
              setDrawerOpen(false);
            }}
          />
        )}
        <Sidebar
          open={sidebarOpen}
          onNewChat={handleNewChat}
          onActivateCitation={handleActivateCitation}
          conversations={conversations}
          activeConversationId={conversationId}
          onOpenConversation={handleOpenConversation}
          onDeleteConversation={handleDeleteConversation}
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
                  />
                ),
              )}

              {loading && <ThinkingBubble />}
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
            onForgetMemory={() => setMemory(null)}
            onActivateCitation={handleActivateCitation}
          />
        </main>
        <ReferencesDrawer
          open={drawerOpen}
          references={currentReferences}
          activeCitation={activeCitation}
        />
      </div>
    </div>
  );
}
