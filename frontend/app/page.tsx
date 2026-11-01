"use client";

import Link from "next/link";
import { useEffect, useState } from "react";
import Uploader from "@/components/Uploader";
import { api, imageUrl, type RecentSearch } from "@/lib/api";

const STORES = ["Myntra", "AJIO", "Nykaa Fashion", "Flipkart", "Amazon.in", "Meesho", "Tata CLiQ", "Libas", "BIBA", "W", "Fabindia", "Zara", "H&M", "and 25 more"];

export default function Home() {
  const [recent, setRecent] = useState<RecentSearch[]>([]);
  useEffect(() => {
    api<RecentSearch[]>("/api/searches").then(setRecent).catch(() => {});
  }, []);

  return (
    <>
      <section className="hero">
        <div className="hero-copy">
          <h1>Saw a kurta you love? Find every shop selling it.</h1>
          <p>
            Upload a photo or a screenshot. We check it against listings across Indian stores, show you which ones are
            the exact same piece, and how each price has moved.
          </p>
          <div className="store-strip" aria-label="Stores we check">
            {STORES.map((s) => <span key={s}>{s}</span>)}
          </div>
        </div>
        <Uploader />
      </section>

      {recent.length > 0 && (
        <section>
          <div className="section-head">
            <h2>Your recent searches</h2>
          </div>
          <div className="recent">
            {recent.slice(0, 12).map((r) => (
              <Link key={r.id} href={`/search/${r.id}`}>
                {/* eslint-disable-next-line @next/next/no-img-element */}
                <img src={imageUrl(r.image_id)} alt={r.garment ?? "Searched outfit"} loading="lazy" />
                <span className="small">
                  {r.status === "done" ? `${r.results} ${r.results === 1 ? "shop" : "shops"}` : r.status === "failed" ? "Didn't finish" : "Searching…"}
                </span>
              </Link>
            ))}
          </div>
        </section>
      )}

      <section className="how" aria-label="How it works">
        <div>
          <h3>Same piece, not lookalikes</h3>
          <p className="muted">Each listing's photo is compared with yours for print, cut and colour. Exact matches are stamped, so a blue version of your pink kurta won't pass.</p>
        </div>
        <div>
          <h3>Live prices from the store page</h3>
          <p className="muted">We open each product page for today's price, MRP, sizes in stock and ratings.</p>
        </div>
        <div>
          <h3>Price history and alerts</h3>
          <p className="muted">Prices are rechecked every few hours. Set a target price and we'll tell you when it drops.</p>
        </div>
      </section>
    </>
  );
}
