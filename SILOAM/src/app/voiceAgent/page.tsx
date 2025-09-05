"use client";

import React, { useEffect, useRef, useState, useCallback } from "react";

// Type declarations for legacy getUserMedia API
declare global {
  interface Navigator {
    getUserMedia?: (
      constraints: MediaStreamConstraints,
      successCallback: (stream: MediaStream) => void,
      errorCallback: (error: any) => void
    ) => void;
  }
}

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
const VAD_HOLD_MS = 200; // sustained speech needed to barge-in (faster response)
const VAD_SPEECH_FACTOR = 2.5; // RMS >= noiseFloor * factor = speech (more sensitive)
const VAD_DECAY = 0.98; // noise floor decay (faster adaptation)
const VAD_RISE = 0.25; // noise floor rise on quiet frames (more responsive)

function clamp(n: number, lo = 0, hi = 1) {
  return Math.max(lo, Math.min(hi, n));
}

export default function VoiceClient({
  wsUrl = process.env.NEXT_PUBLIC_WS_URL || "ws://localhost:8000/ws",
}: Props) {
  // UI / status
  const [connected, setConnected] = useState(true); // Set to true for UI testing
  const [running, setRunning] = useState(false);
  const [status, setStatus] = useState<string>("");
  const [isInitialized, setIsInitialized] = useState(false);

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
  const [canvasSize, setCanvasSize] = useState({ width: 1000, height: 1000 });

  // Auto-initialize on mount (client-side only)
  useEffect(() => {
    if (!isInitialized && typeof window !== "undefined") {
      initializeVoice();
    }
  }, [isInitialized]);

  // Set canvas size on client-side only
  useEffect(() => {
    if (typeof window !== "undefined") {
      const updateCanvasSize = () => {
        setCanvasSize({
          width: window.innerWidth,
          height: window.innerHeight,
        });
      };

      updateCanvasSize();
      window.addEventListener("resize", updateCanvasSize);
      return () => window.removeEventListener("resize", updateCanvasSize);
    }
  }, []);

  // ---- WebSocket (DISABLED FOR UI TESTING) ----
  // const connectWS = () => {
  //   const ws = new WebSocket(wsUrl);
  //   ws.binaryType = "arraybuffer";
  //   ws.onopen = () => {
  //     wsOpenRef.current = true;
  //     setConnected(true);
  //     ws.send(JSON.stringify({ type: "client_ready" }));
  //   };
  //   ws.onclose = () => {
  //     wsOpenRef.current = false;
  //     setConnected(false);
  //   };
  //   ws.onerror = () => setStatus("websocket error");

  //   ws.onmessage = (ev) => {
  //     if (!ev.data) return;
  //     if (typeof ev.data !== "string") {
  //       // (Optional) handle server-sent binary audio here if you also stream TTS to the client
  //       return;
  //     }
  //     try {
  //       const msg = JSON.parse(ev.data);
  //       switch (msg.type) {
  //         case "tts_start":
  //           ttsPlayingRef.current = true;
  //           bargeSentRef.current = false;
  //           canUploadRef.current = false; // block mic streaming during TTS
  //           break;
  //         case "tts_end":
  //           ttsPlayingRef.current = false;
  //           bargeSentRef.current = false;
  //           canUploadRef.current = true; // resume mic streaming
  //           break;
  //         case "tts_level":
  //           ttsLevelRef.current = clamp(Number(msg.value) || 0, 0, 1);
  //           break;
  //         default:
  //           // app-specific messages (ASR/agent events)
  //           break;
  //       }
  //     } catch {
  //       // ignore
  //     }
  //   };

  //   wsRef.current = ws;
  // };

  // const safeSendJSON = (obj: any) => {
  //   const ws = wsRef.current;
  //   if (ws && wsOpenRef.current) ws.send(JSON.stringify(obj));
  // };

  // ---- Auto-initialize voice ----
  const initializeVoice = async () => {
    try {
      if (isInitialized) return;

      // Check if we're in browser environment
      if (typeof window === "undefined") {
        setStatus("Not in browser environment");
        return;
      }

      // Debug: Log browser capabilities
      console.log("Browser check:", {
        userAgent: navigator.userAgent,
        hasMediaDevices: !!navigator.mediaDevices,
        hasGetUserMedia: !!(
          navigator.mediaDevices && navigator.mediaDevices.getUserMedia
        ),
        protocol: location.protocol,
        hostname: location.hostname,
      });

      // Check if MediaDevices API is available
      if (!navigator.mediaDevices) {
        // Fallback for older browsers
        if (navigator.getUserMedia) {
          console.log("Using legacy getUserMedia API");
          // We'll handle this in the getUserMedia call
        } else {
          setStatus(
            "MediaDevices API not available - try Chrome, Firefox, or Safari"
          );
          return;
        }
      }

      if (!navigator.mediaDevices?.getUserMedia && !navigator.getUserMedia) {
        setStatus(
          "getUserMedia not supported - try Chrome, Firefox, or Safari"
        );
        return;
      }

      // Check if we're on HTTPS (required for microphone access)
      if (location.protocol !== "https:" && location.hostname !== "localhost") {
        setStatus("Microphone requires HTTPS connection");
        return;
      }

      setStatus("Initializing...");

      // Request microphone permission immediately
      let stream;
      if (navigator.mediaDevices && navigator.mediaDevices.getUserMedia) {
        // Modern API
        stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
            channelCount: 1,
            sampleRate: 48000,
          },
          video: false,
        });
      } else if (navigator.getUserMedia) {
        // Legacy API (fallback)
        stream = await new Promise((resolve, reject) => {
          navigator.getUserMedia!(
            { audio: true, video: false },
            resolve,
            reject
          );
        });
      } else {
        throw new Error("No getUserMedia API available");
      }

      streamRef.current = stream as MediaStream;
      setIsInitialized(true);
      setStatus("Ready");

      // Auto-start voice processing
      await start();
    } catch (err: any) {
      console.error("Voice initialization error:", err);

      // Handle specific error cases
      if (err.name === "NotAllowedError") {
        setStatus("Microphone permission denied");
      } else if (err.name === "NotFoundError") {
        setStatus("No microphone found");
      } else if (err.name === "NotSupportedError") {
        setStatus("Microphone not supported");
      } else if (err.name === "NotReadableError") {
        setStatus("Microphone is being used by another application");
      } else {
        setStatus(`Error: ${err?.message || "Failed to access microphone"}`);
      }
    }
  };

  // ---- Audio setup ----
  const start = async () => {
    try {
      if (running || !isInitialized) return;
      setStatus("");
      // connectWS(); // DISABLED FOR UI TESTING

      const stream = streamRef.current;
      if (!stream) {
        setStatus("No audio stream available");
        return;
      }

      // 1) audio graph
      const actx = new AudioContext();
      audioCtxRef.current = actx;

      const src = actx.createMediaStreamSource(stream);
      sourceRef.current = src;

      const analyser = actx.createAnalyser();
      analyser.fftSize = 1024;
      src.connect(analyser);
      analyserRef.current = analyser;

      // 2) media recorder (Opus chunks)
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
        // const buf = await e.data.arrayBuffer();
        // wsRef.current?.send(buf); // DISABLED FOR UI TESTING - send binary; change to JSON/base64 if your server prefers
        console.log("Audio chunk received:", e.data.size, "bytes"); // Debug log for UI testing
      };
      rec.start(MEDIA_RECORDER_TIMESLICE_MS);
      recRef.current = rec;

      // allow upstream after start
      canUploadRef.current = true;
      ttsPlayingRef.current = false;
      bargeSentRef.current = false;

      // 3) loops
      drawLoop();
      vadLoop();

      setRunning(true);
      setStatus("Listening...");
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
    setStatus("Stopped");
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
      // Enhanced VAD level calculation with more dynamic response
      const scaledRms = rms / 50; // More sensitive scaling
      const enhancedLevel = Math.pow(scaledRms, 0.6); // Power curve for more dynamic response
      lastUserLevelRef.current = clamp(enhancedLevel, 0, 1);

      // Debug logging (remove this later)
      if (lastUserLevelRef.current > 0.1) {
        console.log("Voice detected:", {
          rms: rms.toFixed(2),
          scaledRms: scaledRms.toFixed(2),
          level: lastUserLevelRef.current.toFixed(2),
          speech: speech,
        });
      }

      if (ttsPlayingRef.current) {
        if (speech) {
          speakingMsRef.current += 1000 / 60; // ~16.7ms/frame
          if (speakingMsRef.current >= VAD_HOLD_MS && !bargeSentRef.current) {
            bargeSentRef.current = true;
            // Ask server to cancel TTS; keep mic blocked until server says tts_end/ready
            // safeSendJSON({ type: "barge_in" }); // DISABLED FOR UI TESTING
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

  // ---- Full-screen minimalistic visualization ----
  const drawLoop = useCallback(() => {
    const cvs = canvasRef.current;
    const ctx = cvs?.getContext("2d");
    if (!cvs || !ctx) return;

    const W = canvasSize.width;
    const H = canvasSize.height;

    const render = () => {
      // Clear canvas
      ctx.clearRect(0, 0, W, H);

      // Create gradient background
      const gradient = ctx.createRadialGradient(
        W / 2,
        H / 2,
        0,
        W / 2,
        H / 2,
        Math.max(W, H) / 2
      );

      // Base colors with higher contrast
      const baseColor = running
        ? `rgba(255, 255, 255, ${0.08 + lastUserLevelRef.current * 0.25})`
        : "rgba(176, 49, 49, 0.05)";

      const accentColor = running
        ? `rgba(0, 255, 255, ${0.12 + lastUserLevelRef.current * 0.35})`
        : "rgba(0, 255, 255, 0.08)";

      gradient.addColorStop(0, baseColor);
      gradient.addColorStop(0.3, accentColor);
      gradient.addColorStop(0.7, baseColor);
      gradient.addColorStop(1, "rgba(0, 0, 0, 0)");

      // Fill with gradient
      ctx.fillStyle = gradient;
      ctx.fillRect(0, 0, W, H);

      // Add dynamic shimmer effect with higher contrast
      if (running) {
        const time = performance.now() / 1000;
        const shimmerGradient = ctx.createLinearGradient(0, 0, W, H);
        const shimmerIntensity = 0.15 + lastUserLevelRef.current * 0.25;

        shimmerGradient.addColorStop(
          0,
          `rgba(0, 255, 255, ${shimmerIntensity * Math.sin(time * 2)})`
        );
        shimmerGradient.addColorStop(
          0.5,
          `rgba(255, 255, 0, ${shimmerIntensity * Math.sin(time * 1.5 + 1)})`
        );
        shimmerGradient.addColorStop(
          1,
          `rgba(255, 255, 255, ${shimmerIntensity * Math.sin(time * 2.5 + 2)})`
        );

        ctx.fillStyle = shimmerGradient;
        ctx.fillRect(0, 0, W, H);
      }

      // Add dynamic pulsing center dot with multiple layers
      // Always show a dot when running for testing
      if (running) {
        const centerX = W / 2;
        const centerY = H / 2;
        const time = performance.now() / 1000;

        // Always show a base dot when running
        const baseRadius = 3 + 2 * Math.sin(time * 1.5);
        const baseGradient = ctx.createRadialGradient(
          centerX,
          centerY,
          0,
          centerX,
          centerY,
          baseRadius
        );
        baseGradient.addColorStop(0, `rgba(255, 255, 255, 0.8)`);
        baseGradient.addColorStop(0.5, `rgba(0, 255, 255, 0.4)`);
        baseGradient.addColorStop(1, "rgba(255, 255, 255, 0)");

        ctx.fillStyle = baseGradient;
        ctx.beginPath();
        ctx.arc(centerX, centerY, baseRadius, 0, Math.PI * 2);
        ctx.fill();
      }

      if (running && lastUserLevelRef.current > 0.01) {
        const centerX = W / 2;
        const centerY = H / 2;
        const time = performance.now() / 1000;

        // Create multiple pulsing layers for more dynamic effect
        const layers = [
          {
            baseRadius: 3 + lastUserLevelRef.current * 12,
            pulseMultiplier: 1.0 + 0.3 * Math.sin(time * 4),
            opacity: 0.8 + lastUserLevelRef.current * 0.2,
            color: [255, 255, 255],
          },
          {
            baseRadius: 2 + lastUserLevelRef.current * 8,
            pulseMultiplier: 1.0 + 0.5 * Math.sin(time * 6 + 1),
            opacity: 0.6 + lastUserLevelRef.current * 0.3,
            color: [0, 255, 255],
          },
          {
            baseRadius: 1 + lastUserLevelRef.current * 6,
            pulseMultiplier: 1.0 + 0.7 * Math.sin(time * 8 + 2),
            opacity: 0.9 + lastUserLevelRef.current * 0.1,
            color: [255, 255, 0],
          },
        ];

        layers.forEach((layer, index) => {
          const radius = layer.baseRadius * layer.pulseMultiplier;

          const pulseGradient = ctx.createRadialGradient(
            centerX,
            centerY,
            0,
            centerX,
            centerY,
            radius
          );

          pulseGradient.addColorStop(
            0,
            `rgba(${layer.color.join(", ")}, ${layer.opacity})`
          );
          pulseGradient.addColorStop(
            0.3,
            `rgba(${layer.color.join(", ")}, ${layer.opacity * 0.6})`
          );
          pulseGradient.addColorStop(
            0.7,
            `rgba(${layer.color.join(", ")}, ${layer.opacity * 0.2})`
          );
          pulseGradient.addColorStop(1, "rgba(255, 255, 255, 0)");

          ctx.fillStyle = pulseGradient;
          ctx.beginPath();
          ctx.arc(centerX, centerY, radius, 0, Math.PI * 2);
          ctx.fill();
        });

        // Add outer ripple effect
        if (lastUserLevelRef.current > 0.3) {
          const rippleRadius = 15 + lastUserLevelRef.current * 25;
          const rippleOpacity = 0.3 * Math.sin(time * 3);

          const rippleGradient = ctx.createRadialGradient(
            centerX,
            centerY,
            0,
            centerX,
            centerY,
            rippleRadius
          );

          rippleGradient.addColorStop(0, `rgba(0, 255, 255, ${rippleOpacity})`);
          rippleGradient.addColorStop(
            0.5,
            `rgba(255, 255, 0, ${rippleOpacity * 0.5})`
          );
          rippleGradient.addColorStop(1, "rgba(255, 255, 255, 0)");

          ctx.fillStyle = rippleGradient;
          ctx.beginPath();
          ctx.arc(centerX, centerY, rippleRadius, 0, Math.PI * 2);
          ctx.fill();
        }
      }

      rafRef.current = requestAnimationFrame(render);
    };
    requestAnimationFrame(render);
  }, [canvasSize.width, canvasSize.height, running]);

  // ---- cleanup on unmount ----
  useEffect(() => {
    return () => stop();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  return (
    <div className="fixed inset-0 w-full h-full overflow-hidden">
      {/* Full-screen canvas */}
      <canvas
        ref={canvasRef}
        width={canvasSize.width}
        height={canvasSize.height}
        className="absolute inset-0 w-full h-full"
        style={{
          background:
            "radial-gradient(circle at center, rgba(0,0,0,0.1) 0%, rgba(0,0,0,0.3) 100%)",
        }}
      />

      {/* Minimal status overlay */}
      <div className="absolute top-4 left-4 z-10">
        <div className="flex items-center gap-3">
          <div
            className={`h-2 w-2 rounded-full ${
              running
                ? "bg-green-400 animate-pulse"
                : connected
                ? "bg-yellow-400"
                : "bg-gray-400"
            }`}
          />
          <span className="text-white/60 text-sm font-mono">
            {running
              ? "listening"
              : connected
              ? "ui testing mode"
              : "connecting..."}
          </span>
        </div>
      </div>

      {/* Minimal control overlay */}
      <div className="absolute bottom-4 right-4 z-10">
        {running ? (
          <button
            onClick={stop}
            className="px-4 py-2 rounded-full bg-white/10 backdrop-blur-sm border border-white/20 text-white hover:bg-white/20 transition-all duration-200"
          >
            Stop
          </button>
        ) : isInitialized ? (
          <button
            onClick={start}
            className="px-4 py-2 rounded-full bg-white/10 backdrop-blur-sm border border-white/20 text-white hover:bg-white/20 transition-all duration-200"
          >
            Start
          </button>
        ) : status?.includes("Error") ||
          status?.includes("denied") ||
          status?.includes("not supported") ? (
          <button
            onClick={() => {
              setIsInitialized(false);
              initializeVoice();
            }}
            className="px-4 py-2 rounded-full bg-white/10 backdrop-blur-sm border border-white/20 text-white hover:bg-white/20 transition-all duration-200"
          >
            Retry
          </button>
        ) : (
          <div className="px-4 py-2 rounded-full bg-white/5 backdrop-blur-sm border border-white/10 text-white/60">
            Initializing...
          </div>
        )}
      </div>

      {/* Status message */}
      {status && (
        <div className="absolute top-1/2 left-1/2 transform -translate-x-1/2 -translate-y-1/2 z-10">
          <div className="px-6 py-3 rounded-full bg-black/20 backdrop-blur-sm border border-white/10 text-white/80 text-sm">
            {status}
          </div>
        </div>
      )}
    </div>
  );
}
