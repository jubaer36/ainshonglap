import { iconColorClass } from "@/lib/colorTokens";
import type { Reference } from "@/lib/types";

interface OfficialReferencesCardProps {
  references: Reference[];
}

export function OfficialReferencesCard({ references }: OfficialReferencesCardProps) {
  return (
    <div className="bg-white rounded-xl border border-slate-200 p-4 shadow-sm space-y-3">
      <div className="flex items-center justify-between pb-2 border-b border-slate-100">
        <div className="flex items-center gap-2">
          <span className="material-symbols-outlined text-slate-800 text-[18px]">verified</span>
          <h3 className="font-medium text-xs text-slate-900">Official References</h3>
        </div>
        <span className="text-[10px] font-mono text-slate-400">Relevant to query</span>
      </div>
      {references.length === 0 ? (
        <p className="text-[11px] text-slate-500 leading-relaxed">
          Ask a question to see the statutes and rules grounding the answer here.
        </p>
      ) : (
        <>
          <p className="text-[11px] text-slate-500 leading-relaxed">
            Statutes and administrative rules governing the latest answer:
          </p>
          <div className="space-y-2">
            {references.map((reference) => (
              <a
                key={reference.url}
                href={reference.url}
                target="_blank"
                rel="noopener noreferrer"
                className="group block p-2.5 rounded-lg border border-slate-100 hover:border-slate-300 hover:bg-slate-50 transition-all text-xs"
              >
                <div className="flex items-center justify-between font-medium text-slate-800 group-hover:text-blue-600">
                  <span className="flex items-center gap-1.5">
                    <span
                      className={`material-symbols-outlined text-[15px] ${iconColorClass(reference.iconColor)}`}
                    >
                      {reference.icon}
                    </span>
                    <span>{reference.title}</span>
                  </span>
                  <span className="material-symbols-outlined text-[14px] text-slate-400 group-hover:text-blue-600">
                    launch
                  </span>
                </div>
                <div className="text-[11px] text-slate-500 mt-1 pl-5">{reference.subtitle}</div>
              </a>
            ))}
          </div>
        </>
      )}
    </div>
  );
}
