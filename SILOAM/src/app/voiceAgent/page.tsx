"use client";

import React, { useEffect, useRef, useState } from "react";

/**
 * FastAPI WS contract (suggested):
 *   client -> server:
 *     { type: "client_ready" }
 *     Binary audio frames (audio/webm;codecs=opus)
 *     { type: "barge_in" }
 *
 *   server -> client:
 *     { type: "tts_start" }       // begin speaking
 *     { type: "tts_end" }         // done speaking; safe to resume mic streaming
 *     { type: "tts_level", value: number } // optional 0..1 meter for UI
 */

type Props = {
  wsUrl?: string; // e.g., "ws://localhost:8000/ws"
};

const MEDIA_RECORDER_TIMESLICE_MS = 200; // ~200ms Opus chunks
const VAD_HOLD_MS = 260; // sustained speech needed to barge-in
const VAD_SPEECH_FACTOR = 3.0; // RMS >= noiseFloor * factor = speech
const VAD_DECAY = 0.985; // noise floor decay
const VAD_RISE = 0.18; // noise floor rise on quiet frames

function clamp(n: number, lo = 0, hi = 1) {
  return Math.max(lo, Math.min(hi, n));
}

export default function VoiceClient({
  wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws",
}: Props) {
  // UI / status
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<string>("");

  // WebSocket
  const wsRef = useRef<WebSocket | null>(null);
  const wsOpenRef = useRef(false);

  // Audio graph
  const audioCtxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const sourceRef = useRef<MediaStreamAudioSourceNode | null>(null);
  const analyserRef = useRef<AnalyserNode | null>(null);
  const recRef = useRef<MediaRecorder | null>(null);

  // Duplex control
  const ttsPlayingRef = useRef(false);
  const bargeSentRef = useRef(false);
  const canUploadRef = useRef(false); // gate mic upload → false while TTS speaking

  // VAD state
  const noiseFloorRef = useRef(200); // adaptive baseline
  const speakingMsRef = useRef(0);
  const lastUserLevelRef = useRef(0); // 0..1
  const ttsLevelRef = useRef(0); // 0..1 (optional; animates if not provided)

  // Canvas
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const rafRef = useRef(0);

  // ---- WebSocket ----
  const connectWS = () => {
    const ws = new WebSocket(wsUrl);
    ws.binaryType = "arraybuffer";
    ws.onopen = () => {
      wsOpenRef.current = true;
      setConnected(true);
      ws.send(JSON.stringify({ type: "client_ready" }));
    };
    ws.onclose = () => {
      wsOpenRef.current = false;
      setConnected(false);
    };
    ws.onerror = () => setStatus("websocket error");

    ws.onmessage = (ev) => {
      if (!ev.data) return;
      if (typeof ev.data !== "string") {
        // (Optional) handle server-sent binary audio here if you also stream TTS to the client
        return;
      }
      try {
        const msg = JSON.parse(ev.data);
        switch (msg.type) {
          case "tts_start":
            ttsPlayingRef.current = true;
            bargeSentRef.current = false;
            canUploadRef.current = false; // block mic streaming during TTS
            break;
          case "tts_end":
            ttsPlayingRef.current = false;
            bargeSentRef.current = false;
            canUploadRef.current = true; // resume mic streaming
            break;
          case "tts_level":
            ttsLevelRef.current = clamp(Number(msg.value) || 0, 0, 1);
            break;
          default:
            // app-specific messages (ASR/agent events)
            break;
        }
      } catch {
        // ignore
      }
    };

    wsRef.current = ws;
  };

  const safeSendJSON = (obj: any) => {
    const ws = wsRef.current;
    if (ws && wsOpenRef.current) ws.send(JSON.stringify(obj));
  };

  // ---- Audio setup ----
  const start = async () => {
    try {
      if (running) return;
      setStatus("");
      connectWS();

      // 1) get user media with AEC/NS/AGC
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
          channelCount: 1,
          sampleRate: 48000, // browser native; encoder handles it
        },
        video: false,
      });
      streamRef.current = stream;

      // 2) audio graph
      const actx = new AudioContext();
      audioCtxRef.current = actx;

      const src = actx.createMediaStreamSource(stream);
      sourceRef.current = src;

      const analyser = actx.createAnalyser();
      analyser.fftSize = 1024;
      src.connect(analyser);
      analyserRef.current = analyser;

      // 3) media recorder (Opus chunks)
      const mime = MediaRecorder.isTypeSupported("audio/webm;codecs=opus")
        ? "audio/webm;codecs=opus"
        : "audio/webm";
      const rec = new MediaRecorder(stream, {
        mimeType: mime,
        audioBitsPerSecond: 32000,
      });
      rec.ondataavailable = async (e) => {
        if (!e.data || e.data.size === 0) return;
        if (!canUploadRef.current) return; // block while TTS speaking
        const buf = await e.data.arrayBuffer();
        wsRef.current?.send(buf); // send binary; change to JSON/base64 if your server prefers
      };
      rec.start(MEDIA_RECORDER_TIMESLICE_MS);
      recRef.current = rec;

      // allow upstream after start
      canUploadRef.current = true;
      ttsPlayingRef.current = false;
      bargeSentRef.current = false;

      // 4) loops
      drawLoop();
      vadLoop();

      setRunning(true);
    } catch (err: any) {
      setStatus(err?.message || "start error");
      stop();
    }
  };

  const stop = () => {
    cancelAnimationFrame(rafRef.current);

    recRef.current?.state !== "inactive" && recRef.current?.stop();
    recRef.current = null;

    analyserRef.current?.disconnect();
    analyserRef.current = null;

    sourceRef.current?.disconnect();
    sourceRef.current = null;

    streamRef.current?.getTracks().forEach((t) => t.stop());
    streamRef.current = null;

    audioCtxRef.current?.close();
    audioCtxRef.current = null;

    wsRef.current && wsOpenRef.current && wsRef.current.close();
    wsRef.current = null;
    wsOpenRef.current = false;

    canUploadRef.current = false;
    ttsPlayingRef.current = false;
    bargeSentRef.current = false;

    setRunning(false);
    setStatus("");
  };

  // ---- VAD (RMS + hysteresis) + barge-in ----
  const vadLoop = () => {
    const an = analyserRef.current;
    if (!an) return;

    const tick = () => {
      const buf = new Uint8Array(an.fftSize);
      an.getByteTimeDomainData(buf);

      // RMS (0..128-ish), center 128
      let sum = 0;
      for (let i = 0; i < buf.length; i++) {
        const v = buf[i] - 128;
        sum += v * v;
      }
      const rms = Math.sqrt(sum / buf.length);

      // Adaptive noise floor
      noiseFloorRef.current = Math.max(1, noiseFloorRef.current * VAD_DECAY);
      if (rms < noiseFloorRef.current) {
        noiseFloorRef.current =
          noiseFloorRef.current * (1 - VAD_RISE) + rms * VAD_RISE;
      }

      const speech = rms >= noiseFloorRef.current * VAD_SPEECH_FACTOR;
      lastUserLevelRef.current = clamp(rms / 64, 0, 1);

      if (ttsPlayingRef.current) {
        if (speech) {
          speakingMsRef.current += 1000 / 60; // ~16.7ms/frame
          if (speakingMsRef.current >= VAD_HOLD_MS && !bargeSentRef.current) {
            bargeSentRef.current = true;
            // Ask server to cancel TTS; keep mic blocked until server says tts_end/ready
            safeSendJSON({ type: "barge_in" });
            setStatus("barge-in sent…");
          }
        } else {
          // quicker decay
          speakingMsRef.current = Math.max(
            0,
            speakingMsRef.current - 2 * (1000 / 60)
          );
        }
        // canUploadRef stays false while ttsPlayingRef is true (set in tts_start)
      } else {
        speakingMsRef.current = 0;
        // animate TTS line down when idle if server doesn't send tts_level
        ttsLevelRef.current *= 0.92;
        setStatus("");
      }

      // If server does not send tts_level, keep a subtle pulse while ttsPlaying
      if (ttsPlayingRef.current && ttsLevelRef.current < 0.4) {
        ttsLevelRef.current = ttsLevelRef.current * 0.9 + 0.35;
      }

      requestAnimationFrame(tick);
    };
    requestAnimationFrame(tick);
  };

  // ---- Canvas dual-wave draw ----
  const drawLoop = () => {
    const cvs = canvasRef.current;
    const ctx = cvs?.getContext("2d");
    if (!cvs || !ctx) return;

    const W = cvs.width;
    const H = cvs.height;

    const render = () => {
      // backdrop (subtle gradient / glassy card feel comes from container styles)
      ctx.clearRect(0, 0, W, H);

      // horizontal dividers
      ctx.strokeStyle = "rgba(255,255,255,0.06)";
      ctx.lineWidth = 1;
      [0.35 * H, 0.7 * H].forEach((y) => {
        ctx.beginPath();
        ctx.moveTo(0, y);
        ctx.lineTo(W, y);
        ctx.stroke();
      });

      // User wave (top) — trendy cyan
      const userAmp = 6 + lastUserLevelRef.current * 28;
      ctx.strokeStyle = "rgba(56,189,248,0.95)"; // tailwind cyan-400
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      for (let x = 0; x < W; x++) {
        const y =
          0.28 * H +
          Math.sin(x / 26 + performance.now() / 420) * userAmp * 0.35;
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();

      // TTS wave (bottom) — amber
      const ttsAmp = 4 + ttsLevelRef.current * 26;
      ctx.strokeStyle = "rgba(245,158,11,0.95)"; // tailwind amber-500
      ctx.lineWidth = 2.5;
      ctx.beginPath();
      for (let x = 0; x < W; x++) {
        const y =
          0.78 * H +
          Math.sin(x / 24 + performance.now() / 520 + 0.6) * ttsAmp * 0.33;
        x === 0 ? ctx.moveTo(x, y) : ctx.lineTo(x, y);
      }
      ctx.stroke();

      rafRef.current = requestAnimationFrame(render);
    };

    rafRef.current = requestAnimationFrame(render);
  };

  // ---- cleanup on unmount ----
  useEffect(() => {
    return () => stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="w-full max-w-3xl mx-auto">
      <div className="relative overflow-hidden rounded-2xl border border-zinc-800/60 bg-gradient-to-b from-zinc-900/60 to-zinc-950/80 backdrop-blur">
        {/* subtle glow */}
        <div className="pointer-events-none absolute -inset-40 bg-[radial-gradient(60%_60%_at_50%_0%,rgba(59,130,246,0.15),transparent)]" />
        <div className="p-4 sm:p-5 relative">
          <div className="flex items-center gap-3">
            <div className="h-8 w-8 rounded-xl bg-zinc-800/80 border border-zinc-700/60 flex items-center justify-center">
              <span className="text-xs text-zinc-300">🎙️</span>
            </div>
            <div className="text-sm text-zinc-300">
              Voice (local VAD + barge-in)
            </div>
            <div className="ml-auto flex items-center gap-2">
              <span
                className={`px-2.5 py-1 rounded-full text-xs border ${
                  connected
                    ? "border-emerald-500/40 text-emerald-300/90 bg-emerald-900/20"
                    : "border-zinc-600 text-zinc-300 bg-zinc-800/40"
                }`}
              >
                {connected ? "ws: connected" : "ws: disconnected"}
              </span>
              <span
                className={`px-2.5 py-1 rounded-full text-xs border ${
                  running
                    ? "border-sky-500/40 text-sky-300/90 bg-sky-900/20"
                    : "border-zinc-600 text-zinc-300 bg-zinc-800/40"
                }`}
              >
                {running
                  ? ttsPlayingRef.current
                    ? "tts: speaking"
                    : "ready"
                  : "idle"}
              </span>
            </div>
          </div>

          <div className="mt-4 rounded-xl border border-zinc-800 overflow-hidden bg-zinc-900/60">
            <canvas
              ref={canvasRef}
              width={1000}
              height={160}
              className="block w-full h-[160px]"
            />
            <div className="flex items-center justify-between px-3 py-2">
              <div className="flex items-center gap-3 text-xs text-zinc-400">
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded-full bg-cyan-400" />
                  User
                </div>
                <div className="flex items-center gap-1.5">
                  <span className="inline-block h-2 w-2 rounded-full bg-amber-500" />
                  TTS
                </div>
              </div>
              <div className="text-xs text-zinc-400">{status}</div>
            </div>
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
              <>
                <button
                  onClick={stop}
                  className="px-4 py-2 rounded-xl bg-zinc-800 hover:bg-zinc-700 text-zinc-100 font-medium border border-zinc-700"
                >
                  Stop
                </button>
              </>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}
