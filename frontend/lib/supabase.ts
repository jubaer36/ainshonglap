"use client";

import { createClient, type SupabaseClient } from "@supabase/supabase-js";

/**
 * Supabase in the browser, for **authentication only**.
 *
 * No table reads or writes happen here. Conversations go through the FastAPI
 * service, which holds the service-role key and filters every statement by the
 * user id it reads from a verified token. That keeps one place responsible for
 * isolation instead of two, and means the browser only ever needs the anon key.
 *
 * Returns null when Supabase is not configured, which is a supported state:
 * without it the assistant runs anonymously and keeps history in the browser.
 */
const url = process.env.NEXT_PUBLIC_SUPABASE_URL;
const anonKey = process.env.NEXT_PUBLIC_SUPABASE_ANON_KEY;

let cached: SupabaseClient | null = null;

export function supabase(): SupabaseClient | null {
  if (!url || !anonKey) return null;
  if (!cached) {
    cached = createClient(url, anonKey, {
      auth: {
        persistSession: true,
        autoRefreshToken: true,
        // The app is a single page with no OAuth redirect handling yet; parsing
        // the URL for a session would only matter once a provider is enabled.
        detectSessionInUrl: true,
      },
    });
  }
  return cached;
}

export const authConfigured = Boolean(url && anonKey);

/** How long to wait for a session before giving up and calling it anonymous. */
const SESSION_TIMEOUT_MS = 3000;

/**
 * The current access token, or null.
 *
 * Bounded, because this sits in front of every API call. `getSession()` can go
 * to the network to refresh an expired token, and if Supabase is slow or
 * unreachable an unbounded wait here stalls requests that have nothing to do
 * with auth — the model list and the statute browser would fail with a timeout
 * that looks like the backend being down.
 *
 * Signing in is optional, so falling back to anonymous is a real answer rather
 * than a papered-over failure: the request goes out without a bearer, and the
 * only thing lost is storage.
 */
export async function accessToken(): Promise<string | null> {
  const client = supabase();
  if (!client) return null;
  try {
    const session = await Promise.race([
      client.auth.getSession(),
      new Promise<null>((resolve) => setTimeout(() => resolve(null), SESSION_TIMEOUT_MS)),
    ]);
    if (!session) {
      console.warn("supabase getSession timed out; continuing without a token");
      return null;
    }
    return session.data.session?.access_token ?? null;
  } catch (error) {
    console.warn("supabase getSession failed; continuing without a token", error);
    return null;
  }
}
