/**
 * Always rendered, independent of FirstTimeNotice's dismiss state —
 * this is the one disclaimer that must never be dismissable.
 */
export function Disclaimer() {
  return (
    <div className="flex items-center justify-center gap-1.5 text-center font-mono text-[10px] text-slate-400">
      <span className="material-symbols-outlined text-[13px]">gavel</span>
      <span>
        AinShonglap provides legal information, not legal advice. Verify against official sources
        before acting.
      </span>
    </div>
  );
}
