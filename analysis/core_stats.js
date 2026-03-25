// analysis/core_stats.js
export async function compute(providers, settings) {
  const { ev, price, spread } = providers.getAligned();

  const ret = (s) => s.slice(1).map((p,i) => Math.log(Math.max(1e-9, p.y)) - Math.log(Math.max(1e-9, s[i].y)));
  const rP = ret(price), rE = ret(ev);

  const mean = (a)=>a.reduce((s,v)=>s+v,0)/Math.max(1,a.length);
  const std  = (a)=>Math.sqrt(mean(a.map(v=>(v-mean(a))**2)));
  const cov  = (a,b)=>mean(a.map((v,i)=>(v-mean(a))*(b[i]-mean(b))));
  const corr = cov(rP, rE) / (std(rP)*std(rE) || 1);

  // Rolling correlation (window ~ 20 points)
  const W = Math.max(10, Math.round(rP.length * 0.2));
  const roll = [];
  for (let i=W;i<=rP.length;i++){
    const a=rP.slice(i-W,i), b=rE.slice(i-W,i);
    const c = (cov(a,b) / ((std(a)*std(b)) || 1)) || 0;
    roll.push({ x: ev[i]?.x ?? i/(rP.length+1), y: c });
  }

  // Spread z-score (using spread level)
  const spY = spread.map(p=>p.y);
  const m = mean(spY), s = std(spY) || 1;
  const z = spread.map((p)=>({ x:p.x, y:(p.y-m)/s }));

  return {
    title: "Core Statistics",
    metrics: {
      "Corr(Price, EV)": corr,
      "μ Price ret": mean(rP),
      "σ Price ret": std(rP),
      "μ EV ret": mean(rE),
      "σ EV ret": std(rE),
      "Spread μ": m,
      "Spread σ": s,
    },
    series: [
      { name: "Rolling Corr (Price vs EV)", lines: [{ points: roll }] },
      { name: "Spread Z-Score (EV − Price)", lines: [{ points: z }] },
    ],
  };
}
