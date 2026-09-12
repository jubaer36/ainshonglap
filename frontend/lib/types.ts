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
