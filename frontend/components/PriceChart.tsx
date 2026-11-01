"use client";

import { useMemo, useState } from "react";
import { CartesianGrid, Line, LineChart, ReferenceLine, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts";
import { money, type HistoryPoint } from "@/lib/api";

const RANGES = [
  { key: "1m", label: "1 month", days: 30 },
  { key: "3m", label: "3 months", days: 90 },
  { key: "all", label: "All", days: 0 },
] as const;

const fmtDate = (t: number) => new Date(t).toLocaleDateString("en-IN", { day: "numeric", month: "short" });

export default function PriceChart({ history, target }: { history: HistoryPoint[]; target?: number | null }) {
  const [range, setRange] = useState<(typeof RANGES)[number]["key"]>("all");
  const data = useMemo(() => {
    const days = RANGES.find((r) => r.key === range)!.days;
    const cutoff = days ? Date.now() - days * 864e5 : 0;
    const pts = history
      .map((h) => ({ t: new Date(h.t.endsWith("Z") || h.t.includes("+") ? h.t : h.t + "Z").getTime(), price: h.price, mrp: h.mrp }))
      .filter((p) => p.t >= cutoff);
    // carry the latest price to "now" so a flat stretch reads as still current
    if (pts.length) pts.push({ ...pts[pts.length - 1], t: Date.now() });
    return pts;
  }, [history, range]);

  if (history.length === 0) return null;
  const lows = data.map((d) => d.price);
  const lo = Math.min(...lows, target ?? Infinity), hi = Math.max(...lows);
  const pad = Math.max(50, (hi - lo) * 0.15);

  return (
    <div className="chart-box">
      <div className="ranges" role="group" aria-label="Time range">
        {RANGES.map((r) => (
          <button key={r.key} className="chip" aria-pressed={range === r.key} onClick={() => setRange(r.key)}>{r.label}</button>
        ))}
      </div>
      <div style={{ width: "100%", height: 260 }}>
        <ResponsiveContainer>
          <LineChart data={data} margin={{ top: 8, right: 18, bottom: 0, left: 8 }}>
            <CartesianGrid stroke="#e4e0ea" vertical={false} />
            <XAxis dataKey="t" type="number" scale="time" domain={["dataMin", "dataMax"]} tickFormatter={fmtDate}
              tick={{ fill: "#6c6a7b", fontSize: 12 }} axisLine={false} tickLine={false} minTickGap={40} />
            <YAxis domain={[Math.max(0, Math.floor(lo - pad)), Math.ceil(hi + pad)]} width={64}
              tickFormatter={(v) => money(v)} tick={{ fill: "#6c6a7b", fontSize: 12 }} axisLine={false} tickLine={false} />
            <Tooltip
              formatter={(v) => [money(Number(v)), "Price"]}
              labelFormatter={(t) => new Date(Number(t)).toLocaleString("en-IN", { day: "numeric", month: "short", hour: "numeric", minute: "2-digit" })}
              contentStyle={{ borderRadius: 8, border: "1px solid #dcd8e3", fontFamily: "inherit" }}
            />
            {target ? <ReferenceLine y={target} stroke="#e3a21a" strokeDasharray="5 4" label={{ value: `Your target ${money(target)}`, fill: "#8a5c00", fontSize: 12, position: "insideBottomRight" }} /> : null}
            <Line type="stepAfter" dataKey="price" stroke="#c8215f" strokeWidth={2.4} dot={data.length < 40 ? { r: 3, fill: "#c8215f" } : false} activeDot={{ r: 5 }} isAnimationActive={false} />
          </LineChart>
        </ResponsiveContainer>
      </div>
    </div>
  );
}
