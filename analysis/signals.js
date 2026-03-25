// analysis/signals.js
export async function compute(providers, settings) {
  const { spread } = providers.getAligned(); // EV − Price
  const y = spread.map(p=>p.y);
  const m = mean(y), s = stdev(y) || 1;
  const z = spread.map(p=>({ x:p.x, y:(p.y-m)/s }));

  const bands = [1, 2].map(k => ({
    name: `±${k}σ bands`,
    lines: [
      { points: z.map(p=>({ x:p.x, y: k })), color: "rgba(255,255,255,.35)" },
      { points: z.map(p=>({ x:p.x, y:-k })), color: "rgba(255,255,255,.35)" },
    ]
  }));

  return {
    title: "Signal Monitor (Spread Z-Score)",
    metrics: {
      "Current z-score": z?.[z.length-1]?.y ?? null,
      "Signal rule": "Buy case when z < -2; Sell when z > +2 (heuristic).",
    },
    series: [
      { name: "Spread Z-Score", lines: [{ points: z, color: "#a78bfa" }] },
      ...bands.map(b => ({ name: b.name, lines: b.lines })),
    ],
    notes: "Backtest this with transaction costs before using live.",
  };
}

function mean(a){ return a.reduce((s,v)=>s+v,0)/Math.max(1,a.length); }
function stdev(a){ const m=mean(a); return Math.sqrt(mean(a.map(v=>(v-m)**2))); }
