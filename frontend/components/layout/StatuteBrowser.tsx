"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { AddSourceDialog } from "@/components/sources/AddSourceDialog";
import { fetchActs, fetchSections } from "@/lib/api";
import {
  TERMINAL, getJob, listUnfinishedJobs, removeSource, type SourceJob,
} from "@/lib/sources";
import type { Act, SectionSummary } from "@/lib/types";
import { invalidateActs } from "@/lib/useActs";
import { useIsAdmin } from "@/lib/useIsAdmin";

// One icon per Act, assigned from its slug rather than listed per Act, so an
// Act loaded later gets one too. Stable: the same slug always gets the same.
const ICON_SET = ["balance", "gavel", "fact_check", "menu_book", "policy", "article"];
function actIcon(slug: string): string {
  let h = 0;
  for (const ch of slug) h = (h * 31 + ch.charCodeAt(0)) >>> 0;
  return ICON_SET[h % ICON_SET.length];
}
interface StatuteBrowserProps {
  onActivateCitation: (key: string) => void;
}

/**
 * Browse the indexed statute directly, rather than through a question.
 *
 * This is the answer to requests like "state some laws of the penal code" —
 * an instruction to enumerate, which retrieval cannot serve because no section
 * is more relevant than any other, so it has nothing to rank. That query used
 * to reach the RAG pipeline, return five sections matching on stray words, and
 * be refused. Listing is a different operation and belongs here.
 *
 * The acts and counts are read from the API, so what this shows is exactly what
 * is in the index — including the section counts, which are slightly below the
 * parsed totals because duplicate section numbers share one citation key.
 */
export function StatuteBrowser({ onActivateCitation }: StatuteBrowserProps) {
  const [acts, setActs] = useState<Act[]>([]);
  const [activeAct, setActiveAct] = useState<string | null>(null);
  const [query, setQuery] = useState("");
  const [sections, setSections] = useState<SectionSummary[]>([]);
  const [total, setTotal] = useState(0);
  const [loading, setLoading] = useState(false);
  const [unreachable, setUnreachable] = useState(false);
  // Source management — only rendered for admins; the server enforces it.
  const admin = useIsAdmin();
  // The add dialog: closed, a new upload, or reopened on a job under way.
  const [dialog, setDialog] = useState<{ resumeJobId?: string } | null>(null);
  // Documents still being added (or failed). Shown as rows that cannot be
  // browsed yet; a click reopens the dialog on that job.
  const [pending, setPending] = useState<SourceJob[]>([]);
  const pendingIds = useRef<Set<string>>(new Set());
  const [confirming, setConfirming] = useState<string | null>(null);
  const [removals, setRemovals] = useState<Record<string, SourceJob>>({});
  const [removeError, setRemoveError] = useState<string | null>(null);

  const reloadActs = useCallback(() => {
    invalidateActs();
    fetchActs()
      .then((list) => {
        setActs(list);
        setUnreachable(false);
      })
      .catch(() => setUnreachable(true));
  }, []);

  useEffect(() => {
    fetchActs()
      .then(setActs)
      .catch(() => setUnreachable(true));
  }, []);

  // A job leaving the unfinished list has finished or been discarded, so the
  // Act list may have changed.
  const refreshPending = useCallback(() => {
    listUnfinishedJobs()
      .then((list) => {
        const ids = new Set(list.map((j) => j.id));
        const left = [...pendingIds.current].some((id) => !ids.has(id));
        pendingIds.current = ids;
        setPending(list);
        if (left) reloadActs();
      })
      .catch(() => {
        /* not an admin, or source management unavailable: no pending rows */
      });
  }, [reloadActs]);

  useEffect(() => {
    if (admin) refreshPending();
  }, [admin, refreshPending]);

  // Follow jobs that are working. One waiting for its review, or failed, only
  // changes when someone acts on it in the dialog, which refreshes on close.
  useEffect(() => {
    const active = pending.some((j) => ["queued", "reviewing", "building"].includes(j.status));
    if (!active || dialog) return;
    const timer = setTimeout(refreshPending, 3000);
    return () => clearTimeout(timer);
  }, [pending, dialog, refreshPending]);

  // Follow running removals until they finish, then refresh the list.
  useEffect(() => {
    const running = Object.values(removals).filter((j) => !TERMINAL.includes(j.status));
    if (running.length === 0) return;
    const timer = setTimeout(() => {
      Promise.all(running.map((j) => getJob(j.id).catch(() => j))).then((updated) => {
        setRemovals((current) => {
          const next = { ...current };
          for (const j of updated) next[j.slug] = j;
          return next;
        });
        if (updated.some((j) => j.status === "done")) reloadActs();
      });
    }, 1500);
    return () => clearTimeout(timer);
  }, [removals, reloadActs]);

  async function startRemoval(slug: string) {
    setConfirming(null);
    setRemoveError(null);
    try {
      const job = await removeSource(slug);
      setRemovals((current) => ({ ...current, [slug]: job }));
      if (activeAct === slug) setActiveAct(null);
    } catch (e) {
      setRemoveError((e as Error).message);
    }
  }

  useEffect(() => {
    if (!activeAct) {
      setSections([]);
      setTotal(0);
      return;
    }
    let cancelled = false;
    setLoading(true);
    // Debounced so typing in the filter does not fire a request per keystroke.
    const timer = setTimeout(() => {
      fetchSections({ act: activeAct, q: query || undefined, limit: 40 })
        .then((page) => {
          if (cancelled) return;
          setSections(page.results);
          setTotal(page.total);
        })
        .catch(() => {
          if (!cancelled) setSections([]);
        })
        .finally(() => {
          if (!cancelled) setLoading(false);
        });
    }, 200);
    return () => {
      cancelled = true;
      clearTimeout(timer);
    };
  }, [activeAct, query]);

  if (unreachable) {
    return (
      <p className="px-1 text-[11px] text-slate-400">
        Statute list unavailable — backend not reachable.
      </p>
    );
  }

  return (
    <div className="space-y-1">
      {admin && (
        <button
          type="button"
          onClick={() => setDialog({})}
          className="w-full flex items-center gap-1.5 px-2 py-1.5 rounded-lg border border-dashed border-slate-300 text-[11px] text-slate-600 hover:bg-slate-50 hover:text-slate-900"
        >
          <span className="material-symbols-outlined text-[15px]">add</span>
          Add a source
        </button>
      )}
      {removeError && (
        <p className="px-1 text-[11px] text-rose-600">{removeError}</p>
      )}
      {pending.map((job) => (
        <PendingRow key={job.id} job={job} onOpen={() => setDialog({ resumeJobId: job.id })} />
      ))}
      {acts.filter((act) => !pending.some((j) => j.slug === act.slug)).map((act) => {
        const open = activeAct === act.slug;
        const removal = removals[act.slug];
        const removing = removal && !TERMINAL.includes(removal.status);
        return (
          <div key={act.slug}>
            <div className="group flex items-center gap-1">
            <button
              type="button"
              disabled={Boolean(removing)}
              className={`flex-1 min-w-0 flex items-center justify-between p-2 rounded-lg text-left text-xs transition-colors ${
                open ? "bg-slate-100/90 text-slate-900" : "hover:bg-slate-50 text-slate-600"
              } ${removing ? "opacity-50" : ""}`}
              onClick={() => {
                setActiveAct(open ? null : act.slug);
                setQuery("");
              }}
            >
              <span className="flex items-center gap-2 min-w-0 flex-1">
                <span className="material-symbols-outlined text-[16px] text-slate-400 flex-shrink-0">
                  {actIcon(act.slug)}
                </span>
                <span className="truncate">{act.name}</span>
              </span>
              <span className="font-mono text-[10px] text-slate-400 flex-shrink-0 ml-2">
                {removing ? "removing…" : act.sections}
              </span>
            </button>
            {/* Core Acts carry a lock and never a remove button; the server
                refuses to remove them whoever asks. Other sources can be
                removed by an admin, after an in-row confirmation. */}
            {act.core ? (
              admin && (
                <span
                  className="material-symbols-outlined text-[14px] text-slate-300 px-1 flex-shrink-0"
                  title="Core Act — cannot be removed"
                >
                  lock
                </span>
              )
            ) : admin && !removing && confirming !== act.slug ? (
              <button
                type="button"
                onClick={() => setConfirming(act.slug)}
                className="opacity-0 group-hover:opacity-100 focus:opacity-100 p-1 rounded text-slate-400 hover:text-rose-600 flex-shrink-0"
                title={`Remove ${act.name}`}
                aria-label={`Remove ${act.name}`}
              >
                <span className="material-symbols-outlined text-[15px]">delete</span>
              </button>
            ) : null}
            </div>
            {confirming === act.slug && (
              <div className="ml-2 mb-1 flex items-center gap-1.5 text-[11px]">
                <span className="text-slate-600">Remove from the corpus?</span>
                <button
                  type="button"
                  onClick={() => startRemoval(act.slug)}
                  className="px-1.5 py-0.5 rounded bg-rose-600 text-white hover:bg-rose-700"
                >
                  Remove
                </button>
                <button
                  type="button"
                  autoFocus
                  onClick={() => setConfirming(null)}
                  className="px-1.5 py-0.5 rounded border border-slate-300 text-slate-600 hover:bg-slate-100"
                >
                  Cancel
                </button>
              </div>
            )}
            {removal?.status === "failed" && (
              <p className="ml-2 text-[11px] text-rose-600">Removal failed: {removal.error}</p>
            )}

            {open && (
              <div className="mt-1 mb-2 pl-2 space-y-1">
                <input
                  type="search"
                  value={query}
                  onChange={(event) => setQuery(event.target.value)}
                  placeholder="Filter by number or title"
                  className="w-full px-2 py-1 text-[11px] rounded-md border border-slate-200 bg-white focus:outline-none focus:border-slate-400"
                />
                {loading && <p className="px-1 text-[11px] text-slate-400">Loading…</p>}
                {!loading && sections.length === 0 && (
                  <p className="px-1 text-[11px] text-slate-400">No matching sections.</p>
                )}
                <div className="max-h-64 overflow-y-auto space-y-0.5">
                  {sections.map((section) => (
                    <button
                      key={section.key}
                      type="button"
                      className="w-full text-left px-2 py-1 rounded-md hover:bg-slate-50 text-[11px] text-slate-600 hover:text-slate-900"
                      onClick={() => onActivateCitation(section.key)}
                    >
                      <span className="font-mono text-slate-400 mr-1.5">s.{section.section}</span>
                      <span className="truncate">{section.sectionTitle ?? "(untitled)"}</span>
                    </button>
                  ))}
                </div>
                {total > sections.length && (
                  <p className="px-1 text-[10px] text-slate-400">
                    showing {sections.length} of {total}
                  </p>
                )}
              </div>
            )}
          </div>
        );
      })}
      {dialog && (
        <AddSourceDialog
          resumeJobId={dialog.resumeJobId}
          onClose={() => {
            setDialog(null);
            refreshPending();
          }}
          onAdded={reloadActs}
          onJobStarted={(job) => {
            pendingIds.current.add(job.id);
            setPending((current) => [...current.filter((j) => j.id !== job.id), job]);
          }}
        />
      )}
    </div>
  );
}

const PENDING_LABEL: Record<string, { text: string; tone: string }> = {
  queued: { text: "reading…", tone: "text-slate-400" },
  reviewing: { text: "reading…", tone: "text-slate-400" },
  review: { text: "review needed", tone: "text-amber-600" },
  building: { text: "indexing…", tone: "text-slate-400" },
  failed: { text: "failed", tone: "text-rose-600" },
};

/**
 * A document still being added. Greyed and not browsable — it has no sections
 * yet — but clickable: it reopens the add dialog on this job, so closing that
 * dialog to let the job run in the background never loses track of it.
 */
function PendingRow({ job, onOpen }: { job: SourceJob; onOpen: () => void }) {
  const label = PENDING_LABEL[job.status] ?? { text: job.status, tone: "text-slate-400" };
  const working = ["queued", "reviewing", "building"].includes(job.status);
  return (
    <button
      type="button"
      onClick={onOpen}
      title="Still being added — click to see its progress"
      className="w-full flex items-center justify-between gap-2 p-2 rounded-lg text-left text-xs text-slate-400 hover:bg-slate-50"
    >
      <span className="flex items-center gap-2 min-w-0 flex-1">
        {working ? (
          <span className="h-3.5 w-3.5 flex-shrink-0 animate-spin rounded-full border-2 border-slate-200 border-t-slate-500 motion-reduce:animate-none" />
        ) : (
          <span className={`material-symbols-outlined text-[16px] flex-shrink-0 ${label.tone}`}>
            {job.status === "failed" ? "error" : "rate_review"}
          </span>
        )}
        <span className="truncate italic">{job.name}</span>
      </span>
      <span className={`font-mono text-[10px] flex-shrink-0 ${label.tone}`}>{label.text}</span>
    </button>
  );
}
