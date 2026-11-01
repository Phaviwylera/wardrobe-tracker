"use client";

import { Suspense } from "react";
import { useSearchParams } from "next/navigation";
import SearchView from "./SearchView";

function Inner() {
  const id = useSearchParams().get("id");
  if (!id) return <div className="empty"><h2>Nothing to show</h2><p className="muted">This link is missing its ID.</p></div>;
  return <SearchView key={id} id={id} />;
}

export default function Page() {
  return (
    <Suspense fallback={<div className="progress"><div className="stage">Loading…</div></div>}>
      <Inner />
    </Suspense>
  );
}
