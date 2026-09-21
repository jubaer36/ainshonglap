"use client";

import Link from "next/link";
import { useAuth } from "./AuthProvider";

interface WelcomeGateProps {
  onContinueAsGuest: () => void;
}

/**
 * The first thing a new visitor sees: use it now, or sign in to keep anything.
 *
 * The two options differ in exactly one way, and it is stated plainly rather
 * than implied — guest questions are never written down anywhere, and signed-in
 * ones are stored on the server. For a tool people bring real legal trouble to,
 * "where does what I type end up" should be a choice made knowingly at the
 * start, not discovered later in a privacy policy.
 *
 * Guest is listed first and is a full option, not a degraded trial. Nothing
 * about the assistant is gated: the same corpus, the same citations, the same
 * verification.
 */
export function WelcomeGate({ onContinueAsGuest }: WelcomeGateProps) {
  const { configured } = useAuth();

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-50 px-4">
      <div className="w-full max-w-md space-y-5">
        <div className="text-center space-y-1.5">
          <div className="mx-auto flex h-11 w-11 items-center justify-center rounded-xl bg-slate-900 font-serif text-sm font-bold text-white">
            আইন
          </div>
          <h1 className="font-serif text-lg font-semibold text-slate-900">AinShonglap</h1>
          <p className="text-xs text-slate-500">
            Answers on Bangladesh criminal law, cited to the Code of Criminal
            Procedure 1898, the Penal Code 1860 and the Evidence Act 1872.
          </p>
        </div>

        <div className="space-y-2.5">
          <button
            type="button"
            onClick={onContinueAsGuest}
            className="w-full rounded-xl border border-slate-200 bg-white p-4 text-left transition-colors hover:border-slate-300 hover:bg-slate-50"
          >
            <div className="flex items-center gap-2 text-sm font-medium text-slate-900">
              <span className="material-symbols-outlined text-[18px] text-slate-500">
                visibility_off
              </span>
              Continue without an account
            </div>
            <p className="mt-1 text-[11px] leading-relaxed text-slate-500">
              Nothing is saved. Your questions and answers stay on this screen
              and disappear when you close the tab — they are never written to
              this device or to our servers.
            </p>
          </button>

          {configured && (
            <Link
              href="/signin"
              className="block w-full rounded-xl border border-slate-900 bg-slate-900 p-4 text-left text-white transition-colors hover:bg-slate-800"
            >
              <div className="flex items-center gap-2 text-sm font-medium">
                <span className="material-symbols-outlined text-[18px]">bookmark</span>
                Sign in or create an account
              </div>
              <p className="mt-1 text-[11px] leading-relaxed text-slate-300">
                Conversations are saved to your account and available on any
                device. Your questions are stored on our servers so they can be
                shown back to you.
              </p>
            </Link>
          )}
        </div>

        <p className="text-center text-[10px] text-slate-400">
          Legal information, not legal advice.
        </p>
      </div>
    </div>
  );
}
