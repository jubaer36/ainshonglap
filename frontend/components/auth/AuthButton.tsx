"use client";

import Link from "next/link";
import { useAuth } from "./AuthProvider";

/**
 * A link to the sign-in page, for a guest.
 *
 * Signed-in identity lives at the foot of the sidebar (`SidebarAccount`) rather
 * than here — it belongs with the conversation list it governs, and the header
 * was already carrying four controls.
 */
export function AuthButton() {
  const { user, ready, configured } = useAuth();
  if (!configured || !ready || user) return null;

  return (
    <Link
      href="/signin"
      title="Sign in"
      className="flex items-center gap-1.5 whitespace-nowrap rounded-md border border-slate-200 px-2 py-1 text-[11px] text-slate-600 hover:bg-slate-50"
    >
      <span className="material-symbols-outlined text-[14px]">login</span>
      {/* Icon alone on phones, where the header has room for the name and a
          few icons but not for labels beside them. */}
      <span className="hidden sm:inline">Sign in</span>
    </Link>
  );
}
