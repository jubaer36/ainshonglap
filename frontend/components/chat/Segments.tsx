import type { Segment } from "@/lib/types";
import { CitationPill } from "./CitationPill";

interface SegmentsProps {
  segments: Segment[];
  onActivateCitation: (key: string) => void;
}

export function Segments({ segments, onActivateCitation }: SegmentsProps) {
  return (
    <>
      {segments.map((segment, index) => {
        if (segment.type === "text") {
          return <span key={index}>{segment.text}</span>;
        }
        if (segment.type === "strong") {
          return (
            <strong key={index} className="text-slate-900">
              {segment.text}
            </strong>
          );
        }
        return (
          <CitationPill
            key={index}
            citationKey={segment.key}
            label={segment.label}
            onActivate={onActivateCitation}
          />
        );
      })}
    </>
  );
}
