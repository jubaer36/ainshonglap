/**
 * Mirrors backend/app/models/schemas.py field-for-field (camelCase here,
 * snake_case there). Keep the two in sync by hand until codegen is worth
 * the trouble.
 */

export type Segment =
  | { type: "text"; text: string }
  | { type: "strong"; text: string }
  | { type: "citation"; key: string; label: string };

export interface ParagraphBlock {
  type: "paragraph";
  segments: Segment[];
}

/** Numbered circle steps, e.g. the FIR filing walkthrough. */
export interface StepsBlock {
  type: "steps";
  items: Segment[][];
}

/** Bullet list with a bold lead-in per item, e.g. the DAO answer. */
export interface BulletsBlock {
  type: "bullets";
  items: Segment[][];
}

export type ContentBlock = ParagraphBlock | StepsBlock | BulletsBlock;

export interface Citation {
  key: string;
  title: string;
  badge: string;
  source: string;
  text: string;
  note: string;
}

export type IconColorToken = "amber" | "blue" | "emerald" | "purple" | "slate";

export interface Reference {
  title: string;
  subtitle: string;
  url: string;
  icon: string;
  /** Semantic token; map to an actual Tailwind class with iconColorClass(). */
  iconColor: IconColorToken;
}

export type QuickAction =
  | { kind: "copy"; label: string; copyText: string }
  | { kind: "preview"; label: string; citationKey: string }
  | { kind: "followup"; label: string; question: string };

export interface UserMessage {
  id: string;
  role: "user";
  text: string;
}

/**
 * Which machine produced an answer. `unsupported` only says "not a grounded
 * answer"; it cannot tell "outside these Acts" from "which Act did you mean?",
 * and those read very differently to someone waiting for an answer.
 *
 *  legal        retrieved, generated, every claim citation-checked
 *  lookup       one named section, quoted verbatim from the catalog
 *  outline      a chapter/range listing, or a whole-Act summary
 *  meta         about the assistant itself
 *  general      not a legal question; the model's own knowledge, uncited
 *  out_of_scope a legal question about law outside the indexed Acts
 *  clarify      the request needs one more detail before it can be run
 *  refused      searched, and nothing survived the citation gates
 */
export type AnswerKind =
  | "legal"
  | "lookup"
  | "outline"
  | "meta"
  | "general"
  | "out_of_scope"
  | "clarify"
  | "refused";

/** Rolling conversation summary. Held by the client and echoed back with each
 *  question — the server stores nothing, which is deliberate for questions this
 *  personal. `sections` is written server-side from the answer's own citations,
 *  never by the summariser, so a section number cannot be paraphrased away. */
export interface ConversationMemory {
  summary: string;
  sections: string[];
  turns: number;
  error: string | null;
}

export interface AssistantMessage {
  id: string;
  role: "assistant";
  heading: string;
  subheading: string;
  kind: AnswerKind;
  unsupported: boolean;
  /** Send back with the next question to continue the conversation. */
  memory: ConversationMemory | null;
  /** Set only when this was answered as a follow-up: the standalone rewrite
   *  that was actually executed. Shown, because a wrong rewrite is otherwise a
   *  confident answer to a question nobody asked. */
  resolvedQuestion: string | null;
  /** Which model actually answered. */
  generator: string | null;
  /** The alias this replaced, when the requested one was out of quota. */
  generatorSubstituted: string | null;
  blocks: ContentBlock[];
  citations: Record<string, Citation>;
  references: Reference[];
  quickActions: QuickAction[];
}

export type ChatMessage = UserMessage | AssistantMessage;

/** One indexed act, from GET /api/acts. */
export interface Act {
  slug: string;
  name: string;
  actNo: string | null;
  sections: number;
  sourcePdf: string | null;
}

/** A section as it appears in a browse listing (no statutory text). */
export interface SectionSummary {
  key: string;
  act: string;
  actSlug: string;
  section: string;
  sectionTitle: string | null;
  chapter: string | null;
  pageStart: number | null;
}

/** A section with its text, from GET /api/sections/{key}. */
export interface SectionDetail extends SectionSummary {
  actNo: string | null;
  part: string | null;
  chapterTitle: string | null;
  pageEnd: number | null;
  sourcePdf: string | null;
  text: string;
}

export interface SectionPage {
  total: number;
  limit: number;
  offset: number;
  results: SectionSummary[];
}

/** A selectable generator, from GET /api/models. */
export interface GeneratorInfo {
  alias: string;
  backend: string;
  model: string;
  /**
   * Whether the route was measured to honour the JSON schema the pipeline
   * requires. false means it returns prose, which the backend surfaces as a
   * refusal rather than an answer; null means advertised but unverified.
   */
  enforcesSchema: boolean | null;
  note: string | null;
  default: boolean;
  /** False when this route is out of quota. Distinct from `enforcesSchema`:
   *  that is permanent and about correctness, this is temporary and about
   *  budget. Both make a model unselectable, for different reasons. */
  available: boolean;
  unavailableReason: string | null;
  secondsRemaining: number | null;
  /** The route that answers when the chosen one is out of quota. */
  fallback: boolean;
}

/**
 * What the planner decided, from GET /api/plan — coordinates, never content.
 * Used by the debug view: when an answer looks wrong, the first question is
 * whether it was *planned* wrong.
 */
export interface QueryPlan {
  question: string;
  category: "meta" | "structural" | "semantic" | "out_of_scope" | "general";
  operation: "fetch" | "enumerate" | "overview" | null;
  act: string | null;
  chapter: string | null;
  sections: string[];
  range: [string, string] | null;
  searchQuery: string | null;
  terms: string[];
  needsClarification: boolean;
  clarification: string | null;
  /** "llm" normally; "fallback" when planning failed and the question
   *  degraded to plain semantic retrieval. */
  by: string;
  /** Everything validation changed or rejected, e.g. a chapter renumbered to
   *  the Act's own spelling, or a coordinate dropped for not existing. */
  notes: string[];
  error: string | null;
}

/** What was heard, from POST /api/transcribe. Never an answer — the transcript
 *  goes back to the reader to confirm or correct before it is asked. */
export interface Transcription {
  transcript: string;
  /** Deepgram's whole-utterance confidence. Shown, not enforced: a low score is
   *  a reason to check the words, not a threshold to block on. */
  confidence: number | null;
  duration: number | null;
  model: string | null;
  language: string | null;
  requestId: string | null;
}
