"use client";

import { useEffect, useRef, useState } from "react";
import { fetchModels } from "@/lib/api";
import type { GeneratorInfo } from "@/lib/types";

interface ModelPickerProps {
  value: string | null;
  onChange: (alias: string | null) => void;
  disabled?: boolean;
}

/**
 * Picks which generator answers. The backend resolves the alias, so the list
 * comes from GET /api/models rather than being duplicated here — the two would
 * otherwise disagree about what "lfm" means.
 *
 * Routes that cannot honour the JSON schema are shown but disabled. They are
 * not merely lower quality: the pipeline requires a verbatim quote per claim,
 * and a route that answers in prose gets parsed as "no claims", which the
 * backend reports as "the Acts do not cover this". A silently wrong refusal is
 * worse than an unavailable option, so the reason is stated rather than hidden.
 */
export function ModelPicker({ value, onChange, disabled }: ModelPickerProps) {
  const [models, setModels] = useState<GeneratorInfo[]>([]);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    fetchModels()
      .then(setModels)
      .catch(() => setModels([])); // picker just stays empty; the server default still applies
  }, []);

  useEffect(() => {
    if (!open) return;
    function onDocumentClick(event: MouseEvent) {
      if (!rootRef.current?.contains(event.target as Node)) setOpen(false);
    }
    document.addEventListener("mousedown", onDocumentClick);
    return () => document.removeEventListener("mousedown", onDocumentClick);
  }, [open]);

  const serverDefault = models.find((m) => m.default);
  const active = models.find((m) => m.alias === value) ?? serverDefault ?? null;
  const usable = (m: GeneratorInfo) => m.enforcesSchema !== false;

  if (models.length === 0) return null;

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        title="Which model writes the answer"
        className="flex items-center gap-1.5 px-2 py-1 rounded-md border border-slate-200 text-[11px] text-slate-600 hover:bg-slate-50 disabled:opacity-50"
      >
        <span className="material-symbols-outlined text-[14px] text-slate-400">memory</span>
        <span className="font-medium">{active?.alias ?? "model"}</span>
        <span className="material-symbols-outlined text-[14px] text-slate-400">
          {open ? "expand_less" : "expand_more"}
        </span>
      </button>

      {open && (
        <div className="absolute bottom-full mb-1 left-0 z-50 w-80 max-h-80 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg p-1">
          {models.map((m) => {
            const selectable = usable(m);
            return (
              <button
                key={m.alias}
                type="button"
                disabled={!selectable}
                onClick={() => {
                  onChange(m.alias);
                  setOpen(false);
                }}
                className={`w-full text-left px-2 py-1.5 rounded-md text-xs transition-colors ${
                  m.alias === active?.alias ? "bg-slate-100" : "hover:bg-slate-50"
                } ${selectable ? "" : "opacity-45 cursor-not-allowed"}`}
              >
                <div className="flex items-center justify-between gap-2">
                  <span className="font-medium text-slate-800">{m.alias}</span>
                  <span className="flex items-center gap-1 flex-shrink-0">
                    {m.default && (
                      <span className="text-[10px] text-slate-400 uppercase tracking-wide">
                        default
                      </span>
                    )}
                    {m.enforcesSchema === false && (
                      <span className="text-[10px] text-rose-600">no JSON schema</span>
                    )}
                    {m.enforcesSchema === null && (
                      <span className="text-[10px] text-amber-600">unverified</span>
                    )}
                  </span>
                </div>
                <div className="text-[11px] text-slate-500 truncate">{m.model}</div>
                {m.note && <div className="text-[10px] text-slate-400 truncate">{m.note}</div>}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
