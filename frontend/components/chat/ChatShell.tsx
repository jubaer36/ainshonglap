"use client";

import { useEffect, useState } from "react";
import { fetchCitationLibrary, sendQuery } from "@/lib/api";
import type { AssistantMessage, ChatMessage, Citation, UserMessage } from "@/lib/types";
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

function errorAssistantMessage(): AssistantMessage {
  return {
    id: newId(),
    role: "assistant",
    heading: "AinShonglap Plain Legal Guide",
    subheading: "Connection issue",
    unsupported: true,
    blocks: [
      {
        type: "paragraph",
        segments: [
          {
            type: "text",
            text: "Couldn't reach the backend API. Confirm it's running (see backend/README.md) and try again.",
          },
        ],
      },
    ],
    citations: {},
    references: [],
    quickActions: [],
  };
}

export function ChatShell() {
  const [messages, setMessages] = useState<ChatMessage[]>([]);
  const [inputValue, setInputValue] = useState("");
  const [loading, setLoading] = useState(false);
  const [noticeDismissed, setNoticeDismissed] = useState(false);
  const [sidebarOpen, setSidebarOpen] = useState(true);
  const [drawerOpen, setDrawerOpen] = useState(true);
  const [citationLibrary, setCitationLibrary] = useState<Record<string, Citation>>({});
  const [activeCitationKey, setActiveCitationKey] = useState<string | null>(null);

  useEffect(() => {
    fetchCitationLibrary()
      .then(setCitationLibrary)
      .catch(() => {
        // Sidebar statute previews just won't resolve; chat-driven
        // citations (carried on each AssistantMessage) still work.
      });
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
      const assistantMessage = await sendQuery(trimmed, historyForRequest);
      setMessages((current) => [...current, assistantMessage]);
      setCitationLibrary((current) => ({ ...current, ...assistantMessage.citations }));
    } catch {
      setMessages((current) => [...current, errorAssistantMessage()]);
    } finally {
      setLoading(false);
    }
  }

  function handleNewChat() {
    setMessages([]);
    setInputValue("");
    setActiveCitationKey(null);
    if (window.matchMedia("(max-width: 1023px)").matches) {
      setSidebarOpen(false);
    }
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
