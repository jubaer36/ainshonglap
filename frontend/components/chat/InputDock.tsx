"use client";

import { useRef, useState } from "react";
import { Disclaimer } from "./Disclaimer";
import { ModelPicker } from "./ModelPicker";

interface InputDockProps {
  value: string;
  onChange: (value: string) => void;
  onSubmit: () => void;
  disabled: boolean;
  /** null = whatever the server has configured as default. */
  model: string | null;
  onModelChange: (alias: string | null) => void;
}

const DEFAULT_STATUS = "Bangladeshi Codex Active";

export function InputDock({
  value,
  onChange,
  onSubmit,
  disabled,
  model,
  onModelChange,
}: InputDockProps) {
  const [status, setStatus] = useState(DEFAULT_STATUS);
  const textareaRef = useRef<HTMLTextAreaElement>(null);

  function flashStatus(text: string, thenSet?: string) {
    setStatus(text);
    setTimeout(() => setStatus(thenSet ?? DEFAULT_STATUS), 2000);
  }

  function handleSubmit() {
    if (!value.trim() || disabled) return;
    flashStatus("Synthesizing Bangladeshi statutory codes…", DEFAULT_STATUS);
    onSubmit();
  }

  return (
    <div className="p-4 bg-white border-t border-slate-200 flex-shrink-0 z-20">
      <div className="max-w-3xl mx-auto space-y-2">
        <div className="relative bg-slate-50 hover:bg-slate-100/60 focus-within:bg-white border border-slate-200 focus-within:border-slate-800 focus-within:ring-2 focus-within:ring-slate-900/10 rounded-2xl transition-all shadow-sm">
          <textarea
            ref={textareaRef}
            className="w-full bg-transparent px-4 pt-3.5 pb-2 text-sm text-slate-900 placeholder:text-slate-400 resize-none focus:outline-none leading-relaxed font-sans"
            placeholder="Ask a question about Bangladeshi laws, criminal procedure, bail, or civil remedies..."
            rows={2}
            value={value}
            onChange={(event) => onChange(event.target.value)}
            onKeyDown={(event) => {
              if ((event.metaKey || event.ctrlKey) && event.key === "Enter") {
                event.preventDefault();
                handleSubmit();
              }
            }}
          />
          <div className="flex items-center justify-between px-3 pb-2.5 pt-1">
            <div className="flex items-center gap-1 text-slate-500">
              <ModelPicker value={model} onChange={onModelChange} disabled={disabled} />
              <span className="font-mono text-[11px] text-slate-400 hidden md:inline ml-1.5">
                {status}
              </span>
            </div>
            <div className="flex items-center gap-2">
              <button
                type="button"
                className="px-2.5 py-1 text-xs text-slate-500 hover:text-slate-800 transition-colors"
                onClick={() => {
                  onChange("");
                  textareaRef.current?.focus();
                }}
              >
                Clear
              </button>
              <button
                type="button"
                className="flex items-center gap-1.5 px-3.5 py-1.5 rounded-xl bg-slate-900 text-white hover:bg-slate-800 text-xs font-medium shadow-sm transition-transform active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
                onClick={handleSubmit}
                disabled={disabled || !value.trim()}
              >
                <span>Synthesize</span>
                <span className="material-symbols-outlined text-[15px]">arrow_upward</span>
              </button>
            </div>
          </div>
        </div>
        <div className="text-center font-mono text-[10px] text-slate-400">
          Powered by Bangladeshi Legal Corpus: BDLaws, Supreme Court of Bangladesh Precedents &amp;
          Gazettes.
        </div>
        <Disclaimer />
      </div>
    </div>
  );
}
