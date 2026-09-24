"""Citation checks, no model involved: the cited section must have been
retrieved (C2), and its quote must appear in it word for word (C3).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

# Curly quotes, long dashes and non-breaking spaces all differ between the PDF
# text and what a model re-emits. None of those differences are substantive.
_QUOTES = str.maketrans({"‘": "'", "’": "'", "“": '"',
                         "”": '"', "–": "-", "—": "-",
                         " ": " "})


def norm(s: str) -> str:
    s = unicodedata.normalize("NFKC", s or "").translate(_QUOTES)
    return re.sub(r"\s+", " ", s).strip().lower()


# Digit-boundary guards matter more than they look: without them "The Penal
# Code, 1860, Section 379" resolves on "186" — three digits lifted out of the
# year — and the real citation is never seen. Years are 4 digits, so refusing to
# match inside a longer run of digits excludes them without special-casing.
SECTION_RE = re.compile(r"(?<!\d)(\d{1,3}[A-Z]{0,2})(?!\d)")
# A number introduced by a section marker beats a bare one elsewhere in the string.
MARKED_RE = re.compile(r"(?:section|sec\.?|s\.|§)\s*(?<!\d)(\d{1,3}[A-Z]{0,2})(?!\d)",
                       re.I)


def resolve(cited: str, retrieved) -> object | None:
    """Map a model's free-form section reference onto a retrieved section.

    The model does not reliably emit a bare number. Asked for "154" it may
    return "The Penal Code, 1860, Section 379" — a perfectly unambiguous
    citation in a different shape. An exact string compare rejected exactly
    that, which looked like a hallucinating model and was a strict checker.

    Ambiguity is the real risk, and it is not hypothetical: the three acts
    overlap in numbering, so "section 25" names a provision in each of them.
    Matching on the number alone would let a claim cite Evidence Act s.25 while
    the retrieved section was Penal Code s.25 — Document-Level Retrieval
    Mismatch, arriving through the citation field instead of the retriever.

    So: extract the number, and where several retrieved sections share it,
    require the Act to be named too. An unresolvable reference fails C2, which
    is the correct outcome for a citation nobody can follow.
    """
    cited = cited or ""
    m = MARKED_RE.search(cited) or SECTION_RE.search(cited)
    if not m:
        return None
    num = m.group(1)
    cands = [r for r in retrieved if str(r.section) == num]
    if len(cands) == 1:
        return cands[0]
    if not cands:
        return None
    hay = norm(cited)
    named = [r for r in cands if norm(r.act) in hay or _names(r.act, cited)]
    return named[0] if len(named) == 1 else None


def _names(act: str, cited: str) -> bool:
    """Does a model-written citation ("CrPC s.154", "Evidence s.25") name `act`?

    Loose matching (`acts.needles(strict=False)`): the string is already a
    citation, so a bare "Penal" or "Evidence" is safe here and often all the
    model wrote. The phrases come from the catalog, so an Act loaded later is
    recognised without a code change.
    """
    from app.rag import acts

    return acts.by_name(act) is not None and \
        acts.by_name(act)["slug"] in acts.mentioned(cited, strict=False)


@dataclass
class ClaimCheck:
    text: str
    section: str
    quote: str
    cited_retrieved: bool     # C2
    quote_found: bool         # C3
    resolved_section: str | None = None   # the retrieved section it resolved to

    @property
    def ok(self) -> bool:
        return self.cited_retrieved and self.quote_found

    @property
    def failure(self) -> str | None:
        if not self.cited_retrieved:
            return "cites a section that was not retrieved"
        if not self.quote_found:
            return "quote does not appear in the cited section"
        return None


@dataclass
class VerifiedAnswer:
    claims: list[ClaimCheck]

    @property
    def all_ok(self) -> bool:
        return bool(self.claims) and all(c.ok for c in self.claims)

    @property
    def failures(self) -> list[ClaimCheck]:
        return [c for c in self.claims if not c.ok]

    @property
    def rate(self) -> float:
        """Share of claims passing both gates — the citation-validity metric."""
        return sum(c.ok for c in self.claims) / len(self.claims) if self.claims else 0.0


def check_claims(claims: list[dict], retrieved) -> VerifiedAnswer:
    """Run C2 and C3 over the model's claims against what it was shown.

    `retrieved` is the list of Retrieved passed to the generator; a claim's
    `section` is matched against those section numbers, and its `quote` against
    that section's text.
    """
    out = []
    for c in claims:
        sec = str(c.get("section", "")).strip()
        quote = (c.get("quote") or "").strip()
        hit = resolve(sec, retrieved)
        out.append(ClaimCheck(
            text=(c.get("text") or "").strip(),
            section=sec,
            quote=quote,
            cited_retrieved=hit is not None,
            quote_found=bool(quote) and hit is not None and norm(quote) in norm(hit.text),
            resolved_section=str(hit.section) if hit else None,
        ))
    return VerifiedAnswer(claims=out)
