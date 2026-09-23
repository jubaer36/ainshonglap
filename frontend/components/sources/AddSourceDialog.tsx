"use client";

import { useEffect, useRef, useState, useSyncExternalStore } from "react";
import { createPortal } from "react-dom";
import {
  TERMINAL, chooseParser, discardJob, getJob, suggestSlug, uploadSource,
  type ParserSummary, type ReviewPage, type ReviewUnit, type SourceJob,
} from "@/lib/sources";

interface AddSourceDialogProps {
  onClose: () => void;
  /** Called once when the source is indexed, so lists can refresh. */
  onAdded: () => void;
}

/**
 * Add a document to the corpus: upload, review, index.
 *
 * The review is the point of the dialog. Two parsers read a PDF differently —
 * PyMuPDF finds a statute's numbered sections, Docling reads any layout — and
 * which one fits is not something a rule judges reliably, because the failure
 * to catch is text that is all there but in the wrong place. So sampled pages
 * are shown as images beside what each parser read from them, and the person
 * decides.
 *
 * Two widths on purpose: narrow while collecting four fields, wide only while
 * comparing pages, where the width is doing work. Rendered into document.body:
 * inside the sidebar it inherited the sidebar's stacking layer, and the input
 * dock showed through the backdrop.
 */
export function AddSourceDialog({ onClose, onAdded }: AddSourceDialogProps) {
  const [file, setFile] = useState<File | null>(null);
  const [name, setName] = useState("");
  const [slug, setSlug] = useState("");
  const [slugTouched, setSlugTouched] = useState(false);
  const [actNo, setActNo] = useState("");
  const [job, setJob] = useState<SourceJob | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const reported = useRef(false);
  const inBrowser = useSyncExternalStore(noop, () => true, () => false);

  // Poll until the job needs a person (review) or has finished.
  useEffect(() => {
    if (!job || TERMINAL.includes(job.status) || job.status === "review") return;
    const timer = setTimeout(() => {
      getJob(job.id).then(setJob).catch((e: Error) => setError(e.message));
    }, 1500);
    return () => clearTimeout(timer);
  }, [job]);

  useEffect(() => {
    if (job?.status === "done" && !reported.current) {
      reported.current = true;
      onAdded();
    }
  }, [job, onAdded]);

  // Escape closes; the page behind does not scroll while the dialog is open.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    const overflow = document.body.style.overflow;
    document.body.style.overflow = "hidden";
    return () => {
      window.removeEventListener("keydown", onKey);
      document.body.style.overflow = overflow;
    };
  }, [onClose]);

  async function run(fn: () => Promise<SourceJob>) {
    setBusy(true);
    setError(null);
    try {
      setJob(await fn());
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const status = job?.status;
  const reviewing = status === "review" && job?.review;
  const step = !job ? 0 : status === "done" ? 3 : status === "building" ? 2 : 1;
  const working = Boolean(job && !TERMINAL.includes(job.status) && status !== "review");

  const body = (
    <div
      className="fixed inset-0 z-[100] flex items-end sm:items-center justify-center bg-slate-900/45 sm:p-6"
      // A click on the backdrop closes — except while working, where a stray
      // click would lose track of a job still running.
      onMouseDown={(e) => e.target === e.currentTarget && !working && onClose()}
    >
      <div
        role="dialog"
        aria-modal="true"
        aria-labelledby="add-source-title"
        className={`relative flex w-full flex-col bg-white shadow-2xl rounded-t-2xl sm:rounded-2xl max-h-[92vh] ${
          reviewing ? "sm:max-w-6xl" : "sm:max-w-lg"
        }`}
      >
        <header className="flex items-start justify-between gap-4 px-5 pt-5 pb-4 border-b border-slate-100">
          <div className="min-w-0 space-y-3">
            <div>
              <h2 id="add-source-title" className="font-serif text-lg font-semibold text-slate-900">
                {job ? job.name : "Add a source"}
              </h2>
              <p className="text-[13px] text-slate-500">
                {!job && "Upload a PDF. You check a few of its pages before anything is added."}
                {reviewing && "Compare each page with what the two parsers read from it, then pick one."}
                {(status === "queued" || status === "reviewing") && "Reading the document. The pages to check appear here in a moment."}
                {status === "building" && "Indexing. You can close this; it keeps going on the server."}
                {status === "done" && "Added. It can be searched and cited now."}
                {status === "failed" && "This didn't finish. Nothing half-added is left behind."}
                {status === "discarded" && "Discarded. Nothing was added."}
              </p>
            </div>
            <Stepper step={step} failed={status === "failed"} />
          </div>
          <button
            type="button"
            onClick={onClose}
            className="-mr-1 rounded-lg p-1.5 text-slate-400 hover:bg-slate-100 hover:text-slate-800"
            aria-label="Close"
            title={working ? "Close — the job keeps running" : "Close"}
          >
            <span className="material-symbols-outlined text-[20px]">close</span>
          </button>
        </header>

        <div className="flex-1 overflow-y-auto px-5 py-5">
          {error && (
            <p className="mb-4 rounded-lg bg-rose-50 px-3 py-2 text-[13px] text-rose-800">{error}</p>
          )}

          {!job && (
            <UploadForm
              file={file} name={name} slug={slug} actNo={actNo}
              onFile={(f) => {
                setFile(f);
                if (f && !name) {
                  const title = f.name.replace(/\.pdf$/i, "");
                  setName(title);
                  if (!slugTouched) setSlug(suggestSlug(title));
                }
              }}
              onName={(v) => {
                setName(v);
                if (!slugTouched) setSlug(suggestSlug(v));
              }}
              onSlug={(v) => {
                setSlugTouched(true);
                setSlug(v.toLowerCase().replace(/[^a-z0-9_]/g, ""));
              }}
              onActNo={setActNo}
            />
          )}

          {job && working && <Working job={job} />}

          {reviewing && <Review review={job!.review!} />}

          {status === "done" && (
            <div className="flex items-start gap-3">
              <span className="material-symbols-outlined text-[28px] text-emerald-600">task_alt</span>
              <div className="space-y-2 text-[13px] text-slate-700">
                <p>
                  <span className="font-medium text-slate-900">{job!.name}</span> is in the corpus.
                  Questions can reach it, and its sections are in the statute list.
                </p>
                <Details lines={job!.log} />
              </div>
            </div>
          )}

          {status === "failed" && (
            <div className="space-y-2 text-[13px]">
              <p className="rounded-lg bg-rose-50 px-3 py-2 text-rose-800">{job!.error}</p>
              <Details lines={job!.log} open />
            </div>
          )}
        </div>

        <footer className="flex flex-wrap items-center justify-end gap-2 border-t border-slate-100 px-5 py-3">
          {!job && (
            <>
              <button type="button" onClick={onClose}
                className="rounded-lg px-3 py-2 text-[13px] text-slate-600 hover:bg-slate-100">
                Cancel
              </button>
              <button
                type="button"
                disabled={busy || !file || !name.trim() || !/^[a-z][a-z0-9_]{1,30}$/.test(slug)}
                onClick={() => file && run(() => uploadSource({ file, slug, name, actNo }))}
                className="rounded-lg bg-slate-900 px-4 py-2 text-[13px] font-medium text-white hover:bg-slate-800 disabled:bg-slate-300"
              >
                {busy ? "Uploading…" : "Upload and review"}
              </button>
            </>
          )}

          {reviewing && (
            <ChoiceBar
              summary={job!.review!.pymupdf}
              busy={busy}
              onChoose={(parser) => run(() => chooseParser(job!.id, parser))}
              onDiscard={() => run(() => discardJob(job!.id))}
            />
          )}

          {(status === "done" || status === "discarded") && (
            <button type="button" onClick={onClose}
              className="rounded-lg bg-slate-900 px-4 py-2 text-[13px] font-medium text-white hover:bg-slate-800">
              Close
            </button>
          )}

          {status === "failed" && (
            <button type="button" disabled={busy} onClick={() => run(() => discardJob(job!.id))}
              className="rounded-lg border border-slate-200 px-3 py-2 text-[13px] text-slate-700 hover:bg-slate-50">
              Discard and close
            </button>
          )}

          {status === "building" && (
            <button type="button" onClick={onClose}
              className="rounded-lg px-3 py-2 text-[13px] text-slate-600 hover:bg-slate-100">
              Close — keep it running
            </button>
          )}
        </footer>
      </div>
    </div>
  );

  // Portals need document.body, which the server does not have. In the app the
  // dialog only opens after a click, but it must not break a server render.
  return inBrowser ? createPortal(body, document.body) : null;
}

const noop = () => () => {};

/* ------------------------------------------------------------------ steps */

const STEPS = ["Upload", "Review", "Index"];

function Stepper({ step, failed }: { step: number; failed: boolean }) {
  return (
    <ol className="flex items-center gap-2 text-[12px]" aria-label="Progress">
      {STEPS.map((label, i) => {
        const done = i < step;
        const current = i === step;
        return (
          <li key={label} className="flex items-center gap-2">
            {i > 0 && <span className={`h-px w-5 sm:w-8 ${done || current ? "bg-slate-400" : "bg-slate-200"}`} />}
            <span
              aria-current={current ? "step" : undefined}
              className={`flex h-5 w-5 items-center justify-center rounded-full text-[11px] font-semibold ${
                current && failed
                  ? "bg-rose-600 text-white"
                  : done
                    ? "bg-slate-900 text-white"
                    : current
                      ? "bg-slate-900 text-white ring-4 ring-slate-900/10"
                      : "bg-slate-100 text-slate-400"
              }`}
            >
              {done ? <span className="material-symbols-outlined text-[14px]">check</span> : i + 1}
            </span>
            <span className={current ? "font-medium text-slate-900" : "text-slate-500"}>{label}</span>
          </li>
        );
      })}
    </ol>
  );
}

/* ------------------------------------------------------------------ upload */

function UploadForm(props: {
  file: File | null; name: string; slug: string; actNo: string;
  onFile: (f: File | null) => void; onName: (v: string) => void;
  onSlug: (v: string) => void; onActNo: (v: string) => void;
}) {
  const [over, setOver] = useState(false);
  const input = useRef<HTMLInputElement>(null);
  const field = "w-full rounded-lg border border-slate-200 bg-white px-3 py-2 text-[13px] text-slate-900 placeholder:text-slate-400 focus:border-slate-400 focus:outline-none focus:ring-2 focus:ring-slate-900/5";

  function take(f: File | undefined) {
    if (f && (f.type === "application/pdf" || /\.pdf$/i.test(f.name))) props.onFile(f);
  }

  return (
    <div className="space-y-4">
      <div
        onDragOver={(e) => {
          e.preventDefault();
          setOver(true);
        }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => {
          e.preventDefault();
          setOver(false);
          take(e.dataTransfer.files?.[0]);
        }}
        className={`rounded-xl border-2 border-dashed px-4 py-5 transition-colors ${
          over ? "border-slate-500 bg-slate-50" : "border-slate-200"
        }`}
      >
        <input ref={input} type="file" accept="application/pdf,.pdf" className="sr-only"
          onChange={(e) => take(e.target.files?.[0])} />
        {props.file ? (
          <div className="flex items-center gap-3">
            <span className="material-symbols-outlined text-[28px] text-slate-500">picture_as_pdf</span>
            <div className="min-w-0 flex-1">
              <p className="truncate text-[13px] font-medium text-slate-900">{props.file.name}</p>
              <p className="text-[12px] text-slate-500">{(props.file.size / (1024 * 1024)).toFixed(1)} MB</p>
            </div>
            <button type="button" onClick={() => input.current?.click()}
              className="rounded-lg px-2.5 py-1.5 text-[12px] text-slate-600 hover:bg-slate-100">
              Change
            </button>
          </div>
        ) : (
          <button type="button" onClick={() => input.current?.click()}
            className="flex w-full flex-col items-center gap-1 text-center">
            <span className="material-symbols-outlined text-[30px] text-slate-400">upload_file</span>
            <span className="text-[13px] text-slate-700">
              Drop a PDF here, or <span className="font-medium text-slate-900 underline underline-offset-2">choose a file</span>
            </span>
            <span className="text-[12px] text-slate-400">Up to 50 MB. Scanned pages work too.</span>
          </button>
        )}
      </div>

      <label className="block space-y-1.5">
        <span className="text-[13px] font-medium text-slate-700">Title</span>
        <input value={props.name} onChange={(e) => props.onName(e.target.value)}
          placeholder="The Police Act, 1861" className={field} />
      </label>

      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        <label className="block space-y-1.5">
          <span className="text-[13px] font-medium text-slate-700">Short name</span>
          <input value={props.slug} onChange={(e) => props.onSlug(e.target.value)}
            placeholder="police_act" spellCheck={false} className={`${field} font-mono`} />
        </label>
        <label className="block space-y-1.5">
          <span className="text-[13px] font-medium text-slate-700">
            Act number <span className="font-normal text-slate-400">optional</span>
          </span>
          <input value={props.actNo} onChange={(e) => props.onActNo(e.target.value)}
            placeholder="V of 1861" className={field} />
        </label>
      </div>
      <p className="text-[12px] text-slate-500">
        Citations will read <span className="font-mono text-slate-700">{props.slug || "police_act"}-s12</span>.
        The short name can use lowercase letters, digits and underscores.
      </p>
    </div>
  );
}

/* ------------------------------------------------------------------ progress */

function Working({ job }: { job: SourceJob }) {
  const latest = job.log[job.log.length - 1] ?? "Starting";
  return (
    <div className="space-y-4">
      <div className="flex items-center gap-3">
        <span className="h-5 w-5 flex-shrink-0 animate-spin rounded-full border-2 border-slate-200 border-t-slate-800 motion-reduce:animate-none" />
        <div className="min-w-0">
          <p className="text-[13px] font-medium text-slate-900">
            {job.status === "building" ? "Indexing" : "Reading the document"}
          </p>
          <p className="truncate text-[12px] text-slate-500">{latest}</p>
        </div>
      </div>
      <Details lines={job.log} />
    </div>
  );
}

function Details({ lines, open = false }: { lines: string[]; open?: boolean }) {
  return (
    <details open={open} className="group text-[12px]">
      <summary className="cursor-pointer select-none text-slate-500 hover:text-slate-800">Show every step</summary>
      <pre className="mt-2 max-h-52 overflow-y-auto whitespace-pre-wrap rounded-lg bg-slate-50 p-3 font-mono text-[11px] leading-relaxed text-slate-600">
        {lines.join("\n")}
      </pre>
    </details>
  );
}

/* ------------------------------------------------------------------ review */

function Review({ review }: { review: NonNullable<SourceJob["review"]> }) {
  return (
    <div className="space-y-5">
      <Verdict summary={review.pymupdf} error={review.pymupdf_error} />
      {review.pages.map((p) => <PageReview key={p.page} page={p} />)}
    </div>
  );
}

function Verdict({ summary, error }: { summary: ParserSummary | null; error: string | null }) {
  let text: string;
  if (!summary) text = `PyMuPDF could not read this file (${error}). Use Docling.`;
  else if (summary.units === 0)
    text = "PyMuPDF found no numbered sections anywhere in this document, so it is probably not a statute. Docling is likely the right choice.";
  else {
    const share = Math.round(summary.in_units_share * 100);
    const order = summary.in_order === null ? null : Math.round(summary.in_order * 100);
    text = `Across the whole document, PyMuPDF found ${summary.units} numbered sections with ${share}% of the text inside them`
      + (order === null ? "." : `, numbered in order ${order}% of the time.`)
      + " Docling is shown for these pages only; choosing it re-reads the whole file.";
  }
  return <p className="rounded-lg bg-slate-50 px-4 py-3 text-[13px] leading-relaxed text-slate-700">{text}</p>;
}

function PageReview({ page }: { page: ReviewPage }) {
  return (
    <section className="grid gap-4 border-t border-slate-100 pt-5 first:border-t-0 first:pt-0 lg:grid-cols-[minmax(0,0.9fr)_minmax(0,1fr)_minmax(0,1fr)]">
      <figure className="space-y-1.5">
        {/* eslint-disable-next-line @next/next/no-img-element -- a data URL
            from the server; next/image has nothing to optimise */}
        <img src={page.image} alt={`Page ${page.page} of the document`}
          className="w-full rounded-sm border border-slate-200 bg-white shadow-[0_1px_3px_rgba(15,23,42,0.08)]" />
        <figcaption className="text-center text-[12px] text-slate-500">Page {page.page}</figcaption>
      </figure>
      <ParserColumn
        title="PyMuPDF"
        purpose="Numbered sections"
        units={page.pymupdf}
        unitLabel="Section"
        problem={page.pymupdf_error}
        note={page.pymupdf_note}
      />
      <ParserColumn
        title="Docling"
        purpose="Page layout"
        units={page.docling}
        unitLabel="Passage"
        problem={page.docling_error}
        note={page.docling.length ? null : "No text found on this page."}
      />
    </section>
  );
}

function ParserColumn({ title, purpose, units, unitLabel, problem, note }: {
  title: string; purpose: string; units: ReviewUnit[]; unitLabel: string;
  /** The parser failed — red. */
  problem: string | null;
  /** The parser found nothing here — neutral: on a title or contents page
   *  that is exactly right, and red would read as a failure. */
  note?: string | null;
}) {
  const count = units.length;
  return (
    <div className="min-w-0 space-y-2">
      <div className="flex items-baseline justify-between gap-2">
        <h3 className="text-[13px] font-semibold text-slate-900">
          {title} <span className="font-normal text-slate-400">reads {purpose.toLowerCase()}</span>
        </h3>
        <span className="flex-shrink-0 text-[12px] text-slate-500">
          {count ? `${count} ${unitLabel.toLowerCase()}${count === 1 ? "" : "s"}` : `No ${unitLabel.toLowerCase()}s`}
        </span>
      </div>
      {problem && <p className="rounded-lg bg-rose-50 px-3 py-2 text-[12px] leading-relaxed text-rose-800">{problem}</p>}
      {note && (
        <p className="flex gap-2 rounded-lg border border-slate-200 px-3 py-2 text-[12px] leading-relaxed text-slate-600">
          <span className="material-symbols-outlined text-[16px] text-slate-400">info</span>
          <span>{note}</span>
        </p>
      )}
      <div className="max-h-[28rem] space-y-2 overflow-y-auto pr-1">
        {units.map((u, i) => (
          <article key={`${u.number}-${i}`} className="rounded-lg border border-slate-200">
            <div className="flex items-baseline justify-between gap-2 border-b border-slate-100 px-3 py-1.5">
              <p className="min-w-0 truncate text-[12px] font-medium text-slate-800">
                {unitLabel} {u.number}{u.title ? `: ${u.title}` : ""}
              </p>
              <span className="flex-shrink-0 text-[11px] text-slate-400">
                p. {u.pages[0]}{u.pages[1] !== u.pages[0] ? `–${u.pages[1]}` : ""}
              </span>
            </div>
            <p className="max-h-40 overflow-y-auto whitespace-pre-wrap break-words px-3 py-2 font-mono text-[11.5px] leading-relaxed text-slate-700">
              {u.text}{u.truncated ? " …" : ""}
            </p>
            {u.children > 0 && (
              <p className="px-3 pb-2 text-[11px] text-slate-400">
                {u.children} subsection{u.children === 1 ? "" : "s"} recognised inside it
              </p>
            )}
          </article>
        ))}
      </div>
    </div>
  );
}

function ChoiceBar({ summary, busy, onChoose, onDiscard }: {
  summary: ParserSummary | null; busy: boolean;
  onChoose: (parser: "pymupdf" | "docling") => void; onDiscard: () => void;
}) {
  const pymupdfUsable = Boolean(summary && summary.units > 0);
  // Emphasis follows the evidence, not a fixed favourite: a statute PyMuPDF
  // read cleanly (almost all text inside sections, numbering in order) makes
  // PyMuPDF the primary button; anything else makes Docling primary.
  const pymupdfClean = pymupdfUsable && summary!.in_units_share >= 0.8
    && (summary!.in_order === null || summary!.in_order >= 0.9);
  const option = "flex flex-col items-start rounded-xl border px-4 py-2.5 text-left transition-colors disabled:cursor-not-allowed disabled:opacity-40";
  const primary = "border-slate-900 bg-slate-900 hover:bg-slate-800";
  const secondary = "border-slate-200 hover:border-slate-400 hover:bg-slate-50";
  return (
    <div className="grid w-full grid-cols-2 gap-2 sm:flex sm:items-center">
      <button type="button" disabled={busy} onClick={onDiscard}
        className="order-last col-span-2 rounded-lg px-3 py-1.5 text-[13px] text-slate-600 hover:bg-slate-100 sm:order-none sm:col-span-1 sm:mr-auto sm:py-2">
        Discard
      </button>
      <button type="button" disabled={busy || !pymupdfUsable} onClick={() => onChoose("pymupdf")}
        title={pymupdfUsable ? undefined : "PyMuPDF found no numbered sections"}
        className={`${option} ${pymupdfClean ? primary : secondary}`}>
        <span className={`text-[13px] font-medium ${pymupdfClean ? "text-white" : "text-slate-900"}`}>Use PyMuPDF</span>
        <span className={`hidden sm:block text-[12px] ${pymupdfClean ? "text-slate-300" : "text-slate-500"}`}>Keeps numbered sections. Best for Acts.</span>
      </button>
      <button type="button" disabled={busy} onClick={() => onChoose("docling")}
        className={`${option} ${pymupdfClean ? secondary : primary}`}>
        <span className={`text-[13px] font-medium ${pymupdfClean ? "text-slate-900" : "text-white"}`}>Use Docling</span>
        <span className={`hidden sm:block text-[12px] ${pymupdfClean ? "text-slate-500" : "text-slate-300"}`}>Reads by layout. Best for judgments and scans.</span>
      </button>
    </div>
  );
}
