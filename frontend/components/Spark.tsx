export default function Spark({ points, width = 96, height = 28 }: { points: number[]; width?: number; height?: number }) {
  if (points.length < 2) return null;
  const lo = Math.min(...points), hi = Math.max(...points);
  const span = hi - lo || 1;
  const step = width / (points.length - 1);
  const xy = points.map((p, i) => [i * step, height - 3 - ((p - lo) / span) * (height - 6)] as const);
  const d = xy.map(([x, y], i) => `${i ? "L" : "M"}${x.toFixed(1)},${y.toFixed(1)}`).join(" ");
  const last = points[points.length - 1];
  const colour = last <= lo ? "#2e7a57" : last >= hi ? "#c8215f" : "#1e2350";
  const [lx, ly] = xy[xy.length - 1];
  return (
    <svg width={width} height={height} viewBox={`0 0 ${width} ${height}`} role="img"
      aria-label={`Price trend over ${points.length} checks`}>
      <path d={d} fill="none" stroke={colour} strokeWidth="1.8" strokeLinejoin="round" strokeLinecap="round" />
      <circle cx={lx} cy={ly} r="2.6" fill={colour} />
    </svg>
  );
}
