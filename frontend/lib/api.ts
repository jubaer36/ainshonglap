import { accessToken } from "./supabase";
import type {
  Act,
  AnswerKind,
  AssistantMessage,
  ChatMessage,
  ConversationMemory,
  GeneratorInfo,
  IconColorToken,
  ImageReading,
  QueryPlan,
  QuickAction,
  Reference,
  SectionDetail,
  SectionPage,
  SectionSummary,
  Transcription,
} from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Authorization header for the current session, or nothing.
 *
 * Absent for anonymous use, which is the default: the API answers questions
 * without a token and only refuses the conversation endpoints, which have
 * nothing to return for someone who is not signed in.
 */
async function authHeaders(): Promise<Record<string, string>> {
  const token = await accessToken();
  return token ? { Authorization: `Bearer ${token}` } : {};
}

/**
 * Generation can be slow: the default generator is a reasoning model that
 * spends most of its tokens thinking, and a cold backend also loads two
 * retrieval models. A short timeout would abort answers that were on their way.
 */
const QUERY_TIMEOUT_MS = 180_000;
const BROWSE_TIMEOUT_MS = 15_000;
// Transcription is roughly realtime; a 60s clip should never take this long.
const TRANSCRIBE_TIMEOUT_MS = 60_000;
// Vision models are slower than speech, and a dense page is a long output.
const IMAGE_TIMEOUT_MS = 180_000;

/** Distinguishes "the API said no" from "the API wasn't there", which need
 * different messages to the reader. */
export class ApiError extends Error {
  constructor(
    readonly status: number,
    message: string,
  ) {
    super(message);
  }
}

async function getJson<T>(path: string, timeoutMs = BROWSE_TIMEOUT_MS): Promise<T> {
  // Resolved BEFORE the timer starts. `authHeaders()` asks Supabase for the
  // current session, which can go to the network to refresh a token — awaited
  // inside the window it spent the whole timeout, and the AbortError then
  // pointed at the fetch, which had not even been issued.
  const headers = await authHeaders();
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, {
      signal: controller.signal,
      headers,
    });
    if (!response.ok) {
      throw new ApiError(response.status, `${path} failed with status ${response.status}`);
    }
    return (await response.json()) as T;
  } finally {
    clearTimeout(timer);
  }
}

/** The acts actually in the index, with how much of each is there. */
export async function fetchActs(): Promise<Act[]> {
  const raw = await getJson<Record<string, unknown>[]>("/api/acts");
  return raw.map((a) => ({
    slug: a.slug as string,
    name: a.name as string,
    actNo: (a.act_no as string) ?? null,
    sections: a.sections as number,
    sourcePdf: (a.source_pdf as string) ?? null,
  }));
}

/**
 * Browse the corpus. This is the right answer to "list some laws of the penal
 * code" — a request to enumerate, which retrieval cannot rank and should not
 * try to.
 */
export async function fetchSections(params: {
  act?: string;
  q?: string;
  limit?: number;
  offset?: number;
} = {}): Promise<SectionPage> {
  const search = new URLSearchParams();
  if (params.act) search.set("act", params.act);
  if (params.q) search.set("q", params.q);
  search.set("limit", String(params.limit ?? 50));
  search.set("offset", String(params.offset ?? 0));
  const raw = await getJson<Record<string, unknown>>(`/api/sections?${search}`);
  return {
    total: raw.total as number,
    limit: raw.limit as number,
    offset: raw.offset as number,
    results: (raw.results as Record<string, unknown>[]).map(normalizeSectionSummary),
  };
}

/**
 * One section with its text. Fetched on demand rather than loading all ~1,300
 * up front: the previous client pulled the entire citation library on mount,
 * which only worked while that library was five canned entries.
 */
export async function fetchSection(key: string): Promise<SectionDetail> {
  const raw = await getJson<Record<string, unknown>>(`/api/sections/${encodeURIComponent(key)}`);
  return {
    ...normalizeSectionSummary(raw),
    actNo: (raw.act_no as string) ?? null,
    part: (raw.part as string) ?? null,
    chapterTitle: (raw.chapter_title as string) ?? null,
    pageEnd: (raw.page_end as number) ?? null,
    sourcePdf: (raw.source_pdf as string) ?? null,
    text: raw.text as string,
  };
}

/** Generators this deployment can use, for the model picker. */
export async function fetchModels(): Promise<GeneratorInfo[]> {
  const raw = await getJson<Record<string, unknown>[]>("/api/models");
  return raw.map((m) => ({
    alias: m.alias as string,
    backend: m.backend as string,
    model: m.model as string,
    enforcesSchema: (m.enforces_schema as boolean | null) ?? null,
    note: (m.note as string) ?? null,
    default: Boolean(m.default),
    // Older backends do not send these; treat a missing flag as available
    // rather than hiding every model.
    available: m.available === undefined ? true : Boolean(m.available),
    unavailableReason: (m.unavailable_reason as string) ?? null,
    secondsRemaining: (m.seconds_remaining as number) ?? null,
    fallback: Boolean(m.fallback),
  }));
}

/**
 * The only place in the frontend that knows about the backend. Components call
 * this and never fetch directly.
 */
export async function sendQuery(
  question: string,
  history: ChatMessage[],
  options: {
    model?: string;
    refine?: boolean;
    memory?: ConversationMemory | null;
    conversationId?: string | null;
  } = {},
): Promise<AssistantMessage> {
  const headers = { "Content-Type": "application/json", ...(await authHeaders()) };
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), QUERY_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE_URL}/api/query`, {
      method: "POST",
      headers,
      signal: controller.signal,
      body: JSON.stringify({
        question,
        history: history.map((message) => ({
          id: message.id,
          role: message.role,
          text: message.role === "user" ? message.text : undefined,
        })),
        model: options.model,
        refine: options.refine,
        // The memory the previous answer returned. Omitted on the first turn.
        memory: options.memory ?? null,
        // Where to append, for a signed-in user. Ignored for a guest, whose
        // turns are never stored.
        conversation_id: options.conversationId ?? null,
      }),
    });

    if (!response.ok) {
      throw new ApiError(response.status, `Query failed with status ${response.status}`);
    }
    return normalizeAssistantMessage(await response.json());
  } finally {
    clearTimeout(timer);
  }
}

/**
 * What the planner decided, without answering. One small model call: no
 * retrieval, no generation. Used by the debug panel to show why a question was
 * routed the way it was.
 */
export async function fetchPlan(question: string, model?: string): Promise<QueryPlan> {
  const params = new URLSearchParams({ q: question });
  if (model) params.set("model", model);
  const raw = await getJson<Record<string, unknown>>(`/api/plan?${params}`);
  return {
    question: raw.question as string,
    category: raw.category as QueryPlan["category"],
    operation: (raw.operation as QueryPlan["operation"]) ?? null,
    act: (raw.act as string) ?? null,
    chapter: (raw.chapter as string) ?? null,
    sections: (raw.sections as string[]) ?? [],
    range: (raw.range as [string, string]) ?? null,
    searchQuery: (raw.search_query as string) ?? null,
    terms: (raw.terms as string[]) ?? [],
    needsClarification: Boolean(raw.needs_clarification),
    clarification: (raw.clarification as string) ?? null,
    by: (raw.by as string) ?? "llm",
    notes: (raw.notes as string[]) ?? [],
    error: (raw.error as string) ?? null,
  };
}

/**
 * Send a recording for transcription. Returns what was heard — it does NOT ask
 * the question.
 *
 * That split is a safety property rather than an API convenience. A misheard
 * section number yields a perfectly grounded answer about the wrong offence:
 * the planner names coordinates that exist, the catalog fetches them, the quote
 * verifies against the section fetched. Every check passes. Nothing downstream
 * knows what was actually said, so the reader is the only check there is, and
 * they get the words before anything is sent.
 */
export async function transcribeAudio(
  blob: Blob,
  language?: string,
  onPhase?: (phase: "uploading" | "processing", percent?: number) => void,
): Promise<Transcription> {
  const form = new FormData();
  // A filename is required for FastAPI to treat this as an UploadFile; the
  // extension is what carries the container type through.
  form.append("audio", blob, "recording.webm");
  const query = language ? `?language=${encodeURIComponent(language)}` : "";
  const raw = await postFormWithPhases<Record<string, unknown>>(
    `/api/transcribe${query}`,
    form,
    TRANSCRIBE_TIMEOUT_MS,
    onPhase,
    await authHeaders(),
  );
  return {
    transcript: (raw.transcript as string) ?? "",
    confidence: (raw.confidence as number) ?? null,
    duration: (raw.duration as number) ?? null,
    model: (raw.model as string) ?? null,
    language: (raw.language as string) ?? null,
    requestId: (raw.request_id as string) ?? null,
  };
}

/**
 * Upload a file and report the two phases honestly.
 *
 * `fetch` cannot report upload progress, so "sending" and "the model is
 * working" are indistinguishable through it — the UI would have to guess when
 * one became the other. XHR exposes `upload.onprogress`, so the switch happens
 * when the bytes have actually left: everything after that really is the server
 * and the model, which is what the waiting message claims.
 */
function postFormWithPhases<T>(
  path: string,
  form: FormData,
  timeoutMs: number,
  onPhase?: (phase: "uploading" | "processing", percent?: number) => void,
  headers: Record<string, string> = {},
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${API_BASE_URL}${path}`);
    for (const [name, value] of Object.entries(headers)) {
      xhr.setRequestHeader(name, value);
    }
    xhr.timeout = timeoutMs;
    xhr.responseType = "text";

    onPhase?.("uploading", 0);
    xhr.upload.onprogress = (event) => {
      if (!event.lengthComputable) return;
      const percent = Math.round((event.loaded / event.total) * 100);
      onPhase?.(percent >= 100 ? "processing" : "uploading", percent);
    };
    // Some browsers fire `load` on the upload without a final progress event.
    xhr.upload.onload = () => onPhase?.("processing", 100);

    xhr.onload = () => {
      if (xhr.status >= 200 && xhr.status < 300) {
        try {
          resolve(JSON.parse(xhr.responseText) as T);
        } catch {
          reject(new ApiError(xhr.status, "The server sent a malformed response."));
        }
        return;
      }
      // The server's own message is more specific than anything invented here
      // — a 415 explains exactly what to send instead.
      let detail = `${path} failed with status ${xhr.status}`;
      try {
        const body = JSON.parse(xhr.responseText) as { detail?: string };
        if (body.detail) detail = body.detail;
      } catch {
        /* keep the generic message */
      }
      reject(new ApiError(xhr.status, detail));
    };
    xhr.onerror = () => reject(new ApiError(0, "Could not reach the backend."));
    xhr.ontimeout = () => reject(new ApiError(0, "The request timed out."));
    xhr.send(form);
  });
}

/** One page as PNG, JPEG or WebP. PDFs and documents are refused by the server
 *  — a PDF has a text layer that the ingestion parser reads far more reliably
 *  than a vision model can. */
export const IMAGE_TYPES = ["image/png", "image/jpeg", "image/webp"];
export const IMAGE_MAX_BYTES = 5 * 1024 * 1024;

/**
 * Read the text off a single page image. Does NOT ask the question — the
 * transcription goes back for the reader to check, exactly as with voice,
 * because a plausibly-wrong reading ("302A" -> "302") would otherwise become a
 * fully grounded answer about a different provision.
 */
export async function readImage(
  file: File,
  onPhase?: (phase: "uploading" | "processing", percent?: number) => void,
  conversationId?: string | null,
): Promise<ImageReading> {
  const form = new FormData();
  form.append("image", file, file.name);
  // The conversation the page belongs to, so a signed-in session can show the
  // image again when it is reopened. Ignored for a guest, whose page is read
  // and discarded.
  const query = conversationId ? `?conversation_id=${encodeURIComponent(conversationId)}` : "";
  const raw = await postFormWithPhases<Record<string, unknown>>(
    `/api/read-image${query}`,
    form,
    IMAGE_TIMEOUT_MS,
    onPhase,
    await authHeaders(),
  );
  return {
    text: (raw.text as string) ?? "",
    model: (raw.model as string) ?? null,
    chars: (raw.chars as number) ?? 0,
    uploadId: (raw.upload_id as string) ?? null,
    url: (raw.url as string) ?? null,
  };
}

function normalizeSectionSummary(raw: Record<string, unknown>): SectionSummary {
  return {
    key: raw.key as string,
    act: raw.act as string,
    actSlug: raw.act_slug as string,
    section: raw.section as string,
    sectionTitle: (raw.section_title as string) ?? null,
    chapter: (raw.chapter as string) ?? null,
    pageStart: (raw.page_start as number) ?? null,
  };
}

// The backend's Pydantic models serialize as snake_case; the frontend types
// are camelCase. This is the single conversion point.
function normalizeAssistantMessage(raw: Record<string, unknown>): AssistantMessage {
  return {
    id: raw.id as string,
    role: "assistant",
    heading: raw.heading as string,
    subheading: raw.subheading as string,
    // Older backends did not send `kind`. Falling back on `unsupported` keeps
    // this working against one, at the cost of the finer distinctions.
    kind: ((raw.kind as AnswerKind) ??
      (raw.unsupported ? "refused" : "legal")) as AnswerKind,
    unsupported: Boolean(raw.unsupported),
    memory: (raw.memory as ConversationMemory) ?? null,
    resolvedQuestion: (raw.resolved_question as string) ?? null,
    generator: (raw.generator as string) ?? null,
    generatorSubstituted: (raw.generator_substituted as string) ?? null,
    retryQuestion: null,
    conversationId: (raw.conversation_id as string) ?? null,
    notice: (raw.notice as string) ?? null,
    suggestedModel: (raw.suggested_model as string) ?? null,
    blocks: raw.blocks as AssistantMessage["blocks"],
    citations: raw.citations as AssistantMessage["citations"],
    references: ((raw.references as Record<string, unknown>[]) ?? []).map(normalizeReference),
    quickActions: ((raw.quick_actions as Record<string, unknown>[]) ?? []).map(
      normalizeQuickAction,
    ),
  };
}

function normalizeReference(raw: Record<string, unknown>): Reference {
  return {
    title: raw.title as string,
    subtitle: raw.subtitle as string,
    url: raw.url as string,
    icon: raw.icon as string,
    iconColor: raw.icon_color as IconColorToken,
  };
}

function normalizeQuickAction(raw: Record<string, unknown>): QuickAction {
  switch (raw.kind) {
    case "copy":
      return { kind: "copy", label: raw.label as string, copyText: raw.copy_text as string };
    case "preview":
      return {
        kind: "preview",
        label: raw.label as string,
        citationKey: raw.citation_key as string,
      };
    case "followup":
      return { kind: "followup", label: raw.label as string, question: raw.question as string };
    default:
      throw new Error(`Unknown quick action kind: ${String(raw.kind)}`);
  }
}
