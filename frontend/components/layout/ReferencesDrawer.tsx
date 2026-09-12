import type { Citation, Reference } from "@/lib/types";
import { LiveCitationPreviewCard } from "../references/LiveCitationPreviewCard";
import { OfficialReferencesCard } from "../references/OfficialReferencesCard";

interface ReferencesDrawerProps {
  open: boolean;
  references: Reference[];
  activeCitation: Citation | null;
}

export function ReferencesDrawer({ open, references, activeCitation }: ReferencesDrawerProps) {
  return (
    <aside
      className={`w-96 max-w-[85vw] bg-slate-50/50 border-l border-slate-200 flex-col flex-shrink-0 overflow-y-auto fixed top-14 bottom-0 right-0 z-40 lg:static lg:z-20 ${
        open ? "flex" : "hidden"
      }`}
    >
      <div className="p-4 space-y-4">
        <OfficialReferencesCard references={references} />
        <LiveCitationPreviewCard citation={activeCitation} />
      </div>
    </aside>
  );
}
