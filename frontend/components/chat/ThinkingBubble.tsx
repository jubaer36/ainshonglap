export function ThinkingBubble() {
  return (
    <div className="flex items-start gap-3.5 pr-2">
      <div className="w-8 h-8 rounded-lg bg-slate-900 text-white flex items-center justify-center flex-shrink-0 font-serif font-bold text-xs shadow-sm mt-0.5">
        আইন
      </div>
      <div className="flex items-center gap-2 text-xs text-slate-500 pt-1.5">
        <span className="flex gap-1">
          <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce [animation-delay:-0.3s]" />
          <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce [animation-delay:-0.15s]" />
          <span className="w-1.5 h-1.5 rounded-full bg-slate-400 animate-bounce" />
        </span>
        <span>Searching Bangladeshi statutory codex…</span>
      </div>
    </div>
  );
}
