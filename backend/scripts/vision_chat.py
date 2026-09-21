"""Send an image to a vision-language model on OpenRouter, and score what comes back.

For this project the interesting question is not "can it see the image" — it can
— but **how faithfully it reproduces statutory text**, because everything
downstream assumes the text it was given is the text on the page.

A VLM is not an OCR engine. OCR extracts a character stream and fails visibly
when it cannot read something; a VLM *describes what it saw*, generatively, and
fails **plausibly**: `302A` quietly becomes `302`, a proviso it judged redundant
is dropped, and the output still reads like a clean statute. Nothing downstream
can catch that — `verify_parsed.py` compares the parse against the PDF's own
text layer, so if the text itself were invented there would be nothing to
compare against.

So this script defaults to measuring rather than admiring. Give it a reference
and it reports character accuracy, section-number fidelity and length drift:

    --pdf FILE --page N    render a page we already parse, and diff the model's
                           reading against the PyMuPDF extraction of that same
                           page. That extraction is what the current pipeline
                           trusts, so it is the right yardstick.
    --expect FILE.txt      diff against any reference text you supply.

Where a VLM could genuinely help is the case with **no text layer at all** — a
scanned Act, where the alternative is not "good text vs model text" but "model
text vs nothing". Docling, the current fallback there, measured worse than
PyMuPDF wherever PyMuPDF works (61 lines / 5 sections against 247 / 13 on the
same 6 pages), so the bar is low and worth measuring against.

Models (OpenRouter, free tiers, confirmed image-capable via their models API):

    omni    nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free   text+audio+image+video, 256K
    lingvl  inclusionai/ling-3.0-flash-vl:free                   text+image+video, 262K

Run (from backend/, ainshonglap conda env active):

    python scripts/vision_chat.py --image page.png
    python scripts/vision_chat.py --image page.png --model lingvl --ask "What is this?"
    python scripts/vision_chat.py --pdf "../Laws/main/The Penal Code, 1860.pdf" --page 40
    python scripts/vision_chat.py --image scan.jpg --expect reference.txt

Reads OPENROUTER_API_KEY from backend/.env.local. The key is never printed.
"""

from __future__ import annotations

import argparse
import base64
import difflib
import json
import mimetypes
import pathlib
import re
import sys
import time

import requests
from dotenv import load_dotenv

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

ENV_FILE = pathlib.Path(__file__).resolve().parents[1] / ".env.local"
API = "https://openrouter.ai/api/v1/chat/completions"
TIMEOUT = 300

MODELS = {
    "omni": "nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free",
    "lingvl": "inclusionai/ling-3.0-flash-vl:free",
}
DEFAULT_MODEL = "lingvl"

# Transcription, not description. Every clause here exists because a VLM's
# default behaviour is to be helpful — to tidy, summarise and renumber — and
# tidied statutory text is wrong statutory text.
OCR_PROMPT = (
    "Transcribe ALL text in this image exactly as printed, in reading order.\n\n"
    "Rules:\n"
    "- Copy section numbers EXACTLY: '302A' is not '302', '99B' is not '99'.\n"
    "- Do not summarise, paraphrase, correct, modernise or reorder anything.\n"
    "- Keep every proviso, explanation, illustration and footnote marker.\n"
    "- Preserve line and paragraph breaks.\n"
    "- If a word is genuinely illegible, write [illegible] rather than "
    "guessing a plausible one.\n"
    "- Output only the transcription. No preamble, no commentary."
)


def encode_image(path: pathlib.Path) -> str:
    mime = mimetypes.guess_type(path.name)[0] or "image/png"
    return f"data:{mime};base64,{base64.b64encode(path.read_bytes()).decode()}"


def render_pdf_page(pdf: pathlib.Path, page: int, dpi: int,
                    out: pathlib.Path) -> pathlib.Path:
    """Rasterise one page, so a page we already parse can be used as ground truth."""
    import pymupdf

    doc = pymupdf.open(pdf)
    pix = doc[page - 1].get_pixmap(dpi=dpi)
    pix.save(out)
    doc.close()
    print(f"  rendered p{page} at {dpi}dpi -> {out} ({out.stat().st_size/1024:.0f} KB)")
    return out


def pdf_page_text(pdf: pathlib.Path, page: int) -> str:
    """What PyMuPDF reads from that page — the text the pipeline currently trusts."""
    import pymupdf

    doc = pymupdf.open(pdf)
    text = doc[page - 1].get_text()
    doc.close()
    return text


# ------------------------------------------------------------------ scoring

def normalise(text: str) -> str:
    """Fold whitespace and quote style so formatting is not scored as error."""
    text = (text or "").replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    text = text.replace("–", "-").replace("—", "-")
    return re.sub(r"\s+", " ", text).strip()


def sections(text: str) -> list[str]:
    """Section numbers as printed: '302.', '99B.' — the detail that must survive."""
    return re.findall(r"(?<!\d)(\d{1,3}[A-Z]{0,2})\.", text or "")


def score(reference: str, hypothesis: str) -> dict:
    ref, hyp = normalise(reference), normalise(hypothesis)
    ratio = difflib.SequenceMatcher(None, ref, hyp).ratio()
    ref_secs, hyp_secs = sections(reference), sections(hypothesis)
    pool = list(hyp_secs)
    missed = []
    for s in ref_secs:
        if s in pool:
            pool.remove(s)
        else:
            missed.append(s)
    return {
        "char_similarity": ratio,
        "ref_chars": len(ref),
        "hyp_chars": len(hyp),
        "length_ratio": (len(hyp) / len(ref)) if ref else 0.0,
        "sections_expected": len(ref_secs),
        "sections_found": len(ref_secs) - len(missed),
        "sections_missed": missed,
        "sections_invented": [s for s in pool],
    }


def report(result: dict) -> None:
    print(f"    character similarity  {result['char_similarity']:.1%}")
    print(f"    length                {result['hyp_chars']} chars vs "
          f"{result['ref_chars']} reference ({result['length_ratio']:.2f}x)")
    if result["sections_expected"]:
        ok = result["sections_found"] == result["sections_expected"]
        print(f"    section numbers       {result['sections_found']}/"
              f"{result['sections_expected']}"
              + ("" if ok else f"   MISSED {result['sections_missed']}"))
    if result["sections_invented"]:
        # Numbers that are not on the page. The most dangerous output this can
        # produce, because a citation to an invented section looks exactly like
        # a citation to a real one.
        print(f"    INVENTED numbers      {result['sections_invented']}")


# ------------------------------------------------------------------ request

def ask(key: str, model: str, image: pathlib.Path, prompt: str,
        max_tokens: int = 8192) -> tuple[str, dict, float]:
    body = {
        "model": model,
        "messages": [{
            "role": "user",
            "content": [
                {"type": "text", "text": prompt},
                {"type": "image_url", "image_url": {"url": encode_image(image)}},
            ],
        }],
        "temperature": 0.0,
        "max_tokens": max_tokens,
    }
    started = time.perf_counter()
    resp = requests.post(
        API, json=body, timeout=TIMEOUT,
        headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
    )
    elapsed = time.perf_counter() - started
    if resp.status_code != 200:
        raise RuntimeError(f"openrouter {resp.status_code}: {resp.text[:400]}")
    data = resp.json()
    if data.get("error"):
        raise RuntimeError(f"openrouter: {json.dumps(data['error'])[:400]}")
    choice = (data.get("choices") or [{}])[0]
    content = (choice.get("message") or {}).get("content")
    if not (content or "").strip():
        raise RuntimeError(
            f"empty response (finish_reason={choice.get('finish_reason')})")
    return content, data.get("usage") or {}, elapsed


def main() -> None:
    ap = argparse.ArgumentParser()
    src = ap.add_mutually_exclusive_group(required=True)
    src.add_argument("--image", help="image file to send")
    src.add_argument("--pdf", help="render a page of this PDF and send that")
    ap.add_argument("--page", type=int, default=1, help="page number, with --pdf")
    ap.add_argument("--dpi", type=int, default=200, help="render resolution")
    ap.add_argument("--model", default=DEFAULT_MODEL,
                    help=f"alias or full id (aliases: {', '.join(MODELS)})")
    ap.add_argument("--ask", help="your own prompt instead of verbatim transcription")
    ap.add_argument("--expect", help="reference text file, to score against")
    ap.add_argument("--both", action="store_true", help="run every alias in turn")
    ap.add_argument("--max-tokens", type=int, default=8192)
    ap.add_argument("--save", help="write the transcription here")
    args = ap.parse_args()

    load_dotenv(ENV_FILE, override=True)
    import os

    key = os.environ.get("OPENROUTER_API_KEY")
    if not key:
        raise SystemExit("OPENROUTER_API_KEY not set (expected in backend/.env.local)")

    reference = None
    if args.pdf:
        pdf = pathlib.Path(args.pdf)
        if not pdf.exists():
            raise SystemExit(f"no such PDF: {pdf}")
        image = render_pdf_page(pdf, args.page, args.dpi,
                                pathlib.Path(f"/tmp/vision_p{args.page}.png"))
        # Ground truth for free: the page already has a text layer, and that
        # layer is what the pipeline trusts today.
        reference = pdf_page_text(pdf, args.page)
        print(f"  reference from the PDF text layer: {len(reference)} chars")
    else:
        image = pathlib.Path(args.image)
        if not image.exists():
            raise SystemExit(f"no such image: {image}")

    if args.expect:
        reference = pathlib.Path(args.expect).read_text(encoding="utf-8")

    prompt = args.ask or OCR_PROMPT
    aliases = list(MODELS) if args.both else [args.model]

    for alias in aliases:
        model = MODELS.get(alias, alias)
        print(f"\n=== {model}")
        try:
            text, usage, elapsed = ask(key, model, image, prompt, args.max_tokens)
        except RuntimeError as exc:
            print(f"  FAILED: {exc}")
            continue
        print(f"  {elapsed:.1f}s, {usage.get('prompt_tokens','?')} in / "
              f"{usage.get('completion_tokens','?')} out")
        print("  --- output ---")
        print("\n".join("  " + line for line in text.strip().splitlines()[:40]))
        if len(text.strip().splitlines()) > 40:
            print(f"  ... {len(text.strip().splitlines()) - 40} more lines")

        if reference and not args.ask:
            print("  --- scored against the reference ---")
            report(score(reference, text))

        if args.save:
            out = pathlib.Path(args.save)
            if len(aliases) > 1:
                out = out.with_name(f"{out.stem}.{alias}{out.suffix}")
            out.write_text(text, encoding="utf-8")
            print(f"  saved -> {out}")


if __name__ == "__main__":
    main()
