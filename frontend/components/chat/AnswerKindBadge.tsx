import type { AnswerKind } from "@/lib/types";

/**
 * How this answer was produced, in three words.
 *
 * The whole design rests on different answers being trustworthy in different
 * ways, and that difference is invisible on screen otherwise. A section quoted
 * from the catalog cannot be wrong — no model touched the text. A generated
 * answer had every claim checked against a retrieved section, which is strong
 * but not the same thing. A `general` answer is the model talking, with nothing
 * behind it. All three look identical as prose, so the difference is labelled.
 *
 * `general` is the one that matters most: it is the only kind that reads like a
 * confident answer while resting on nothing checkable.
 */
const BADGES: Record<AnswerKind, { label: string; title: string; className: string } | null> = {
  lookup: {
    label: "Quoted verbatim",
    title: "Fetched from the parsed statute by section number. No model altered this text.",
    className: "bg-emerald-50 text-emerald-700 border-emerald-200",
  },
  outline: {
    label: "From the statute's structure",
    title: "Chapter and section titles taken from the parsed document hierarchy.",
    className: "bg-emerald-50 text-emerald-700 border-emerald-200",
  },
  legal: {
    label: "Cited & checked",
    title:
      "Retrieved, then every claim verified: the cited section must have been retrieved and the quote must appear in it verbatim. Claims that failed were dropped.",
    className: "bg-slate-100 text-slate-600 border-slate-200",
  },
  general: {
    label: "Not from the Acts",
    title:
      "This is not a legal question, so it was answered from the model's own knowledge. Nothing here is cited or checked against a source.",
    className: "bg-amber-50 text-amber-700 border-amber-200",
  },
  meta: null,
  out_of_scope: null,
  clarify: null,
  refused: null,
};

export function AnswerKindBadge({ kind }: { kind: AnswerKind }) {
  const badge = BADGES[kind];
  if (!badge) return null;
  return (
    <span
      title={badge.title}
      className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-medium leading-none ${badge.className}`}
    >
      {badge.label}
    </span>
  );
}
