import { API_BASE_URL } from "./config";
import { accessToken } from "./supabase";

/**
 * Adding and removing sources — the admin half of the statute list.
 *
 * Every call sends the signed-in user's token; the server decides whether they
 * are an admin (ADMIN_EMAILS) and refuses otherwise. Nothing here is trusted
 * for protection: hiding a button is a courtesy, the check is on the server.
 */

export type JobStatus =
  | "queued" | "reviewing" | "review" | "building" | "done" | "failed" | "discarded";

export interface ReviewUnit {
  number: string;
  title: string | null;
  pages: [number, number];
  text: string;
  truncated: boolean;
  children: number;
}

export interface ReviewPage {
  page: number;
  image: string;
  pymupdf: ReviewUnit[];
  pymupdf_error: string | null;
  pymupdf_note: string | null;
  docling: ReviewUnit[];
  docling_error: string | null;
}

export interface ParserSummary {
  units: number;
  in_units_share: number;
  preamble_chars: number;
  in_order: number | null;
}

export interface SourceJob {
  id: string;
  kind: "add" | "remove";
  slug: string;
  name: string;
  status: JobStatus;
  log: string[];
  error: string | null;
  review?: { pages: ReviewPage[]; pymupdf: ParserSummary | null; pymupdf_error: string | null };
}

export const TERMINAL: JobStatus[] = ["done", "failed", "discarded"];

async function headers(extra: Record<string, string> = {}): Promise<Record<string, string>> {
  const token = await accessToken();
  return token ? { ...extra, Authorization: `Bearer ${token}` } : extra;
}

async function call<T>(path: string, init: RequestInit = {}): Promise<T> {
  const response = await fetch(`${API_BASE_URL}${path}`, init);
  if (!response.ok) {
    // FastAPI puts the reason in `detail`; show that rather than a status code.
    let detail = `${response.status}`;
    try {
      const body = (await response.json()) as { detail?: string };
      if (body.detail) detail = body.detail;
    } catch {
      /* not JSON */
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

export async function checkAdmin(): Promise<boolean> {
  try {
    // `manage` is false where the server cannot run the ingestion pipeline (the
    // deployed container): an admin there gets no add/remove controls rather
    // than controls that fail. Absent on older servers, so treated as true.
    const body = await call<{ admin: boolean; manage?: boolean }>("/api/sources/admin", {
      headers: await headers(),
    });
    return body.admin && body.manage !== false;
  } catch {
    return false;
  }
}

export async function uploadSource(input: {
  file: File; slug: string; name: string; actNo: string;
}): Promise<SourceJob> {
  const form = new FormData();
  form.append("file", input.file);
  form.append("slug", input.slug);
  form.append("name", input.name);
  form.append("act_no", input.actNo);
  return call<SourceJob>("/api/sources", { method: "POST", body: form, headers: await headers() });
}

export async function getJob(id: string): Promise<SourceJob> {
  return call<SourceJob>(`/api/sources/jobs/${id}`, { headers: await headers() });
}

export async function chooseParser(id: string, parser: "pymupdf" | "docling"): Promise<SourceJob> {
  return call<SourceJob>(`/api/sources/jobs/${id}/choose`, {
    method: "POST",
    headers: await headers({ "Content-Type": "application/json" }),
    body: JSON.stringify({ parser }),
  });
}

export async function discardJob(id: string): Promise<SourceJob> {
  return call<SourceJob>(`/api/sources/jobs/${id}/discard`, { method: "POST", headers: await headers() });
}

export async function removeSource(slug: string): Promise<SourceJob> {
  return call<SourceJob>(`/api/sources/${slug}`, { method: "DELETE", headers: await headers() });
}

/** "The Police Act, 1861" -> "police_act": a starting suggestion, editable. */
export function suggestSlug(title: string): string {
  return title
    .toLowerCase()
    .replace(/\.pdf$/, "")
    .replace(/^\s*the\s+/, "")
    .replace(/,?\s*\(?\d{4}\)?.*$/, "")
    .replace(/[^a-z0-9]+/g, "_")
    .replace(/^_+|_+$/g, "")
    .slice(0, 31);
}
