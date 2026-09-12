// Cosmetic-only mapping from citation key -> pill color. The backend only
// sends key + label; which statute "family" gets which color is a
// frontend display concern, ported from the sample.html mockup.
const CITATION_COLORS: Record<string, string> = {
  "crpc-154": "bg-amber-50 hover:bg-amber-100 text-amber-900 border border-amber-300",
  "prb-243": "bg-blue-50 hover:bg-blue-100 text-blue-900 border border-blue-300",
  "crpc-156": "bg-emerald-50 hover:bg-emerald-100 text-emerald-900 border border-emerald-300",
  "comp-2": "bg-purple-50 hover:bg-purple-100 text-purple-900 border border-purple-300",
  "csa-2023": "bg-blue-50 hover:bg-blue-100 text-blue-900 border border-blue-300",
};

const DEFAULT_CITATION_COLOR =
  "bg-slate-100 hover:bg-slate-200 text-slate-800 border border-slate-300";

export function citationColorClasses(key: string): string {
  return CITATION_COLORS[key] ?? DEFAULT_CITATION_COLOR;
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
