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
 * Two different things make a route unselectable, and they are shown
 * differently because they mean different things:
 *
 *   no JSON schema   permanent, about correctness. The pipeline requires a
 *                    verbatim quote per claim; a route that answers in prose
 *                    parses as "no claims" and surfaces as "the Acts do not
 *                    cover this" — a silently wrong refusal.
 *   out of quota     temporary, about budget. Groq's daily cap is per model,
 *                    so one route running dry says nothing about the others.
 *
 * When the selected model runs out mid-conversation the picker moves to the
 * fallback rather than leaving a dead choice selected, because the alternative
 * is every subsequent question failing with a 502.
 */
export function ModelPicker({ value, onChange, disabled }: ModelPickerProps) {
  const [models, setModels] = useState<GeneratorInfo[]>([]);
  const [failed, setFailed] = useState<string | null>(null);
  const [open, setOpen] = useState(false);
  const rootRef = useRef<HTMLDivElement>(null);
  const [reloadKey, setReloadKey] = useState(0);

  useEffect(() => {
    let cancelled = false;
    function load() {
      fetchModels()
        .then((list) => {
          if (cancelled) return;
          setModels(list);
          setFailed(null);
        })
        .catch((error: unknown) => {
          if (cancelled) return;
          // Kept and shown. "models unavailable" with no cause is the same
          // dead end as the box silently disappearing was.
          console.error("GET /api/models failed", error);
          setModels([]);
          setFailed(
            error instanceof Error ? `${error.name}: ${error.message}` : String(error),
          );
        });
    }
    load();
    // Quota frees up on the provider's clock, not ours, so the list is
    // re-read periodically — otherwise a model stays greyed out until reload
    // long after its budget reset.
    const timer = setInterval(load, 60_000);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [reloadKey]);

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
  const usable = (m: GeneratorInfo) => m.enforcesSchema !== false && m.available;

  // If the chosen model has run out, move to the fallback. Done here rather
  // than server-side alone so the picker stops showing a selection that no
  // longer applies — the backend substitutes anyway, and the two would disagree.
  useEffect(() => {
    if (models.length === 0) return;
    const chosen = models.find((m) => m.alias === value);
    if (!chosen || usable(chosen)) return;
    const replacement =
      models.find((m) => m.fallback && usable(m)) ?? models.find(usable);
    if (replacement) onChange(replacement.alias);
  }, [models, value, onChange]);

  // Never disappear silently. The old version returned null whenever the list
  // was empty, so a backend that 500s on /api/models looked identical to a
  // feature that was never there — which is exactly how this got reported as
  // "the box is gone". Say what happened instead, and offer a retry.
  if (models.length === 0) {
    if (!failed) return null;             // still loading on first paint
    return (
      <button
        type="button"
        onClick={() => setReloadKey((k) => k + 1)}
        title={`Could not load the model list: ${failed}. Click to retry.`}
        className="flex items-center gap-1.5 px-2 py-1 rounded-md border border-rose-200 bg-rose-50 text-[11px] text-rose-700 hover:bg-rose-100"
      >
        <span className="material-symbols-outlined text-[14px]">error</span>
        <span className="font-medium">models unavailable</span>
        <span className="material-symbols-outlined text-[13px]">refresh</span>
      </button>
    );
  }

  return (
    <div ref={rootRef} className="relative">
      <button
        type="button"
        disabled={disabled}
        onClick={() => setOpen((o) => !o)}
        title="Which model writes the answer"
        className="flex items-center gap-1.5 whitespace-nowrap px-2 py-1 rounded-md border border-slate-200 text-[11px] text-slate-600 hover:bg-slate-50 disabled:opacity-50"
      >
        <span className="material-symbols-outlined text-[14px] text-slate-400">memory</span>
        <span className="font-medium">{active?.alias ?? "model"}</span>
        <span className="material-symbols-outlined text-[14px] text-slate-400">
          {open ? "expand_less" : "expand_more"}
        </span>
      </button>

      {open && (
        <div className="absolute bottom-full mb-1 left-0 z-50 w-[min(20rem,calc(100vw-2rem))] max-h-80 overflow-y-auto rounded-lg border border-slate-200 bg-white shadow-lg p-1">
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
                    {!m.available && (
                      <span className="text-[10px] text-rose-600">
                        {m.secondsRemaining && m.secondsRemaining > 60
                          ? `out of quota · ${Math.ceil(m.secondsRemaining / 60)}m`
                          : "out of quota"}
                      </span>
                    )}
                  </span>
                </div>
                {/* Alias and the provider's real model id, and nothing else.
                    `note` carries parameter counts and tokens/sec, which read
                    as a spec sheet next to a choice nobody makes on those
                    grounds. It stays on the API for /docs and /health. */}
                <div className="text-[11px] text-slate-500 truncate">{m.model}</div>
                {!m.available && m.unavailableReason && (
                  <div className="text-[10px] text-rose-500 truncate">
                    {m.unavailableReason}
                  </div>
                )}
              </button>
            );
          })}
        </div>
      )}
    </div>
  );
}
