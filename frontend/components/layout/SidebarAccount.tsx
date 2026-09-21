"use client";

import Link from "next/link";
import { useAuth } from "@/components/auth/AuthProvider";

/**
 * Who you are, at the foot of the sidebar.
 *
 * Signed in: the account and a way out of it. As a guest: a reminder that
 * nothing is being kept, and an offer to change that.
 *
 * The guest line states the consequence rather than selling the feature —
 * "this conversation disappears when you close the tab" is the fact someone
 * needs before they lose an hour of work, and it is also the reason the guest
 * option exists at all.
 */
export function SidebarAccount() {
  const { user, ready, configured, signOut } = useAuth();

  if (!configured || !ready) return null;

  if (user) {
    const name = (user.user_metadata?.name as string) || user.email || "Signed in";
    const initial = name.trim().charAt(0).toUpperCase() || "?";
    return (
      <div className="flex items-center gap-2 border-t border-slate-200 p-3">
        <div className="flex h-7 w-7 flex-shrink-0 items-center justify-center rounded-full bg-slate-200 text-[11px] font-semibold text-slate-700">
          {initial}
        </div>
        <div className="min-w-0 flex-1">
          <div className="truncate text-[11px] font-medium text-slate-800" title={name}>
            {name}
          </div>
          <div className="text-[10px] text-slate-400">Conversations are saved</div>
        </div>
        <button
          type="button"
          onClick={() => void signOut()}
          title="Sign out"
          aria-label="Sign out"
          className="rounded p-1 text-slate-400 hover:bg-slate-100 hover:text-slate-700"
        >
          <span className="material-symbols-outlined text-[16px]">logout</span>
        </button>
      </div>
    );
  }

  return (
    <div className="border-t border-slate-200 p-3">
      <div className="mb-2 flex items-start gap-1.5 text-[10px] leading-relaxed text-slate-500">
        <span className="material-symbols-outlined mt-px text-[13px]">visibility_off</span>
        <span>
          Guest — nothing is saved. This conversation disappears when you close
          the tab.
        </span>
      </div>
      <Link
        href="/signin"
        className="flex w-full items-center justify-center gap-1.5 rounded-lg bg-slate-900 px-3 py-2 text-[11px] font-medium text-white transition-colors hover:bg-slate-800"
      >
        <span className="material-symbols-outlined text-[14px]">bookmark</span>
        Sign up to keep your conversations
      </Link>
    </div>
  );
}
