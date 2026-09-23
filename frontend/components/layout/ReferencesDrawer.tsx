"use client";

import type { Citation, Reference } from "@/lib/types";
import { ResizeHandle, useResizableWidth } from "./ResizeHandle";
import { LiveCitationPreviewCard } from "../references/LiveCitationPreviewCard";
import { OfficialReferencesCard } from "../references/OfficialReferencesCard";

interface ReferencesDrawerProps {
  open: boolean;
  references: Reference[];
  activeCitation: Citation | null;
}

// Wider bounds than the left sidebar: this panel shows statutory text, and a
// long section reads better with room. The floor still fits a citation card.
const MIN_WIDTH = 280;
const MAX_WIDTH = 720;
const DEFAULT_WIDTH = 384; // w-96

export function ReferencesDrawer({ open, references, activeCitation }: ReferencesDrawerProps) {
  const panel = useResizableWidth({ min: MIN_WIDTH, max: MAX_WIDTH, initial: DEFAULT_WIDTH });

  return (
    <aside
      style={{ width: panel.width }}
      className={`max-w-[85vw] bg-slate-50/50 border-l border-slate-200 flex-col flex-shrink-0 fixed top-14 bottom-0 right-0 z-40 lg:relative lg:top-auto lg:bottom-auto lg:right-auto lg:z-20 ${
        open ? "flex" : "hidden"
      }`}
    >
      {/* Handle on the LEFT edge, so dragging left widens the panel. */}
      <ResizeHandle
        edge="left"
        width={panel.width}
        min={panel.min}
        max={panel.max}
        initial={panel.initial}
        onResize={panel.setWidth}
        label="Resize references panel"
      />
      {/* Scrolling lives here rather than on the aside: the handle is
          absolutely positioned against the aside, and inside a scrolling
          container it would scroll away with the citations. */}
      <div className="flex-1 min-h-0 overflow-y-auto">
        <div className="p-4 space-y-4">
          <OfficialReferencesCard references={references} />
          <LiveCitationPreviewCard citation={activeCitation} />
        </div>
      </div>
    </aside>
  );
}
