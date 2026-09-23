// Citation pill colour, by the Act a key belongs to ("penal-s302" -> penal), so
// pills from different Acts are told apart at a glance.
//
// Assigned from the slug, not listed per Act: the old table keyed specific
// mock citations ("crpc-154") that never matched a real key ("crpc-s154"), so
// every pill was grey, and an Act loaded later would have had no entry at all.
// Literal class strings, because Tailwind only generates what it can read.
const PILL_PALETTE = [
  "bg-amber-50 hover:bg-amber-100 text-amber-900 border border-amber-300",
  "bg-blue-50 hover:bg-blue-100 text-blue-900 border border-blue-300",
  "bg-emerald-50 hover:bg-emerald-100 text-emerald-900 border border-emerald-300",
  "bg-purple-50 hover:bg-purple-100 text-purple-900 border border-purple-300",
  "bg-rose-50 hover:bg-rose-100 text-rose-900 border border-rose-300",
  "bg-cyan-50 hover:bg-cyan-100 text-cyan-900 border border-cyan-300",
];

const DEFAULT_CITATION_COLOR =
  "bg-slate-100 hover:bg-slate-200 text-slate-800 border border-slate-300";

export function citationColorClasses(key: string): string {
  const slug = key.split("-s")[0];
  if (!slug || slug === key) return DEFAULT_CITATION_COLOR;
  let h = 0;
  for (const ch of slug) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return PILL_PALETTE[h % PILL_PALETTE.length];
}

// Same rationale: Tailwind's content scanner needs to see these literal
// class strings in a frontend file. A raw class string sent from the
// backend as data (e.g. "text-amber-600") would never get generated.
const ICON_COLOR_CLASSES: Record<string, string> = {
  amber: "text-amber-600",
  blue: "text-blue-600",
  emerald: "text-emerald-600",
  purple: "text-purple-600",
  slate: "text-slate-600",
};

export function iconColorClass(token: string): string {
  return ICON_COLOR_CLASSES[token] ?? ICON_COLOR_CLASSES.slate;
}
