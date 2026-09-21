"""Speech-to-text probe for voice input — Deepgram, evaluated on what matters here.

Voice input for a legal assistant has one failure mode that dwarfs the rest:
**a misheard number cites the wrong law.** "Section 379" heard as "section 279"
is not a slightly worse transcript, it is a different offence, and every stage
downstream — planner, catalog fetch, citation gates — will faithfully answer the
wrong question. Word error rate averages that away, so this script reports it
separately.

Three metrics, in order of how much they matter:

  number accuracy   every digit string in the reference, found in the transcript.
                    This is the one that decides whether voice input is safe.
  keyterm recall    did 'cognizable', 'bailable', 'Penal Code' survive? Statutory
                    vocabulary is rare in general speech and is exactly what an
                    STT model smooths into something commoner.
  WER               the usual aggregate, for comparison with published numbers.

**Language, and the finding that shapes this.** Bengali is `bn` and is supported
only by nova-3. It is **not** in Deepgram's `multi` code-switching set (English,
Spanish, French, German, Hindi, Russian, Portuguese, Japanese, Italian, Dutch).
So "theft er jonno ki shasti?" — English legal nouns inside a Bangla sentence,
which is how people here actually talk about law — has no single-model answer.
`--language bn` and `--language en` each handle half of it. Measuring that gap
is the point of `--compare`.

Keyterm prompting (nova-3, up to 500 tokens, 20-50 recommended) is the lever
against the second failure. `--keyterms` builds the list from the catalog, so
the terms boosted are the Acts and vocabulary actually in the corpus rather than
a guess.

No new dependencies: recording shells out to `arecord`, conversion to `ffmpeg`,
and the edit distance is implemented here rather than pulling in `jiwer`.

Run (from backend/, ainshonglap conda env active):

    python scripts/deepgram_stt.py --record 6                  # mic -> transcript
    python scripts/deepgram_stt.py --file q.wav
    python scripts/deepgram_stt.py --file q.wav --expect "what does section 379 say"
    python scripts/deepgram_stt.py --file q.wav --keyterms     # + corpus keyterms
    python scripts/deepgram_stt.py --file q.wav --compare      # en vs bn vs keyterms
    python scripts/deepgram_stt.py --record 6 --ask            # transcribe, then answer

Reads DEEPGRAM_API_KEY from backend/.env.local. The key is never printed.
"""

from __future__ import annotations

import argparse
import json
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import time
import urllib.parse

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ENV_FILE = pathlib.Path(__file__).resolve().parents[1] / ".env.local"
API = "https://api.deepgram.com/v1/listen"
TIMEOUT = 120

# Bengali is nova-3 only. nova-2 and the legacy models cannot transcribe it at
# all, so the default here is not negotiable if Bangla input matters.
DEFAULT_MODEL = "nova-3"

MIME = {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/mp4",
        ".ogg": "audio/ogg", ".opus": "audio/ogg", ".flac": "audio/flac",
        ".webm": "audio/webm", ".aac": "audio/aac"}

# Statutory vocabulary that general-purpose STT has no reason to prefer. These
# are the words whose loss silently changes the question.
LEGAL_TERMS = [
    "cognizable", "non-cognizable", "bailable", "non-bailable", "compoundable",
    "First Information Report", "FIR", "magistrate", "sessions judge",
    "cognizance", "remand", "bail", "charge sheet", "summons", "warrant",
    "culpable homicide", "house-breaking", "criminal breach of trust",
    "wrongful restraint", "wrongful confinement", "abetment", "mens rea",
]


def keyterms_from_catalog(limit: int = 40) -> list[str]:
    """Boost the Acts and offences this corpus actually contains.

    Built from the catalog rather than written out, so the terms boosted cannot
    drift from what is indexed — and so adding an Act extends the vocabulary
    without editing this file.
    """
    terms = list(LEGAL_TERMS)
    try:
        from app.rag import catalog

        for a in catalog.manifest()["acts"]:
            terms.append(a["name"])
        # Frequent section rubrics: the words a caller is most likely to say.
        seen: set[str] = set()
        for row in catalog.connect().execute(
                "SELECT title FROM sections WHERE title IS NOT NULL"
                " AND LENGTH(title) BETWEEN 6 AND 34 LIMIT 400"):
            t = (row["title"] or "").strip()
            if t and t.lower() not in seen:
                seen.add(t.lower())
                terms.append(t)
    except Exception:
        pass                       # catalog absent: the static list still works
    return terms[:limit]


# ------------------------------------------------------------------ metrics

def words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9']+", (text or "").lower())


def wer(reference: str, hypothesis: str) -> tuple[float, int, int]:
    """Word error rate by Levenshtein distance over word sequences."""
    r, h = words(reference), words(hypothesis)
    if not r:
        return (0.0 if not h else 1.0), 0, len(h)
    prev = list(range(len(h) + 1))
    for i, rw in enumerate(r, 1):
        cur = [i] + [0] * len(h)
        for j, hw in enumerate(h, 1):
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (rw != hw))
        prev = cur
    return prev[len(h)] / len(r), prev[len(h)], len(r)


def numbers(text: str) -> list[str]:
    """Digit strings — the section numbers a citation depends on."""
    return re.findall(r"\d+[A-Za-z]{0,2}", text or "")


def number_accuracy(reference: str, hypothesis: str) -> tuple[int, int, list[str]]:
    """(found, expected, missed). Order-insensitive: the number has to survive,
    not stay in position."""
    want, got = numbers(reference), [n.lower() for n in numbers(hypothesis)]
    missed = []
    pool = list(got)
    for n in want:
        if n.lower() in pool:
            pool.remove(n.lower())
        else:
            missed.append(n)
    return len(want) - len(missed), len(want), missed


def _has_term(text: str, term: str) -> bool:
    """Whole-word match.

    Substring matching scored this metric wrong in exactly the case it exists
    to catch: 'cognizable' is inside 'recognizable', so a transcript that
    replaced the statutory term with a commoner one was counted as keeping it.
    """
    return re.search(rf"(?<!\w){re.escape(term.lower())}(?!\w)",
                     (text or "").lower()) is not None


def term_recall(reference: str, hypothesis: str,
                terms: list[str]) -> tuple[int, int, list[str]]:
    present = [t for t in terms if _has_term(reference, t)]
    missed = [t for t in present if not _has_term(hypothesis, t)]
    return len(present) - len(missed), len(present), missed


# ------------------------------------------------------------------ audio

def record(seconds: float, out: pathlib.Path) -> pathlib.Path:
    """Record mono 16 kHz from the default input with `arecord`.

    16 kHz mono because that is what speech models want; anything higher is
    bandwidth Deepgram discards.
    """
    # `-d` is an integer number of seconds; a float there is rejected outright
    # ("invalid duration argument '6.0'").
    secs = max(1, int(round(seconds)))
    cmd = ["arecord", "-q", "-f", "S16_LE", "-r", "16000", "-c", "1",
           "-d", str(secs), str(out)]
    print(f"recording {secs}s — speak now ...", flush=True)
    try:
        subprocess.run(cmd, check=True)
    except subprocess.CalledProcessError as exc:
        raise SystemExit(
            f"arecord failed ({exc.returncode}). Check `arecord -l` lists a "
            "capture device and that this terminal may use it; "
            "`arecord -D default -f S16_LE -r 16000 -c 1 -d 2 /tmp/t.wav` "
            "tests it directly.") from exc
    size = out.stat().st_size
    print(f"  saved {out} ({size/1024:.0f} KB)")
    if size < 2000:
        print("  warning: that file is nearly empty — the mic may be muted")
    return out


def ensure_supported(path: pathlib.Path) -> tuple[pathlib.Path, str, bool]:
    """(path, mime, is_temp). Converts through ffmpeg only when needed."""
    mime = MIME.get(path.suffix.lower())
    if mime:
        return path, mime, False
    tmp = pathlib.Path(tempfile.mkstemp(suffix=".wav")[1])
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(path),
                    "-ar", "16000", "-ac", "1", str(tmp)], check=True)
    return tmp, "audio/wav", True


# ------------------------------------------------------------------ deepgram

def transcribe(key: str, audio: pathlib.Path, *, model: str = DEFAULT_MODEL,
               language: str = "en", keyterms: list[str] | None = None,
               smart_format: bool = True) -> dict:
    path, mime, is_temp = ensure_supported(audio)
    params: list[tuple[str, str]] = [
        ("model", model),
        ("language", language),
        ("smart_format", "true" if smart_format else "false"),
        ("punctuate", "true"),
    ]
    # Repeated keyterm parameters, not comma-separated: the API rejects the
    # latter, and multi-word terms need URL encoding.
    for t in (keyterms or []):
        params.append(("keyterm", t))
    url = f"{API}?{urllib.parse.urlencode(params)}"

    body = path.read_bytes()
    started = time.perf_counter()
    try:
        resp = requests.post(
            url, data=body, timeout=TIMEOUT,
            headers={"Authorization": f"Token {key}", "Content-Type": mime})
    finally:
        if is_temp:
            path.unlink(missing_ok=True)
    elapsed = time.perf_counter() - started

    if resp.status_code != 200:
        raise RuntimeError(f"deepgram {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    alt = (((data.get("results") or {}).get("channels") or [{}])[0]
           .get("alternatives") or [{}])[0]
    meta = data.get("metadata") or {}
    return {
        "transcript": (alt.get("transcript") or "").strip(),
        "confidence": alt.get("confidence"),
        "audio_seconds": meta.get("duration"),
        "elapsed": elapsed,
        "model": (meta.get("models") or [None])[0],
        "language": language,
        "keyterms": len(keyterms or []),
    }


def report(result: dict, expect: str | None, terms: list[str]) -> None:
    dur = result.get("audio_seconds") or 0
    rtf = f", {dur / result['elapsed']:.1f}x realtime" if result["elapsed"] else ""
    conf = result.get("confidence")
    print(f"  [{result['language']}, {result['keyterms']} keyterms] "
          f"{result['elapsed']:.2f}s for {dur:.1f}s audio{rtf}"
          + (f", confidence {conf:.3f}" if isinstance(conf, float) else ""))
    print(f"  > {result['transcript'] or '(empty)'}")
    if not expect:
        return
    rate, errs, total = wer(expect, result["transcript"])
    hit, want, missed = number_accuracy(expect, result["transcript"])
    thit, twant, tmissed = term_recall(expect, result["transcript"], terms)
    print(f"    WER          {rate:.1%}  ({errs} errors / {total} words)")
    if want:
        flag = "" if hit == want else "   <-- WRONG LAW RISK"
        print(f"    numbers      {hit}/{want}{'  missed ' + str(missed) if missed else ''}{flag}")
    if twant:
        print(f"    legal terms  {thit}/{twant}"
              + (f"  missed {tmissed}" if tmissed else ""))


def main() -> None:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--file", help="audio file to transcribe")
    src.add_argument("--record", type=float, metavar="SECONDS",
                     help="record from the default microphone, then transcribe")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--language", default="en",
                    help="en | bn (Bengali, nova-3 only) | multi (no Bengali)")
    ap.add_argument("--expect", help="reference transcript, to score against")
    ap.add_argument("--keyterms", action="store_true",
                    help="boost statutory vocabulary built from the catalog")
    ap.add_argument("--compare", action="store_true",
                    help="run several configurations on the same audio and "
                         "print them together")
    ap.add_argument("--ask", action="store_true",
                    help="send the transcript through the full pipeline")
    ap.add_argument("--keep", action="store_true", help="keep the recording")
    args = ap.parse_args()

    load_dotenv(ENV_FILE, override=True)
    key = os.environ.get("DEEPGRAM_API_KEY")
    if not key:
        raise SystemExit("DEEPGRAM_API_KEY not set (expected in backend/.env.local)")

    if args.record:
        audio = pathlib.Path(tempfile.mkstemp(suffix=".wav")[1])
        record(args.record, audio)
    else:
        audio = pathlib.Path(args.file)
        if not audio.exists():
            raise SystemExit(f"no such file: {audio}")

    terms = keyterms_from_catalog() if (args.keyterms or args.compare) else []
    if terms and not args.compare:
        print(f"keyterms: {len(terms)} from the catalog "
              f"(e.g. {', '.join(terms[:4])})")

    try:
        if args.compare:
            # The comparison that decides the configuration: does boosting
            # statutory vocabulary recover the section numbers and legal terms
            # a general model drops, and what does Bangla cost?
            configs = [
                ("en, no keyterms", dict(language="en", keyterms=None)),
                ("en + keyterms", dict(language="en", keyterms=terms)),
                ("bn", dict(language="bn", keyterms=None)),
                ("multi", dict(language="multi", keyterms=None)),
            ]
            print(f"\ncomparing {len(configs)} configurations on the same audio\n")
            for label, kw in configs:
                print(f"--- {label}")
                try:
                    res = transcribe(key, audio, model=args.model, **kw)
                    report(res, args.expect, terms)
                except RuntimeError as exc:
                    print(f"  FAILED: {exc}")
                print()
            return

        result = transcribe(key, audio, model=args.model,
                            language=args.language, keyterms=terms or None)
        print()
        report(result, args.expect, terms)

        if args.ask:
            if not result["transcript"]:
                raise SystemExit("nothing transcribed — no question to ask")
            print("\n--- through the pipeline")
            from app.rag.execute import answer_question

            t0 = time.perf_counter()
            a = answer_question(result["transcript"])
            print(f"  kind    {a.kind}  ({time.perf_counter()-t0:.1f}s)")
            if a.refused:
                print(f"  REFUSED {a.reason}")
            if a.summary:
                print(f"  answer  {a.summary[:400]}")
            for c in a.claims[:4]:
                print(f"    s.{c['section']}  {c['text'][:90]}")
    finally:
        if args.record and not args.keep:
            audio.unlink(missing_ok=True)
        elif args.record:
            print(f"\nrecording kept at {audio}")


if __name__ == "__main__":
    main()
