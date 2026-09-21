"use client";

import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
} from "react";
import type { Session, User } from "@supabase/supabase-js";
import { authConfigured, supabase } from "@/lib/supabase";

interface AuthState {
  user: User | null;
  /** Null while the first session check is still running, so the UI can avoid
   *  flashing "Sign in" at someone who is already signed in. */
  ready: boolean;
  configured: boolean;
  signIn: (email: string, password: string) => Promise<string | null>;
  signUp: (email: string, password: string) => Promise<string | null>;
  signOut: () => Promise<void>;
}

const Context = createContext<AuthState>({
  user: null,
  ready: false,
  configured: false,
  signIn: async () => "auth is not configured",
  signUp: async () => "auth is not configured",
  signOut: async () => {},
});

export const useAuth = () => useContext(Context);

/**
 * Session state, shared across the app.
 *
 * Signing in is optional throughout. Anonymous use is the default and keeps
 * working exactly as before — history in the browser, no account, nothing about
 * the questions leaving the machine. Signing in adds storage across devices;
 * it does not gate the assistant.
 */
export function AuthProvider({ children }: { children: React.ReactNode }) {
  const [session, setSession] = useState<Session | null>(null);
  const [ready, setReady] = useState(!authConfigured);

  useEffect(() => {
    const client = supabase();
    if (!client) return;
    let cancelled = false;

    client.auth.getSession().then(({ data }) => {
      if (cancelled) return;
      setSession(data.session);
      setReady(true);
    });
    // Fires on sign-in, sign-out and token refresh, so the bearer the API gets
    // is never a stale one.
    const { data: sub } = client.auth.onAuthStateChange((_event, next) => {
      setSession(next);
      setReady(true);
    });
    return () => {
      cancelled = true;
      sub.subscription.unsubscribe();
    };
  }, []);

  const signIn = useCallback(async (email: string, password: string) => {
    const client = supabase();
    if (!client) return "auth is not configured";
    const { error } = await client.auth.signInWithPassword({ email, password });
    return error?.message ?? null;
  }, []);

  const signUp = useCallback(async (email: string, password: string) => {
    const client = supabase();
    if (!client) return "auth is not configured";
    const { data, error } = await client.auth.signUp({ email, password });
    if (error) return error.message;
    // With email confirmation on (the Supabase default) sign-up succeeds but
    // returns no session. Saying so beats a form that appears to do nothing.
    if (!data.session) return "Check your email to confirm the account.";
    return null;
  }, []);

  const signOut = useCallback(async () => {
    await supabase()?.auth.signOut();
  }, []);

  const value = useMemo<AuthState>(
    () => ({
      user: session?.user ?? null,
      ready,
      configured: authConfigured,
      signIn,
      signUp,
      signOut,
    }),
    [session, ready, signIn, signUp, signOut],
  );

  return <Context.Provider value={value}>{children}</Context.Provider>;
}
