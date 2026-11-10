"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

export default function Nav() {
  const path = usePathname();
  return (
    <header className="topbar">
      <Link href="/" className="brand" aria-label="StyleTrail home">
        <svg className="brand-mark" viewBox="0 0 22 30" aria-hidden="true">
          <path d="M5 0h12l5 5v25H0V5z" fill="currentColor" />
          <circle cx="11" cy="6" r="2.6" fill="#f5f3f7" />
        </svg>
        <span className="brand-word">StyleTrail</span>
      </Link>
      <nav className="nav" aria-label="Main">
        <Link href="/" aria-current={path === "/" ? "page" : undefined}>Search</Link>
        <Link href="/finder" aria-current={path.startsWith("/finder") ? "page" : undefined}>Finder</Link>
        <Link href="/watchlist" aria-current={path.startsWith("/watchlist") ? "page" : undefined}>Watchlist</Link>
      </nav>
    </header>
  );
}
