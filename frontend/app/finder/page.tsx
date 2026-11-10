"use client";

import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { finderBookmarklet } from "@/lib/finder";

export default function FinderPage() {
  const [code, setCode] = useState("");
  const [copied, setCopied] = useState(false);
  const dragRef = useRef<HTMLAnchorElement>(null);

  useEffect(() => {
    const c = finderBookmarklet(window.location.origin);
    setCode(c);
    // React refuses javascript: hrefs in JSX; set it directly so the link can be dragged to the bookmarks bar.
    dragRef.current?.setAttribute("href", c);
  }, []);

  async function copy() {
    try {
      await navigator.clipboard.writeText(code);
    } catch {
      const t = document.getElementById("finder-code") as HTMLTextAreaElement | null;
      t?.select();
      document.execCommand("copy");
    }
    setCopied(true);
    setTimeout(() => setCopied(false), 2500);
  }

  return (
    <div className="split finder" style={{ gap: 32, maxWidth: 760 }}>
      <div className="split" style={{ gap: 12 }}>
        <h1>StyleTrail Finder</h1>
        <p className="muted">
          A bookmark that works on your phone and your computer, no app or extension needed. On a Myntra page you
          opened from your results, tap it: it finds your piece on the page, highlights it, and adds its link to your
          results. On a Myntra product page, it saves the price to that product&rsquo;s price history.
        </p>
      </div>

      <section className="split" style={{ gap: 12 }}>
        <h2>1. Copy the Finder</h2>
        <div className="finder-copy">
          <button className="btn" onClick={copy} disabled={!code}>{copied ? "Copied" : "Copy Finder code"}</button>
          <a ref={dragRef} className="btn quiet" onClick={(e) => e.preventDefault()} title="Drag me to your bookmarks bar">
            StyleTrail Finder
          </a>
          <span className="muted small">On a computer you can drag this button to your bookmarks bar instead.</span>
        </div>
        <textarea id="finder-code" className="input finder-code" readOnly value={code} rows={3} aria-label="Finder code" />
      </section>

      <section className="split" style={{ gap: 12 }}>
        <h2>2. Save it as a bookmark</h2>
        <div className="finder-steps">
          <div>
            <h3>Android, Chrome</h3>
            <ol>
              <li>On this page, tap ⋮ then the ☆ star to bookmark it.</li>
              <li>Tap <b>Edit</b>. Name it <b>Finder</b>.</li>
              <li>Clear the URL, paste the Finder code, and save.</li>
            </ol>
          </div>
          <div>
            <h3>iPhone, Safari</h3>
            <ol>
              <li>Tap Share, then <b>Add Bookmark</b>, and save.</li>
              <li>Open Bookmarks, tap <b>Edit</b>, then this bookmark.</li>
              <li>Name it <b>Finder</b>, replace the address with the Finder code, and tap Done.</li>
            </ol>
          </div>
        </div>
      </section>

      <section className="split" style={{ gap: 12 }}>
        <h2>3. Use it on Myntra</h2>
        <ol className="finder-use">
          <li>Search with your photo on StyleTrail, then open a link from <b>Spotted on</b>.
            If Myntra tries to open its app, choose to stay in the browser.</li>
          <li><b>Android:</b> tap the address bar, type <b>Finder</b>, and tap the bookmark that appears.
            <b> iPhone / computer:</b> open your bookmarks and tap <b>Finder</b>.</li>
          <li>Scroll the page. Your piece gets a pink <b>Your piece</b> label and is added to your results.</li>
        </ol>
        <p className="muted small">
          Found it in the Myntra app instead? Share the product, copy the link, and paste it into
          &ldquo;Found it yourself?&rdquo; on your results page.
        </p>
      </section>

      <div><Link className="btn quiet" href="/">Back to search</Link></div>
    </div>
  );
}
