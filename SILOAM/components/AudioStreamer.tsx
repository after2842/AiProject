"use client";

import React, { useRef, useState } from "react";

/**
 * What this component does:
 * 1) On Start, it asks for the mic with echoCancellation etc.
 * 2) It builds a WebAudio graph with an AudioWorklet that gives us raw Float32 samples.
 * 3) On the main thread, we resample from the device rate to 24 kHz.
 * 4) We convert to Int16 and ship fixed-size chunks over a WebSocket to your backend.
 * 5) It also plays server-sent PCM16 chunks (TTS) on the SAME WebSocket using a scheduled clock
 *    with a small prebuffer to avoid clicks/gaps.
 *
 * Server side expectations (uplink):
 * - Receives raw PCM Int16 LE frames (mono, 24_000 Hz).
 * - Chunk size controlled by CHUNK_MS (e.g., 40 ms → 960 samples → 1920 bytes).
 *
 * Server side expectations (downlink):
 * - Sends JSON events: {"type":"tts.start","rate":24000,"channels":1}, {"type":"tts.end"}, {"type":"stt.final","text":"..."}
 * - Sends binary frames containing raw PCM16 LE for TTS.
 */

// ---- tweak these to your liking ----
const WS_URL = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws";
const TARGET_SR = 24_000; // 24 kHz (what your STT expects)
const CHUNK_MS = 40; // send 40 ms packets (typical real-time cadence)
const LOOKAHEAD_SEC = 0.06; // schedule each chunk a bit in the future (60 ms prebuffer)
const CATCHUP_MARGIN = 0.02; // if we fall behind this margin, snap the clock to now+LOOKAHEAD
// ------------------------------------

const CHUNK_FRAMES = Math.floor((TARGET_SR * CHUNK_MS) / 1000); // e.g., 960 samples
const BYTES_PER_SAMPLE = 2; // Int16

export default function VoiceCapture() {
  // UI state
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [level, setLevel] = useState(0); // 0..1 input meter
  const [sttText, setSttText] = useState<string>("");

  // WS
  const wsRef = useRef<WebSocket | null>(null);
  const wsOpenRef = useRef(false);

  // Audio graph refs (mic capture)
  const audioCtxRef = useRef<AudioContext | null>(null);
  const srcNodeRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const workletRef = useRef<AudioWorkletNode | null>(null);
  const streamRef = useRef<MediaStream | null>(null);

  // Resampler state (main thread, uplink)
  const inBufRef = useRef<Float32Array>(new Float32Array(0)); // queued input samples
  const readPtrRef = useRef<number>(0); // fractional position within inBuf
  const ratioRef = useRef<number>(1); // input_sr / TARGET_SR

  // Downlink TTS player state (scheduled playback)
  const ttsRateRef = useRef<number>(TARGET_SR); // default; server may override on tts.start
  const playbackTimeRef = useRef<number | null>(null); // running playback clock
  const ttsActiveRef = useRef<boolean>(false); // whether TTS is currently active

  // ---- Start mic + WS ----
  const start = async () => {
    try {
      if (running) return;

      // 0) Connect WebSocket (full-duplex: mic up, TTS down)
      const ws = new WebSocket(WS_URL);
      ws.binaryType = "arraybuffer";
      ws.onopen = () => {
        wsOpenRef.current = true;
        setStatus("WS connected");
        // (Optional) tell server about uplink format
        ws.send(
          JSON.stringify({
            type: "audio_format",
            format: "pcm_s16le",
            rate: TARGET_SR,
            channels: 1,
            chunk_ms: CHUNK_MS,
          })
        );
      };
      ws.onmessage = async (ev) => {
        if (typeof ev.data === "string") {
          const m = JSON.parse(ev.data);
          if (m.type === "stt.final") {
            setSttText(m.text || "");
          } else if (m.type === "tts.start") {
            // Prepare downlink playback
            const rate = Number(m.rate) || TARGET_SR;
            const channels = Number(m.channels) || 1;
            ttsRateRef.current = rate;
            ttsActiveRef.current = true;
            await ensureAudioContext(); // we need an AudioContext to play
            const actx = audioCtxRef.current!;
            // reset the playback clock to now + lookahead for glitch-free start
            playbackTimeRef.current = actx.currentTime + LOOKAHEAD_SEC;
            setStatus(`TTS start (pcm_s16le @ ${rate} Hz, ch=${channels})`);
          } else if (m.type === "tts.end") {
            ttsActiveRef.current = false;
            setStatus("TTS end");
          }
          return;
        }

        // Binary = downlink audio chunk (server TTS PCM)
        const pcm = ev.data as ArrayBuffer;
        schedulePcmChunk(pcm); // schedule playback with lookahead clock
      };
      ws.onclose = () => {
        wsOpenRef.current = false;
        setStatus("WS disconnected");
      };
      ws.onerror = () => setStatus("WS error");
      wsRef.current = ws;

      // 1) Ask for mic with echoCancellation enabled (NS/AGC too)
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: { ideal: true },
          noiseSuppression: { ideal: true },
          autoGainControl: { ideal: true },
          channelCount: { ideal: 1 },
        },
        video: false,
      });
      streamRef.current = stream;

      // 2) Build WebAudio graph (mic capture)
      const actx = new (window.AudioContext ||
        (window as any).webkitAudioContext)();
      audioCtxRef.current = actx;
      if (actx.state === "suspended") await actx.resume(); // important for some browsers

      // 3) Create a MediaStream source from the mic
      const src = actx.createMediaStreamSource(stream);
      srcNodeRef.current = src;

      // 4) AudioWorklet that posts raw Float32 chunks to the main thread
      const workletCode = `
        class PCMProbe extends AudioWorkletProcessor {
          process (inputs, outputs, parameters) {
            const input = inputs[0];
            if (input && input[0]) {
              const chunk = new Float32Array(input[0].length);
              chunk.set(input[0]); // mono
              this.port.postMessage(chunk, [chunk.buffer]);
            }
            return true;
          }
        }
        registerProcessor('pcm-probe', PCMProbe);
      `;
      const blob = new Blob([workletCode], { type: "text/javascript" });
      const url = URL.createObjectURL(blob);
      await actx.audioWorklet.addModule(url);

      const node = new AudioWorkletNode(actx, "pcm-probe", {
        numberOfInputs: 1,
        numberOfOutputs: 0,
      });
      workletRef.current = node;
      src.connect(node);

      // 5) Uplink resampling: device rate → 24k, to Int16, send over WS
      const inSampleRate = actx.sampleRate; // often 48000
      ratioRef.current = inSampleRate / TARGET_SR;
      node.port.onmessage = (e: MessageEvent<Float32Array>) => {
        const f32 = e.data;
        if (!f32 || f32.length === 0) return;

        // simple RMS for UI meter
        let sum = 0;
        for (let i = 0; i < f32.length; i++) sum += f32[i] * f32[i];
        setLevel(Math.min(1, Math.sqrt(sum / f32.length)));

        // append to input queue
        const inBuf = inBufRef.current;
        const merged = new Float32Array(inBuf.length + f32.length);
        merged.set(inBuf, 0);
        merged.set(f32, inBuf.length);
        inBufRef.current = merged;

        // try to produce one or more fixed-size 24k Int16 chunks
        pumpUplinkChunks();
      };

      setRunning(true);
      setStatus(
        `Mic @ ${inSampleRate} Hz → uplink @ ${TARGET_SR} Hz Int16 (CHUNK ${CHUNK_MS}ms)`
      );
    } catch (err: any) {
      setStatus(err?.message || "Failed to start");
      stop();
    }
  };

  const stop = () => {
    // Stop graph
    try {
      workletRef.current?.port?.close();
      workletRef.current?.disconnect();
    } catch {}
    try {
      srcNodeRef.current?.disconnect();
    } catch {}
    try {
      audioCtxRef.current?.close();
    } catch {}

    workletRef.current = null;
    srcNodeRef.current = null;
    audioCtxRef.current = null;

    // Stop mic
    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    // Close WS
    if (wsRef.current && wsOpenRef.current) wsRef.current.close();
    wsRef.current = null;
    wsOpenRef.current = false;

    // Reset uplink resampler state
    inBufRef.current = new Float32Array(0);
    readPtrRef.current = 0;
    ratioRef.current = 1;

    // Reset downlink playback clock
    playbackTimeRef.current = null;
    ttsActiveRef.current = false;

    setRunning(false);
    setStatus("Stopped");
    setLevel(0);
  };

  // ---- Uplink resampling pipeline (Float32 → 24k Int16 → WS) ----
  const pumpUplinkChunks = () => {
    const ws = wsRef.current;
    if (!ws || !wsOpenRef.current) return;

    const ratio = ratioRef.current;
    let inBuf = inBufRef.current;
    let readPtr = readPtrRef.current;

    // Minimum input needed to produce one output chunk:
    const minNeeded = Math.floor(readPtr + CHUNK_FRAMES * ratio + 1);
    if (inBuf.length < minNeeded) return;

    while (inBuf.length >= Math.floor(readPtr + CHUNK_FRAMES * ratio + 1)) {
      const out = new Int16Array(CHUNK_FRAMES);
      let rp = readPtr;

      for (let i = 0; i < CHUNK_FRAMES; i++) {
        const i0 = rp | 0; // floor
        const i1 = i0 + 1;
        const frac = rp - i0;

        const s0 = inBuf[i0] || 0;
        const s1 = inBuf[i1] || 0;
        const s = s0 + (s1 - s0) * frac; // linear interpolation

        // clamp to [-1, 1] then convert to Int16 LE
        const clamped = Math.max(-1, Math.min(1, s));
        out[i] = (clamped < 0 ? clamped * 32768 : clamped * 32767) | 0;

        rp += ratio;
      }

      // Advance pointer, drop consumed input frames, keep fractional remainder
      readPtr = rp;
      const consumed = readPtr | 0; // integer part
      inBuf = inBuf.subarray(consumed); // drop consumed samples (cheap view)
      readPtr -= consumed;

      // Ship this chunk (raw little-endian bytes)
      ws.send(out.buffer);
    }

    // Save updated state
    inBufRef.current = inBuf;
    readPtrRef.current = readPtr;
  };

  // ---- Downlink scheduled playback (PCM16 → Float32 → scheduled start) ----
  const schedulePcmChunk = (buffer: ArrayBuffer) => {
    // basic validation
    if (!buffer || buffer.byteLength < 4 || buffer.byteLength % 2 !== 0) return;
    const actx = audioCtxRef.current;
    if (!actx) return;

    // Convert Int16LE → AudioBuffer @ ttsRate
    const rate = ttsActiveRef.current ? ttsRateRef.current : TARGET_SR;
    const pcm = new Int16Array(buffer);
    const audioBuffer = actx.createBuffer(1, pcm.length, rate);
    const ch = audioBuffer.getChannelData(0);
    for (let i = 0; i < pcm.length; i++) ch[i] = pcm[i] / 32768;

    // Determine when to start (use running playback clock)
    const now = actx.currentTime;
    if (playbackTimeRef.current == null) {
      playbackTimeRef.current = now + LOOKAHEAD_SEC;
    } else if (playbackTimeRef.current < now + CATCHUP_MARGIN) {
      // fell behind; catch up to avoid gap
      playbackTimeRef.current = now + LOOKAHEAD_SEC;
    }

    const when = playbackTimeRef.current;
    const duration = audioBuffer.duration;

    const src = actx.createBufferSource();
    src.buffer = audioBuffer;
    src.connect(actx.destination);
    src.start(when);

    // advance clock for next chunk
    playbackTimeRef.current += duration;
  };

  // Ensure AudioContext exists and is resumed (for TTS-only cases)
  const ensureAudioContext = async () => {
    if (!audioCtxRef.current) {
      const actx = new (window.AudioContext ||
        (window as any).webkitAudioContext)();
      audioCtxRef.current = actx;
      if (actx.state === "suspended") await actx.resume();
    } else if (audioCtxRef.current.state === "suspended") {
      await audioCtxRef.current.resume();
    }
  };

  return (
    <div className="w-full max-w-xl mx-auto p-4">
      <div className="rounded-2xl border border-zinc-800/70 bg-zinc-950/70 p-4">
        <div className="flex items-center justify-between">
          <h2 className="text-zinc-200 font-semibold">Mic ↔ WS ↔ TTS</h2>
          <div
            className={`text-xs px-2 py-1 rounded-full border ${
              running
                ? "border-emerald-500/40 text-emerald-300/90 bg-emerald-900/20"
                : "border-zinc-600 text-zinc-300 bg-zinc-800/40"
            }`}
          >
            {running ? "running" : "idle"}
          </div>
        </div>

        <p className="text-zinc-400 text-sm mt-2">
          Uplink: mono PCM <span className="font-mono">Int16</span> at{" "}
          <span className="font-mono">{TARGET_SR} Hz</span> in{" "}
          <span className="font-mono">{CHUNK_MS} ms</span> chunks. Downlink:
          scheduled PCM playback with{" "}
          <span className="font-mono">
            ~{Math.round(LOOKAHEAD_SEC * 1000)}ms
          </span>{" "}
          prebuffer to avoid gaps.
        </p>

        {/* tiny input level bar */}
        <div className="mt-4 h-2 rounded-full bg-zinc-800 overflow-hidden">
          <div
            className="h-full bg-cyan-500 transition-[width] duration-75"
            style={{ width: `${Math.round(level * 100)}%` }}
            title="mic input level"
          />
        </div>

        <div className="mt-4 flex items-center gap-2">
          {!running ? (
            <button
              onClick={start}
              className="px-4 py-2 rounded-xl bg-sky-600 hover:bg-sky-500 text-white font-medium shadow-[0_8px_20px_rgba(2,132,199,.25)]"
            >
              Start
            </button>
          ) : (
            <button
              onClick={stop}
              className="px-4 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-100 font-medium border border-zinc-700"
            >
              Stop
            </button>
          )}
          <span className="text-xs text-zinc-400">{status}</span>
        </div>

        {sttText && (
          <div className="mt-4 text-sm text-zinc-200">
            <span className="font-semibold text-zinc-400">STT:</span>{" "}
            <span className="font-mono">{sttText}</span>
          </div>
        )}

        <div className="mt-3 text-xs text-zinc-500">
          <ul className="list-disc pl-5 space-y-1">
            <li>
              Requests <span className="font-mono">echoCancellation</span>,{" "}
              <span className="font-mono">noiseSuppression</span>,{" "}
              <span className="font-mono">autoGainControl</span>.
            </li>
            <li>
              Device <span className="font-mono">AudioContext</span> is usually{" "}
              48 kHz; we resample to 24 kHz using simple linear interpolation
              (good for voice).
            </li>
            <li>
              Downlink TTS playback uses a scheduling clock and{" "}
              <span className="font-mono">
                {Math.round(LOOKAHEAD_SEC * 1000)}ms
              </span>{" "}
              prebuffer to avoid clicks/gaps.
            </li>
          </ul>
        </div>
      </div>
    </div>
  );
}
