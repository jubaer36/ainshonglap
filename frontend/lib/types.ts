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

export interface AssistantMessage {
  id: string;
  role: "assistant";
  heading: string;
  subheading: string;
  unsupported: boolean;
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
}
