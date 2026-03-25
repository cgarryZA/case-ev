// analysis/efficiency.js
export async function compute(providers, settings) {
  const { ev, price } = providers.getAligned();

  const diff = (s)=>s.slice(1).map((p,i)=>({ x: p.x, y: p.y - s[i].y }));
  const dP = diff(price).map(p=>p.y);
  const dE = diff(ev).map(p=>p.y);

  // lead-lag correlation: corr(ΔPrice_t, ΔEV_{t-k})
  const maxLag = Math.min(20, Math.floor(dP.length/4));
  const arr = [];
  for (let k=-maxLag; k<=maxLag; k++){
    const A=[], B=[];
    for (let i=0;i<dP.length;i++){
      const j = i - k; // EV lead if k>0
      if (j>=0 && j<dE.length) { A.push(dP[i]); B.push(dE[j]); }
    }
    const c = corr(A,B);
    arr.push({ lag: k, value: c });
  }
  const best = arr.reduce((a,b)=> Math.abs(b.value)>Math.abs(a.value)?b:a, {lag:0,value:0});

  // simple OLS ΔPrice_t ~ α + β ΔEV_{t-bestLag}
  let A=[], B=[];
  for (let i=0;i<dP.length;i++){
    const j = i - best.lag;
    if (j>=0 && j<dE.length) { A.push(dP[i]); B.push(dE[j]); }
  }
  const { beta, alpha, r2 } = ols(B, A);

  // Build series for plot
  const leadLagSeries = arr.map((o,i)=>({ x:(i/(arr.length-1)), y:o.value }));
  const text = best.lag > 0
    ? `EV leads Price by ~${best.lag} steps`
    : best.lag < 0
      ? `Price leads EV by ~${-best.lag} steps`
      : `No lead-lag detected`;

  return {
    title: "Market Efficiency (Lead–Lag)",
    metrics: {
      "Best Lag (EV→Price)": best.lag,
      "Corr at Best Lag": best.value,
      "OLS β (ΔP ~ ΔEV)": beta,
      "OLS α": alpha,
      "R²": r2,
      "Inference": text,
    },
    series: [
      { name: "Lead–Lag Correlation (k: EV leads +k)", lines: [{ points: leadLagSeries }] },
    ],
    notes: "Positive lag means EV changes precede Price changes.",
  };
}

function corr(a,b){
  if (!a.length || a.length !== b.length) return 0;
  const m = mean(a), n = mean(b);
  const s = Math.sqrt(variance(a, m)*variance(b, n)) || 1;
  let c=0; for (let i=0;i<a.length;i++) c += (a[i]-m)*(b[i]-n);
  return c/(a.length*s);
}
function mean(a){ return a.reduce((s,v)=>s+v,0)/Math.max(1,a.length); }
function variance(a,m=mean(a)){ return mean(a.map(v=>(v-m)**2)); }
function ols(x, y){ // y = a + b x
  const mx=mean(x), my=mean(y);
  let num=0, den=0;
  for (let i=0;i<x.length;i++){ num += (x[i]-mx)*(y[i]-my); den += (x[i]-mx)**2; }
  const b = den ? num/den : 0;
  const a = my - b*mx;
  const yhat = x.map(v=>a+b*v);
  const ssr = y.reduce((s,v,i)=>s+(yhat[i]-my)**2,0);
  const sst = y.reduce((s,v)=>s+(v-my)**2,0) || 1;
  return { alpha:a, beta:b, r2:ssr/sst };
}
