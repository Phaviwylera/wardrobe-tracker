"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import { api } from "@/lib/api";

type Box = { x: number; y: number; w: number; h: number }; // fractions of the image
const FULL: Box = { x: 0, y: 0, w: 1, h: 1 };
const ACCEPT = ["image/jpeg", "image/png", "image/webp", "image/avif", "image/gif"];

export default function Uploader() {
  const router = useRouter();
  const [file, setFile] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [box, setBox] = useState<Box>(FULL);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const stageRef = useRef<HTMLDivElement>(null);
  const drag = useRef<{ x: number; y: number } | null>(null);

  const take = useCallback((f: File | null | undefined) => {
    if (!f) return;
    if (!ACCEPT.includes(f.type)) {
      setError("That file isn't a photo we can read. Use a JPG, PNG or WEBP.");
      return;
    }
    if (f.size > 12 * 1024 * 1024) {
      setError("That photo is over 12 MB. Use a smaller one or a screenshot.");
      return;
    }
    setError(null);
    setFile(f);
    setBox(FULL);
    setPreview((old) => {
      if (old) URL.revokeObjectURL(old);
      return URL.createObjectURL(f);
    });
  }, []);

  // Paste a screenshot straight from the clipboard.
  useEffect(() => {
    const onPaste = (e: ClipboardEvent) => {
      const item = Array.from(e.clipboardData?.items ?? []).find((i) => i.type.startsWith("image/"));
      if (item) take(item.getAsFile());
    };
    window.addEventListener("paste", onPaste);
    return () => window.removeEventListener("paste", onPaste);
  }, [take]);

  const point = (e: React.PointerEvent) => {
    const r = stageRef.current!.getBoundingClientRect();
    return {
      x: Math.min(1, Math.max(0, (e.clientX - r.left) / r.width)),
      y: Math.min(1, Math.max(0, (e.clientY - r.top) / r.height)),
    };
  };
  const onDown = (e: React.PointerEvent) => {
    (e.target as HTMLElement).setPointerCapture(e.pointerId);
    drag.current = point(e);
    setBox({ ...drag.current, w: 0, h: 0 });
  };
  const onMove = (e: React.PointerEvent) => {
    if (!drag.current) return;
    const p = point(e), s = drag.current;
    setBox({ x: Math.min(s.x, p.x), y: Math.min(s.y, p.y), w: Math.abs(p.x - s.x), h: Math.abs(p.y - s.y) });
  };
  const onUp = () => {
    drag.current = null;
    setBox((b) => (b.w < 0.05 || b.h < 0.05 ? FULL : b));
  };

  async function submit() {
    if (!file) return;
    setBusy(true);
    setError(null);
    const fd = new FormData();
    fd.append("file", file);
    if (box.w < 0.999 || box.h < 0.999) {
      fd.append("crop_x", String(box.x));
      fd.append("crop_y", String(box.y));
      fd.append("crop_w", String(box.w));
      fd.append("crop_h", String(box.h));
    }
    try {
      const { id } = await api<{ id: string }>("/api/searches", { method: "POST", body: fd });
      router.push(`/search/${id}`);
    } catch (e) {
      setError((e as Error).message);
      setBusy(false);
    }
  }

  if (preview) {
    const cropped = box.w < 0.999 || box.h < 0.999;
    return (
      <div className="crop-wrap">
        <p className="muted">
          Drag over the piece you want if the photo shows more than one. Leave it as is to search the whole photo.
        </p>
        <div
          ref={stageRef}
          className="crop-stage"
          onPointerDown={onDown}
          onPointerMove={onMove}
          onPointerUp={onUp}
          role="img"
          aria-label="Your photo. Drag to select one garment."
        >
          {/* eslint-disable-next-line @next/next/no-img-element */}
          <img src={preview} alt="" />
          {cropped && (
            <div
              className="crop-box"
              style={{ left: `${box.x * 100}%`, top: `${box.y * 100}%`, width: `${box.w * 100}%`, height: `${box.h * 100}%` }}
            />
          )}
        </div>
        {error && <p className="notice" role="alert">{error}</p>}
        <div className="crop-actions">
          <button className="btn" onClick={submit} disabled={busy}>
            {busy ? "Starting search…" : cropped ? "Find this piece" : "Find this outfit"}
          </button>
          {cropped && <button className="btn quiet" onClick={() => setBox(FULL)} disabled={busy}>Use whole photo</button>}
          <button className="btn quiet" onClick={() => { setFile(null); setPreview(null); }} disabled={busy}>
            Choose another photo
          </button>
        </div>
      </div>
    );
  }

  return (
    <div>
      <span className="tag-string" aria-hidden="true" />
      <label
        className={`tag drop${over ? " over" : ""}`}
        onDragOver={(e) => { e.preventDefault(); setOver(true); }}
        onDragLeave={() => setOver(false)}
        onDrop={(e) => { e.preventDefault(); setOver(false); take(e.dataTransfer.files?.[0]); }}
      >
        <input ref={inputRef} type="file" accept={ACCEPT.join(",")} onChange={(e) => take(e.target.files?.[0])} />
        <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden="true">
          <path d="M22 8 32 14 42 8 56 16 50 28 44 25V56H20V25l-6 3-6-12z" fill="none" stroke="#1e2350" strokeWidth="2.5" strokeLinejoin="round" />
          <path d="M26 36h12M26 43h8" stroke="#c8215f" strokeWidth="2.5" strokeLinecap="round" />
        </svg>
        <span className="drop-title">Drop a photo of the outfit</span>
        <span className="btn">Choose photo</span>
        <span className="drop-hint">Screenshots from Instagram work. You can also paste one.</span>
        {error && <span className="notice small" role="alert">{error}</span>}
      </label>
    </div>
  );
}
