"use client";

import { useRef, useState } from "react";
import { ApiError, IMAGE_MAX_BYTES, IMAGE_TYPES, readImage } from "@/lib/api";
import type { InputStatusState } from "./InputStatus";

interface ImageButtonProps {
  /** Receives the text read off the page. Placed in the input for the reader to
   *  check — never submitted. */
  onText: (text: string) => void;
  onStatus?: (state: InputStatusState | null) => void;
  disabled?: boolean;
}

/**
 * Read one page from an image.
 *
 * **One page, and an image only.** PDFs and documents are refused rather than
 * converted. A PDF carries a text layer that the ingestion parser reads with
 * page numbers, fonts and column positions — everything a vision model discards
 * — so accepting one here would swap verified extraction for a generative
 * guess. The honest use is a photo or scan with no text layer at all.
 *
 * **The text is never sent on its own**, for the same reason the microphone's
 * transcript is not: a vision model is not OCR. It fails *plausibly* — "302A"
 * quietly becomes "302" and the output still reads like clean statute — and
 * nothing downstream can catch a correctly-executed wrong question. Nothing it
 * produces is cited either; an answer about this text is still retrieved from
 * the indexed Acts and verified against them.
 */
export function ImageButton({ onText, onStatus, disabled }: ImageButtonProps) {
  const [busy, setBusy] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  async function handleFile(file: File | undefined) {
    if (!file) return;
    // Checked here for a fast, specific message; the server checks the bytes
    // themselves, which is what actually catches a PDF renamed .png.
    if (!IMAGE_TYPES.includes(file.type)) {
      onStatus?.({ text: "Only one page as PNG, JPEG or WebP — not PDFs", tone: "error" });
      return;
    }
    if (file.size > IMAGE_MAX_BYTES) {
      onStatus?.({
        text: `That image is ${(file.size / (1024 * 1024)).toFixed(1)} MB — the limit is ${
          IMAGE_MAX_BYTES / (1024 * 1024)
        } MB`,
        tone: "error",
      });
      return;
    }

    setBusy(true);
    const size = `${(file.size / (1024 * 1024)).toFixed(1)} MB`;
    try {
      const result = await readImage(file, (phase, percent) => {
        // Two genuinely different waits, named separately: sending bytes is
        // bounded and depends on the connection, a vision model reading a dense
        // page is open-ended and depends on the provider.
        onStatus?.(
          phase === "uploading"
            ? { text: `Sending the image (${size})…`, busy: true, percent }
            : {
                text: "Reading the page — the vision model is transcribing it. This can take up to a minute.",
                busy: true,
              },
        );
      });
      if (!result.text) {
        onStatus?.({ text: "No text found in that image", tone: "error" });
        return;
      }
      onText(result.text);
      onStatus?.({ text: `Read ${result.chars} characters — check the text, then ask` });
    } catch (error) {
      console.error("read-image failed", error);
      onStatus?.({
        tone: "error",
        text:
          error instanceof ApiError
            ? error.status === 415
              ? "Only one page as PNG, JPEG or WebP — not PDFs"
              : error.status === 503
                ? "Image reading is not configured on the server"
                : `Could not read the image (${error.status})`
            : "Could not reach the backend",
      });
    } finally {
      setBusy(false);
      // Clear the input so selecting the same file twice fires change again.
      if (inputRef.current) inputRef.current.value = "";
    }
  }

  return (
    <>
      <input
        ref={inputRef}
        type="file"
        accept={IMAGE_TYPES.join(",")}
        className="hidden"
        onChange={(event) => void handleFile(event.target.files?.[0])}
      />
      <button
        type="button"
        onClick={() => inputRef.current?.click()}
        disabled={disabled || busy}
        aria-label="Read a page from an image"
        title="Read one page from an image (PNG, JPEG or WebP, max 5 MB). PDFs are not accepted — the text lands in the box for you to check."
        className="flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-medium text-slate-500 hover:text-slate-800 hover:bg-slate-200/60 transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <span className="material-symbols-outlined text-[16px]">
          {busy ? "hourglass_top" : "image"}
        </span>
      </button>
    </>
  );
}
