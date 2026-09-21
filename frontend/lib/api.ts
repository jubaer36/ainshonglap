import type {
  Act,
  AnswerKind,
  AssistantMessage,
  ChatMessage,
  GeneratorInfo,
  IconColorToken,
  QueryPlan,
  QuickAction,
  Reference,
  SectionDetail,
  SectionPage,
  SectionSummary,
} from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000";

/**
 * Generation can be slow: the default generator is a reasoning model that
 * spends most of its tokens thinking, and a cold backend also loads two
 * retrieval models. A short timeout would abort answers that were on their way.
 */
const QUERY_TIMEOUT_MS = 180_000;
const BROWSE_TIMEOUT_MS = 15_000;

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
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetch(`${API_BASE_URL}${path}`, { signal: controller.signal });
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
  }));
}

/**
 * The only place in the frontend that knows about the backend. Components call
 * this and never fetch directly.
 */
export async function sendQuery(
  question: string,
  history: ChatMessage[],
  options: { model?: string; refine?: boolean } = {},
): Promise<AssistantMessage> {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), QUERY_TIMEOUT_MS);
  try {
    const response = await fetch(`${API_BASE_URL}/api/query`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
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
