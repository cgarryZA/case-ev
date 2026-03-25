// analysis/cross_section.js
// With a single case, we provide relative value diagnostics (EV/Price, premium/discount).
export async function compute(providers, settings) {
  const { ev, price } = providers.getAligned();
  const ratio = ev.map((p,i)=>({ x:p.x, y: safe(price[i].y) ? p.y/price[i].y : 0 }));
  const prem  = ev.map((p,i)=>({ x:p.x, y: p.y - price[i].y }));

  const lastRatio = ratio?.[ratio.length-1]?.y ?? null;
  const lastPrem  = prem?.[prem.length-1]?.y ?? null;

  return {
    title: "Relative Value (Single Case)",
    metrics: {
      "EV / Price (last)": lastRatio,
      "EV − Price ($, last)": lastPrem,
      "EV above Price?": lastPrem != null ? (lastPrem > 0 ? "Yes" : "No") : "n/a",
    },
    series: [
      { name: "EV / Price", lines: [{ points: ratio }] },
      { name: "EV − Price (Premium/Discount $)", lines: [{ points: prem }] },
    ],
    notes: "Add more cases to unlock cross-sectional factor models and cointegration across cases.",
  };
}

function safe(v){ return Number.isFinite(v) && Math.abs(v) > 1e-9; }
