"""The Acts the corpus holds — asked of the catalog, never listed in code.

Every "which Acts do we have?" in the app answers from here: the planner's
scope, citation keys, short names in citations, the sentences that say what the
assistant covers, spotting which Act a piece of text refers to. They used to be
separate hardcoded three-entry lists, which is why a fourth Act, loaded and
searchable, still came back keyed `act-s13` and was ruled out of scope by a
planner told there were "exactly three Acts".

The catalog is the source because it is what the app actually serves: loaded
from Supabase, rebuilt within a minute of an Act being loaded or removed. So
adding or removing an Act changes all of the above with no code edit and no
restart. Nothing is cached here — the catalog is an in-memory database and
these are small reads.

Short names are derived, not stored: "The Arms Act, 1878" -> "Arms Act". That
is right for most titles; conventional abbreviations like "CrPC" cannot be
derived, so the CrPC is shown as "Code of Criminal Procedure". The slug is also
accepted wherever text is matched, and `crpc` is its slug.
"""

from __future__ import annotations

import re


def all() -> list[dict]:  # noqa: A001 - the module reads as acts.all()
    """Every Act in the catalog, with its derived short name, by slug."""
    from app.rag import catalog

    return [{**a, "short": short_name(a["name"])} for a in catalog.acts()]


def short_name(name: str) -> str:
    """'The Penal Code, 1860' -> 'Penal Code'. Leading 'The' and year dropped."""
    s = re.sub(r"^\s*the\s+", "", name or "", flags=re.I)
    s = re.sub(r",?\s*\(?\d{4}\)?\s*$", "", s)
    return s.strip() or (name or "").strip()


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", (text or "").strip().lower())


def by_name(name: str) -> dict | None:
    """The Act whose full title this is, compared case- and space-insensitively."""
    want = _norm(name)
    return next((a for a in all() if _norm(a["name"]) == want), None)


def slug_for_name(name: str) -> str | None:
    """The citation-key prefix for an Act title, or None if it is not held."""
    a = by_name(name)
    return a["slug"] if a else None


def short_for_name(name: str) -> str:
    """Display name for an Act title — the held Act's short name, else derived."""
    a = by_name(name)
    return a["short"] if a else short_name(name)


def needles(a: dict, strict: bool = True) -> tuple[str, ...]:
    """Lower-case phrases that identify this Act in free text.

    Strict (the default, for reading a user's question or a pasted document):
    the full title, the short name, a slug that is an abbreviation (`crpc`),
    and a multi-word core ("criminal procedure"). Not a single ordinary word:
    "the evidence shows he was armed" names no Act, and a bare "evidence" or
    "arms" would say it names two.

    Loose (for a citation string the model wrote, like "Evidence s.25" or
    "Penal s.379"): the single-word core as well. There the context is already
    a citation, so the bare word is safe and often all there is.
    """
    name, short, slug = _norm(a["name"]), _norm(a["short"]), _norm(a["slug"])
    core = re.sub(r"^(code of|the)\s+", "", short)
    core = re.sub(r"\s+(act|code|ordinance|order|rules)$", "", core)
    found = {name, short}
    if slug != core:
        found.add(slug)                      # an abbreviation, e.g. crpc
    if " " in core or not strict:
        found.add(core)
    if not strict:
        found.add(slug)
    return tuple(sorted((n for n in found if len(n) >= 4), key=len, reverse=True))


def _prep(text: str) -> str:
    """Lower-case, whitespace collapsed, dots dropped — WITHOUT trimming, so a
    position in the original maps onto this by preparing the prefix the same way."""
    return re.sub(r"\s+", " ", (text or "").lower()).replace(".", "")


def _positions(text: str, strict: bool) -> list[tuple[int, str]]:
    """(position, slug) for every mention of every held Act in the text.

    Dots are dropped before matching, so "Cr.P.C." reads as "crpc" — the
    dotted spelling is how the abbreviation is often written.
    """
    low = _prep(text)
    out = []
    for a in all():
        for n in needles(a, strict):
            for m in re.finditer(rf"(?<![a-z]){re.escape(n.replace('.', ''))}(?![a-z])", low):
                out.append((m.start(), a["slug"]))
    return out


def mentioned(text: str, strict: bool = True) -> list[str]:
    """Slugs of every held Act the text names, in order of first mention."""
    first: dict[str, int] = {}
    for pos, slug in _positions(text, strict):
        first[slug] = min(pos, first.get(slug, pos))
    return sorted(first, key=first.get)


def nearest(text: str, index: int, strict: bool = True) -> str | None:
    """The held Act named closest to `index` in the text, or None.

    For a citation inside a passage that names more than one Act: "under
    section 54 of the CrPC and section 25 of the Evidence Act" — each number
    belongs to the Act beside it, not to whichever Act is checked first.
    """
    found = _positions(text, strict)
    if not found:
        return None
    at = len(_prep(text[:index]))          # the same position, in prepared text
    return min(found, key=lambda f: abs(f[0] - at))[1]


def hint_slug(text: str, strict: bool = True) -> str | None:
    """The one held Act the text names, or None when it names none or several."""
    found = mentioned(text, strict)
    return found[0] if len(found) == 1 else None


def listing(conjunction: str = "and") -> str:
    """'the Arms Act, 1878, the Code of Criminal Procedure, 1898 and …'."""
    names = [a["name"] for a in all()]
    names = [re.sub(r"^The\s", "the ", n) for n in names]
    if not names:
        return "no Acts"
    if len(names) == 1:
        return names[0]
    return ", ".join(names[:-1]) + f" {conjunction} " + names[-1]
