"use client";

import { useEffect, useRef, useState } from "react";

const WS_URL = process.env.NEXT_PUBLIC_WS_URL ?? "ws://localhost:8001/ws";

type ServerMsg =
  | { type: "hello"; message: string }
  | { type: "partial_transcript"; text: string }
  | { type: "final_transcript"; text: string; provider?: string; raw?: any }
  | { type: "error"; error: string }
  | { type: "info"; echo?: string }
  | { type: string; [k: string]: any };

export default function VoiceClient() {
  const [connected, setConnected] = useState(false);
  const [running, setRunning] = useState(false);
  const [framesSent, setFramesSent] = useState(0);
  const [framesDropped, setFramesDropped] = useState(0);
  const [liveText, setLiveText] = useState(""); // partial/live transcript
  const [finalLines, setFinalLines] = useState<string[]>([]); // finalized lines
  const [log, setLog] = useState<string[]>([]);
  const [error, setError] = useState<string | null>(null);

  const wsRef = useRef<WebSocket | null>(null);
  const ctxRef = useRef<AudioContext | null>(null);
  const streamRef = useRef<MediaStream | null>(null);
  const nodeRef = useRef<AudioWorkletNode | null>(null);
  const workletUrlRef = useRef<string | null>(null);

  // ---------- WebSocket ----------
  const connectWS = () =>
    new Promise<void>((resolve, reject) => {
      try {
        const ws = new WebSocket(WS_URL);
        ws.binaryType = "arraybuffer";

        ws.onopen = () => {
          setConnected(true);
          pushLog("ws: open");
          ws.send(
            JSON.stringify({
              type: "hello",
              client: "nextjs",
              format: "pcm_s16le_16k_20ms",
            })
          );
          resolve();
        };

        ws.onmessage = (ev) => {
          if (ev.data instanceof ArrayBuffer) {
            // If your server ever streams audio back, handle it here.
            pushLog(`ws: [binary ${ev.data.byteLength} bytes]`);
            return;
          }
          try {
            const msg: ServerMsg = JSON.parse(String(ev.data));
            if (msg.type === "partial_transcript") {
              setLiveText(msg.text ?? "");
            } else if (msg.type === "final_transcript") {
              // push finalized line and clear live buffer
              setFinalLines((L) => [...L, msg.text ?? "EMPTY"]);
              setLiveText("");
            } else if (msg.type === "hello") {
              pushLog(`server: ${msg.message}`);
            } else if (msg.type === "error") {
              pushLog(`error: ${msg.error}`);
              setError(msg.error);
            } else if (msg.type === "info") {
              pushLog(`info: ${JSON.stringify(msg)}`);
            } else {
              pushLog(`ws: ${String(ev.data)}`);
            }
          } catch {
            // plain text fallback
            pushLog(`ws: ${String(ev.data)}`);
          }
        };

        ws.onclose = () => {
          setConnected(false);
          pushLog("ws: close");
          if (running) stopCapture(); // safety
          wsRef.current = null;
        };

        ws.onerror = () => {
          pushLog("ws: error");
          setError("WebSocket error");
        };

        wsRef.current = ws;
      } catch (e: any) {
        reject(e);
      }
    });

  const disconnectWS = () => {
    try {
      wsRef.current?.close();
    } catch {}
    wsRef.current = null;
  };

  // ---------- Mic → 16kHz/20ms Int16 → WS ----------
  async function startCapture() {
    try {
      setError(null);
      if (!wsRef.current || wsRef.current.readyState !== WebSocket.OPEN) {
        await connectWS();
      }

      // 1) mic
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: {
          channelCount: 1,
          echoCancellation: true,
          noiseSuppression: true,
          autoGainControl: true,
        },
      });
      streamRef.current = stream;

      // 2) audio context (48k typical)
      const ctx = new (window.AudioContext ||
        (window as any).webkitAudioContext)({
        sampleRate: 48000,
      });
      ctxRef.current = ctx;

      // 3) inline worklet (downsample → 16k, 20ms frames, Int16 LE)
      const workletCode = `
        class PcmDownsampler extends AudioWorkletProcessor {
          constructor(options) {
            super();
            const opts = (options && options.processorOptions) || {};
            this.targetSr = opts.targetSampleRate || 16000;
            this.frameMs = opts.frameMs || 20;
            this.outSamplesPerFrame = Math.floor(this.targetSr * this.frameMs / 1000); // 320
            this.outBuf = new Float32Array(this.outSamplesPerFrame);
            this.outWrite = 0;
            this.ratio = sampleRate / this.targetSr; // e.g., 48000/16000 = 3
            this.inBuf = new Float32Array(0);
            this.inLen = 0;
            this.inRead = 0;
            this._pos = 0;
          }
          _appendInput(input) {
            if (!input || input.length === 0) return;
            const ch0 = input[0]; if (!ch0) return;
            const keep = this.inLen - this.inRead;
            const merged = new Float32Array(keep + ch0.length);
            if (keep > 0) merged.set(this.inBuf.subarray(this.inRead, this.inLen), 0);
            merged.set(ch0, keep);
            this.inBuf = merged; this.inLen = merged.length; this.inRead = 0;
          }
          _flushFrame() {
            const int16 = new Int16Array(this.outBuf.length);
            for (let i = 0; i < this.outBuf.length; i++) {
              const s = Math.max(-1, Math.min(1, this.outBuf[i]));
              int16[i] = (s * 0x7fff) | 0;
            }
            this.port.postMessage(int16.buffer, [int16.buffer]); // 640 bytes/frame
            this.outWrite = 0;
          }
          _produceOut() {
            while (true) {
              const nextPos = this._pos + this.ratio;
              const need = Math.ceil(nextPos) + 1;
              const avail = this.inLen - this.inRead;
              if (avail < need) break;
              const base = this.inRead + Math.floor(this._pos);
              const frac = this._pos - Math.floor(this._pos);
              const s0 = this.inBuf[base] || 0;
              const s1 = this.inBuf[base + 1] || s0;
              this.outBuf[this.outWrite++] = s0 + (s1 - s0) * frac;
              this._pos = nextPos;
              const consume = Math.floor(this._pos);
              if (consume > 0) { this.inRead += consume; this._pos -= consume; }
              if (this.outWrite >= this.outBuf.length) this._flushFrame();
            }
          }
          process(inputs) {
            this._appendInput(inputs[0]);
            this._produceOut();
            // compact
            if (this.inRead > 0) {
              const tail = this.inBuf.subarray(this.inRead, this.inLen);
              this.inBuf = new Float32Array(tail.length);
              this.inBuf.set(tail, 0);
              this.inLen = tail.length;
              this.inRead = 0;
            }
            return true;
          }
        }
        registerProcessor('pcm-downsampler', PcmDownsampler);
      `;
      const blob = new Blob([workletCode], { type: "text/javascript" });
      const url = URL.createObjectURL(blob);
      workletUrlRef.current = url;
      await ctx.audioWorklet.addModule(url);

      // 4) graph: mic → worklet → WS
      const src = ctx.createMediaStreamSource(stream);
      const node = new AudioWorkletNode(ctx, "pcm-downsampler", {
        processorOptions: { targetSampleRate: 16000, frameMs: 20 },
      });
      nodeRef.current = node;

      node.port.onmessage = (e: MessageEvent<ArrayBuffer>) => {
        const ws = wsRef.current;
        if (!ws || ws.readyState !== WebSocket.OPEN) return;
        if (ws.bufferedAmount > 1_000_000) {
          // simple backpressure
          setFramesDropped((d) => d + 1);
          return;
        }
        ws.send(e.data); // send each 20ms frame to server
        setFramesSent((n) => n + 1);
      };

      src.connect(node);
      setFramesSent(0);
      setFramesDropped(0);
      setLiveText("");
      setRunning(true);
    } catch (err: any) {
      console.error(err);
      setError(err?.message || String(err));
      stopCapture();
    }
  }

  function stopCapture() {
    setRunning(false);
    try {
      nodeRef.current?.port.close();
      nodeRef.current?.disconnect();
    } catch {}
    nodeRef.current = null;
    try {
      streamRef.current?.getTracks().forEach((t) => t.stop());
    } catch {}
    streamRef.current = null;
    try {
      ctxRef.current?.close();
    } catch {}
    ctxRef.current = null;
    if (workletUrlRef.current) {
      URL.revokeObjectURL(workletUrlRef.current);
      workletUrlRef.current = null;
    }
  }

  // ---------- helpers ----------
  const pushLog = (s: string) =>
    setLog((L) => [
      ...L.slice(-200),
      `[${new Date().toLocaleTimeString()}] ${s}`,
    ]);

  useEffect(() => {
    return () => {
      stopCapture();
      disconnectWS();
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);

  // ---------- UI ----------
  return (
    <div className="px-12 h-screen w-full space-y-4 rounded-xl border p-4">
      <h2 className="text-lg font-semibold">
        Voice Client · 16kHz/20ms → WS · Live Transcript
      </h2>

      <div className="flex flex-wrap gap-2">
        {!connected ? (
          <button
            onClick={connectWS}
            className="rounded bg-black px-3 py-2 text-white"
          >
            Connect WS
          </button>
        ) : (
          <button
            onClick={disconnectWS}
            className="rounded bg-gray-700 px-3 py-2 text-white"
          >
            Disconnect
          </button>
        )}

        {!running ? (
          <button
            onClick={startCapture}
            disabled={!connected}
            className={`rounded px-3 py-2 text-white ${
              connected ? "bg-green-600" : "bg-gray-400 cursor-not-allowed"
            }`}
          >
            Start Mic
          </button>
        ) : (
          <button
            onClick={stopCapture}
            className="rounded bg-red-600 px-3 py-2 text-white"
          >
            Stop Mic
          </button>
        )}
      </div>

      <div className="text-sm text-gray-700">
        WS: {connected ? "connected" : "disconnected"} · REC:{" "}
        {running ? "on" : "off"} · sent: {framesSent} · dropped: {framesDropped}
      </div>

      <div className="">
        {/* <div>
          <div className="text-sm font-medium">Live (partial)</div>
          <pre className="h-28 overflow-auto rounded bg-gray-50 p-2 text-sm">
            {liveText || "..."}
          </pre>
        </div> */}
        <div>
          <div className="text-sm font-lg">Final transcripts</div>
          <pre className="h-[60vh] overflow-auto rounded bg-gray-50 p-2 text-lg text-black">
            {finalLines.length
              ? finalLines.map((l, i) => `${i + 1}. ${l}`).join("\n")
              : "(none yet)"}
          </pre>
        </div>
      </div>

      <div>
        <div className="text-sm font-medium">Log</div>
        <pre className="h-32 overflow-auto rounded bg-gray-50 p-2 text-xs">
          {log.join("\n")}
        </pre>
      </div>

      {error && (
        <div className="rounded bg-red-50 p-2 text-sm text-red-700">
          {error}
        </div>
      )}
    </div>
  );
}
