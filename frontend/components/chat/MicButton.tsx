"use client";

import { useEffect, useRef, useState } from "react";
import { ApiError, transcribeAudio } from "@/lib/api";

interface MicButtonProps {
  /** Receives the transcript. It is placed in the input for the reader to
   *  confirm — never submitted automatically. See the note below. */
  onTranscript: (text: string, confidence: number | null) => void;
  onStatus?: (text: string) => void;
  disabled?: boolean;
}

type State = "idle" | "starting" | "recording" | "transcribing";

/** Longest single recording. A spoken legal question is a few seconds; a
 *  forgotten open mic is a large upload and a surprising bill. */
const MAX_SECONDS = 60;

/**
 * Click to record, click again to stop.
 *
 * **Click-to-toggle, not hold-to-talk.** The first version held: `pointerdown`
 * started and `pointerup` stopped. That is unusable on first run, because
 * `getUserMedia` shows a permission prompt and does not resolve until it is
 * answered — so the release arrived while the state was still `idle`, the stop
 * did nothing, and recording began *after* the button came up and never ended.
 * Toggling removes the race rather than papering over it, and a `starting`
 * state now absorbs a stop that lands before the stream is granted.
 *
 * **The transcript is never sent on its own.** It lands in the input box and
 * the reader presses send. That is the only check that exists on this path: a
 * misheard section number — "379" as "279" — produces a perfectly grounded
 * answer about a different offence, because the planner names coordinates that
 * exist, the catalog fetches them, and the quote verifies against the section
 * that was fetched. Every gate passes; none of them know what was said.
 */
export function MicButton({ onTranscript, onStatus, disabled }: MicButtonProps) {
  const [state, setState] = useState<State>("idle");
  const [seconds, setSeconds] = useState(0);
  const recorderRef = useRef<MediaRecorder | null>(null);
  const chunksRef = useRef<Blob[]>([]);
  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);
  // Set when a stop arrives while the microphone is still being granted.
  const cancelledRef = useRef(false);

  useEffect(() => {
    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
      recorderRef.current?.stream.getTracks().forEach((track) => track.stop());
    };
  }, []);

  function clearTimer() {
    if (timerRef.current) clearInterval(timerRef.current);
    timerRef.current = null;
  }

  async function start() {
    cancelledRef.current = false;
    setState("starting");
    onStatus?.("Waiting for microphone…");
    let stream: MediaStream;
    try {
      if (!navigator.mediaDevices?.getUserMedia) {
        throw new Error("getUserMedia unavailable (needs https or localhost)");
      }
      stream = await navigator.mediaDevices.getUserMedia({ audio: true });
    } catch (error) {
      // Denied, no hardware, and insecure-context all look the same to the
      // user: nothing happened. Name it instead, and log the real cause.
      console.error("microphone unavailable", error);
      onStatus?.(
        error instanceof DOMException && error.name === "NotAllowedError"
          ? "Microphone permission denied"
          : "Microphone unavailable",
      );
      setState("idle");
      return;
    }

    // Stopped while the prompt was open: release the device and stay idle.
    if (cancelledRef.current) {
      stream.getTracks().forEach((track) => track.stop());
      setState("idle");
      onStatus?.("Cancelled");
      return;
    }

    try {
      const recorder = new MediaRecorder(stream);
      chunksRef.current = [];
      recorder.ondataavailable = (event) => {
        if (event.data.size > 0) chunksRef.current.push(event.data);
      };
      recorder.onerror = (event) => {
        console.error("MediaRecorder error", event);
        onStatus?.("Recording failed");
        stream.getTracks().forEach((track) => track.stop());
        clearTimer();
        setState("idle");
      };
      recorder.onstop = () => {
        stream.getTracks().forEach((track) => track.stop());
        const blob = new Blob(chunksRef.current, {
          type: recorder.mimeType || "audio/webm",
        });
        void send(blob);
      };
      recorder.start();
      recorderRef.current = recorder;
      setSeconds(0);
      setState("recording");
      onStatus?.("Listening — click again to stop");
      timerRef.current = setInterval(() => {
        setSeconds((s) => {
          if (s + 1 >= MAX_SECONDS) stop();
          return s + 1;
        });
      }, 1000);
    } catch (error) {
      console.error("MediaRecorder unavailable", error);
      stream.getTracks().forEach((track) => track.stop());
      onStatus?.("Recording not supported in this browser");
      setState("idle");
    }
  }

  function stop() {
    if (state === "starting") {
      cancelledRef.current = true;
      return;
    }
    if (state !== "recording") return;
    clearTimer();
    setState("transcribing");
    onStatus?.("Transcribing…");
    recorderRef.current?.stop();
  }

  async function send(blob: Blob) {
    try {
      if (blob.size < 1000) {
        onStatus?.("Recording too short");
        return;
      }
      const result = await transcribeAudio(blob);
      if (!result.transcript) {
        onStatus?.("Nothing heard — try again");
        return;
      }
      onTranscript(result.transcript, result.confidence);
      onStatus?.(
        result.confidence !== null
          ? `Heard (${Math.round(result.confidence * 100)}%) — check it, then send`
          : "Check the text, then send",
      );
    } catch (error) {
      console.error("transcription failed", error);
      onStatus?.(
        error instanceof ApiError
          ? error.status === 503
            ? "Voice input not configured on the server"
            : `Transcription failed (${error.status})`
          : "Could not reach the backend",
      );
    } finally {
      setState("idle");
      setSeconds(0);
    }
  }

  const recording = state === "recording";
  const busy = state === "transcribing" || state === "starting";

  return (
    <button
      type="button"
      onClick={() => (recording || state === "starting" ? stop() : void start())}
      disabled={disabled || state === "transcribing"}
      aria-label={recording ? "Stop recording" : "Record a question"}
      aria-pressed={recording}
      title={
        recording
          ? "Click to stop and transcribe"
          : "Record a question — the transcript goes in the box for you to check before sending"
      }
      className={`flex items-center gap-1.5 px-2.5 py-1.5 rounded-xl text-xs font-medium transition-all active:scale-95 disabled:opacity-50 disabled:cursor-not-allowed ${
        recording
          ? "bg-red-600 text-white shadow-sm animate-pulse"
          : "text-slate-500 hover:text-slate-800 hover:bg-slate-200/60"
      }`}
    >
      <span className="material-symbols-outlined text-[16px]">
        {state === "transcribing"
          ? "hourglass_top"
          : recording
            ? "stop_circle"
            : "mic"}
      </span>
      {recording && <span className="font-mono tabular-nums">{seconds}s</span>}
      {state === "starting" && <span>…</span>}
      {busy && !recording && state === "transcribing" && <span>…</span>}
    </button>
  );
}
