"use client";

import { citationColorClasses } from "@/lib/colorTokens";

interface CitationPillProps {
  citationKey: string;
  label: string;
  onActivate: (key: string) => void;
}

export function CitationPill({ citationKey, label, onActivate }: CitationPillProps) {
  return (
    <button
      type="button"
      className={`citation-pill rounded px-1.5 py-0.5 font-medium ${citationColorClasses(citationKey)}`}
      onMouseEnter={() => onActivate(citationKey)}
      onFocus={() => onActivate(citationKey)}
      onClick={() => onActivate(citationKey)}
    >
      {label}
    </button>
  );
}
