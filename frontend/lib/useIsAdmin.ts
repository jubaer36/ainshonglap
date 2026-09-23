"use client";

import { useEffect, useState } from "react";
import { useAuth } from "@/components/auth/AuthProvider";
import { checkAdmin } from "./sources";

/**
 * Whether the signed-in user may add and remove sources. Asked of the server,
 * because only it can check the token against ADMIN_EMAILS; re-asked when the
 * user changes. False for guests, and false while asking.
 */
export function useIsAdmin(): boolean {
  const { user } = useAuth();
  const [admin, setAdmin] = useState(false);
  useEffect(() => {
    let live = true;
    if (!user) {
      queueMicrotask(() => live && setAdmin(false));
    } else {
      checkAdmin().then((value) => live && setAdmin(value));
    }
    return () => {
      live = false;
    };
  }, [user]);
  return admin;
}
