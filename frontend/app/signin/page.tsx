"use client";

import { Suspense, useEffect, useState } from "react";
import { useRouter, useSearchParams } from "next/navigation";
import { useAuth } from "@/components/auth/AuthProvider";
import { BrandMark } from "@/components/brand/BrandMark";

/**
 * Sign in or create an account, on its own page.
 *
 * A page rather than a popover: this is the one place where someone decides
 * whether their legal questions are stored, and that decision deserves room to
 * read the consequence rather than a dropdown that closes on an outside click.
 * It also gives the flow a URL, so "sign in" can be linked to from anywhere and
 * survives a refresh mid-typing.
 */
export default function SignInPage() {
  // `useSearchParams` makes the form depend on the request URL, which does not
  // exist when the page is prerendered at build time. Without a Suspense
  // boundary `next build` fails outright ("useSearchParams() should be wrapped
  // in a suspense boundary"); with one, the shell is prerendered and the form
  // renders in the browser.
  return (
    <Suspense fallback={<div className="min-h-screen bg-slate-50" />}>
      <SignInForm />
    </Suspense>
  );
}

function SignInForm() {
  const router = useRouter();
  const params = useSearchParams();
  const { user, ready, configured, signIn, signUp } = useAuth();
  const [mode, setMode] = useState<"in" | "up">(params.get("mode") === "in" ? "in" : "up");
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState<string | null>(null);

  useEffect(() => {
    // Already signed in — nothing to do here.
    if (ready && user) router.replace("/");
  }, [ready, user, router]);

  async function submit(event: React.FormEvent) {
    event.preventDefault();
    setBusy(true);
    setMessage(null);
    const error = mode === "in" ? await signIn(email, password) : await signUp(email, password);
    setBusy(false);
    if (error) {
      setMessage(error);
      return;
    }
    router.replace("/");
  }

  return (
    <div className="flex min-h-screen items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-sm space-y-5">
        <div className="space-y-1.5 text-center">
          <BrandMark size={80} variant="full" eager className="mx-auto rounded-2xl shadow-sm" />
          <h1 className="font-serif text-lg font-semibold text-slate-900">
            {mode === "in" ? "Sign in" : "Create an account"}
          </h1>
          <p className="text-xs text-slate-500">
            Conversations are saved to your account and available on any device.
          </p>
        </div>

        {!configured ? (
          <p className="rounded-lg border border-amber-200 bg-amber-50 px-3 py-2 text-[11px] text-amber-800">
            Accounts are not configured on this deployment. You can keep using
            the assistant without one — nothing will be saved.
          </p>
        ) : (
          <form onSubmit={submit} className="space-y-2.5 rounded-xl border border-slate-200 bg-white p-4">
            <input
              type="email"
              required
              autoComplete="email"
              autoFocus
              value={email}
              onChange={(event) => setEmail(event.target.value)}
              placeholder="you@example.com"
              className="w-full rounded-md border border-slate-200 px-2.5 py-2 text-xs focus:border-slate-800 focus:outline-none"
            />
            <input
              type="password"
              required
              minLength={8}
              autoComplete={mode === "in" ? "current-password" : "new-password"}
              value={password}
              onChange={(event) => setPassword(event.target.value)}
              placeholder="Password (at least 8 characters)"
              className="w-full rounded-md border border-slate-200 px-2.5 py-2 text-xs focus:border-slate-800 focus:outline-none"
            />
            {message && (
              <p className="rounded-md bg-amber-50 px-2 py-1.5 text-[11px] text-amber-800">{message}</p>
            )}
            <button
              type="submit"
              disabled={busy}
              className="w-full rounded-md bg-slate-900 px-2 py-2 text-xs font-medium text-white hover:bg-slate-800 disabled:opacity-50"
            >
              {busy ? "…" : mode === "in" ? "Sign in" : "Create account"}
            </button>
            <button
              type="button"
              onClick={() => {
                setMode((m) => (m === "in" ? "up" : "in"));
                setMessage(null);
              }}
              className="w-full text-[11px] text-slate-500 hover:text-slate-800"
            >
              {mode === "in" ? "Create an account instead" : "I already have an account"}
            </button>
          </form>
        )}

        <button
          type="button"
          onClick={() => router.replace("/?guest=1")}
          className="w-full text-center text-[11px] text-slate-500 hover:text-slate-800"
        >
          Continue without an account — nothing is saved
        </button>
      </div>
    </div>
  );
}
