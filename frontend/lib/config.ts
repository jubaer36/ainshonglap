/**
 * Where the backend is. Read once, here, rather than in each module.
 *
 * `NEXT_PUBLIC_API_URL` is compiled into the browser bundle at build time, so on
 * a host like Render it must be set before the build, not only at runtime.
 * `||` rather than `??`: an empty variable (set but blank in a dashboard) must
 * fall back too — otherwise every call goes to the frontend's own origin. A
 * trailing slash is trimmed so paths never become `//api/...`.
 */
export const API_BASE_URL = (process.env.NEXT_PUBLIC_API_URL || "http://localhost:8000").replace(
  /\/+$/,
  "",
);
