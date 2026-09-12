import type { ContentBlock } from "@/lib/types";
import { Segments } from "./Segments";

interface BlockRendererProps {
  block: ContentBlock;
  onActivateCitation: (key: string) => void;
}

export function BlockRenderer({ block, onActivateCitation }: BlockRendererProps) {
  if (block.type === "paragraph") {
    return (
      <p>
        <Segments segments={block.segments} onActivateCitation={onActivateCitation} />
      </p>
    );
  }

  if (block.type === "steps") {
    return (
      <div className="rounded-xl bg-slate-50/90 border border-slate-200/90 p-4 space-y-3">
        <div className="space-y-2.5 text-xs text-slate-700">
          {block.items.map((segments, index) => (
            <div key={index} className="flex items-start gap-2.5">
              <span className="w-5 h-5 rounded-full bg-slate-200 text-slate-800 flex items-center justify-center font-bold text-[11px] flex-shrink-0 mt-0.5">
                {index + 1}
              </span>
              <div>
                <Segments segments={segments} onActivateCitation={onActivateCitation} />
              </div>
            </div>
          ))}
        </div>
      </div>
    );
  }

  // block.type === "bullets"
  return (
    <div className="p-3.5 rounded-xl bg-slate-50 border border-slate-200/80 text-xs text-slate-700 space-y-2">
      {block.items.map((segments, index) => (
        <p key={index}>
          •{" "}
          <Segments segments={segments} onActivateCitation={onActivateCitation} />
        </p>
      ))}
    </div>
  );
}
