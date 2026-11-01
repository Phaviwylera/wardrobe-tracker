// Empty = same origin: Next.js proxies /api/* to the backend (see next.config.mjs).
export const API = (process.env.NEXT_PUBLIC_API_URL || "").replace(/\/$/, "");

export type Product = {
  id: string;
  url: string;
  store_key: string;
  store_name: string;
  domain: string;
  title: string | null;
  brand: string | null;
  image_url: string | null;
  currency: string;
  current_price: number | null;
  mrp: number | null;
  discount_pct: number | null;
  in_stock: boolean | null;
  sizes: { label: string; available: boolean | null }[] | null;
  rating: number | null;
  rating_count: number | null;
  last_checked: string | null;
  live: boolean;
};

export type MatchLevel = "exact" | "close" | "similar";

export type Result = {
  rank: number;
  score: number;
  match_level: MatchLevel;
  signals: Record<string, unknown> | null;
  sources: string[] | null;
  product: Product;
  spark: number[];
  lowest: number | null;
};

export type Attributes = {
  garment?: string;
  gender?: string;
  colours?: string[];
  pattern?: string;
  fabric?: string | null;
  details?: string[];
  brand?: string | null;
  queries?: string[];
};

export type SearchState = {
  id: string;
  image_id: string;
  status: "queued" | "searching" | "verifying" | "pricing" | "done" | "failed";
  stage: string;
  progress: number;
  error: string | null;
  attributes: Attributes | null;
  stats: { results?: number; candidates?: number; listings?: number; priced?: number; seconds?: number } | null;
  created_at: string;
  results: Result[];
};

export type HistoryPoint = { t: string; price: number; mrp: number | null; in_stock: boolean | null; source: string };

export type PriceStats = {
  count: number;
  current?: number;
  lowest?: number;
  highest?: number;
  average?: number;
  since?: string;
  updated?: string;
  change?: number | null;
  verdict?: "lowest" | "good" | "fair" | "high" | "tracking";
};

export type ProductDetail = {
  product: Product;
  history: HistoryPoint[];
  stats: PriceStats;
  other_stores: Product[];
  watch: { target_price: number | null; email: string | null } | null;
};

export type WatchRow = {
  product: Product;
  target_price: number | null;
  email: string | null;
  spark: number[];
  lowest: number | null;
  hit: boolean;
  added: string;
};

export type RecentSearch = {
  id: string;
  image_id: string;
  status: string;
  created_at: string;
  garment: string | null;
  results: number;
};

export function clientId(): string {
  if (typeof window === "undefined") return "server-render-00";
  try {
    let id = window.localStorage.getItem("st_client");
    if (!id) {
      id = (crypto.randomUUID?.() ?? Math.random().toString(36).slice(2) + Date.now().toString(36)).replace(/[^a-z0-9-]/gi, "");
      window.localStorage.setItem("st_client", id);
    }
    return id;
  } catch {
    return "anonymous-client-0";
  }
}

export class ApiError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.status = status;
  }
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers);
  headers.set("X-Client-Id", clientId());
  if (init.body && !(init.body instanceof FormData)) headers.set("Content-Type", "application/json");
  let res: Response;
  try {
    res = await fetch(`${API}${path}`, { ...init, headers, cache: "no-store" });
  } catch {
    throw new ApiError(0, "Can't reach the StyleTrail server. Check your connection and try again.");
  }
  if (!res.ok) {
    let msg = `Request failed (${res.status})`;
    try {
      const body = await res.json();
      if (typeof body.detail === "string") msg = body.detail;
    } catch {}
    throw new ApiError(res.status, msg);
  }
  return res.json() as Promise<T>;
}

export const imageUrl = (imageId: string) => `${API}/api/images/${imageId}.jpg`;

const inr = new Intl.NumberFormat("en-IN", { maximumFractionDigits: 0 });
export function money(v: number | null | undefined, currency = "INR"): string {
  if (v == null) return "—";
  return currency === "INR" ? `₹${inr.format(v)}` : `${currency} ${inr.format(v)}`;
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return "never";
  const t = new Date(iso.endsWith("Z") || iso.includes("+") ? iso : iso + "Z").getTime();
  const m = Math.round((Date.now() - t) / 60000);
  if (m < 1) return "just now";
  if (m < 60) return `${m} min ago`;
  const h = Math.round(m / 60);
  if (h < 24) return `${h} h ago`;
  const d = Math.round(h / 24);
  return d === 1 ? "yesterday" : `${d} days ago`;
}

export const LEVEL_LABEL: Record<MatchLevel, string> = {
  exact: "Exact match",
  close: "Very close",
  similar: "Similar style",
};
