"""Remove an Act from the corpus — from Supabase, and from the local fallback.

    python scripts/remove_act.py --act arms              # asks you to confirm
    python scripts/remove_act.py --act arms --yes        # no prompt
    python scripts/remove_act.py --act arms --supabase-only

What it does, in order:

    1. Supabase   deletes the Act's row; every section, subsection, reference,
                  vector and load record under it goes with it (cascade). The
                  app stops searching it at once and drops it from its catalog,
                  its planner scope and the statute browser within a minute —
                  no restart. This is the step that matters for users.

    2. Local      moves Laws/main/parsed/<slug>.json and chunks/<slug>.json to
                  Laws/removed/<slug>/ (untracked, recoverable — not deleted),
                  then rebuilds the local catalog and FAISS index without it.
                  Those are the fallback: left alone, a Supabase outage would
                  bring the removed Act back into answers.

    3. Re-sync    reloads Supabase from the rebuilt index. Vector positions shift
                  when an Act leaves the index, and the verifier compares the two
                  stores by position; the loader skips any Act whose content did
                  not change. Then verify_supabase.py checks both stores agree.

Step 2 embeds nothing: build_index reuses every remaining chunk's vector.
`--supabase-only` skips steps 2 and 3, and says plainly that the fallback still
holds the Act.

The core Acts (config.CORE_ACTS — the CrPC, the Penal Code, the Evidence Act)
are refused outright.

The source PDF is left where it is. Nothing re-adds an Act on its own: the
pipeline reads Laws/main/parsed/, and the Act's file is no longer there. To put
it back, move the two files back and run chunk -> index -> catalog -> load.

The frontend does the same through /api/sources for users listed in
ADMIN_EMAILS; this is the command-line route to it.
"""

from __future__ import annotations

import argparse
import pathlib
import shutil
import subprocess
import sys

BACKEND = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND))

from app.rag import config  # noqa: E402

REMOVED = config.LAWS.parent / "removed"


def run(*script_and_args: str) -> None:
    """Run another pipeline script with this interpreter; stop if it fails."""
    cmd = [sys.executable, str(BACKEND / "scripts" / script_and_args[0]), *script_and_args[1:]]
    print(f"\n$ {' '.join(cmd[1:])}")
    if subprocess.run(cmd, cwd=BACKEND).returncode != 0:
        raise SystemExit(f"{script_and_args[0]} failed — stopping. Supabase no longer "
                         "has the Act; re-run this command once the problem is fixed.")


def main() -> None:
    ap = argparse.ArgumentParser(description="Remove an Act from the corpus.")
    ap.add_argument("--act", required=True, help="the Act's slug, e.g. arms")
    ap.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    ap.add_argument("--supabase-only", action="store_true",
                    help="remove it from Supabase only; the local fallback keeps it")
    args = ap.parse_args()
    slug = args.act.strip().lower()
    if slug in config.CORE_ACTS:
        raise SystemExit(f"'{slug}' is a core Act and cannot be removed "
                         f"(core: {', '.join(sorted(config.CORE_ACTS))}).")

    from app.db import supabase

    client = supabase.client()
    row = client.table("law_acts").select("slug,name,n_sections").eq("slug", slug).execute().data
    local = (config.PARSED_DIR / f"{slug}.json").exists()
    if not row and not local:
        raise SystemExit(f"no Act '{slug}' in Supabase or in {config.PARSED_DIR}")
    others = [r["slug"] for r in client.table("law_acts").select("slug").execute().data
              if r["slug"] != slug]
    name = row[0]["name"] if row else slug

    print(f"About to remove: {name} ({slug})"
          + (f", {row[0]['n_sections']} sections" if row else " (local files only)"))
    print(f"Acts that will remain: {', '.join(others) or 'NONE — the assistant will have nothing to answer from'}")
    if not args.yes:
        if input(f"Type the slug '{slug}' to confirm: ").strip().lower() != slug:
            raise SystemExit("Not confirmed. Nothing was removed.")

    # 1. Supabase — the step users see.
    if row:
        client.table("law_acts").delete().eq("slug", slug).execute()
        left = client.table("law_chunks").select("id", count="exact").eq("act_slug", slug).limit(0).execute().count
        print(f"\nSupabase: removed. Vectors left for '{slug}': {left}. "
              "The app drops it within a minute.")

    if args.supabase_only:
        print(f"\n! The local fallback still has '{slug}'. If Supabase is ever unreachable, "
              "the app falls back to it and the removed Act can reappear in answers. "
              "Run again without --supabase-only to remove it there too.")
        return

    # 2. Local — the fallback. Moved, not deleted.
    dest = REMOVED / slug
    dest.mkdir(parents=True, exist_ok=True)
    for folder in (config.PARSED_DIR, config.CHUNKS_DIR):
        f = folder / f"{slug}.json"
        if f.exists():
            shutil.move(str(f), str(dest / f"{folder.name}-{slug}.json"))
            print(f"moved {f.relative_to(config.LAWS.parent)} -> {dest.relative_to(config.LAWS.parent)}/")

    run("build_catalog.py")
    run("verify/verify_catalog.py")
    run("build_index.py")

    # 3. Re-sync Supabase with the rebuilt index, then check both agree.
    run("load_supabase.py", "--all")
    run("verify/verify_supabase.py")
    print(f"\nDone. '{slug}' is gone from Supabase and from the local fallback. "
          f"Its files are kept in {dest}.")


if __name__ == "__main__":
    main()
