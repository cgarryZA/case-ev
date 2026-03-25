// analysis/volatility.js
export async function compute(providers, settings) {
  const { ev, price, spread } = providers.getAligned();
  const logRet = (s)=>s.slice(1).map((p,i)=>Math.log(Math.max(1e-9,p.y)) - Math.log(Math.max(1e-9,s[i].y)));
  const rP = logRet(price), rE = logRet(ev);

  const W = Math.max(10, Math.round(rP.length*0.2));
  const roll = (a)=> {
    const out=[]; for (let i=W;i<=a.length;i++){
      const seg=a.slice(i-W,i); out.push({ x: ev[i]?.x ?? i/a.length, y: stdev(seg)*Math.sqrt(252) });
    } return out;
  };

  const vP = roll(rP);
  const vE = roll(rE);

  // Spread mean-reversion half-life via AR(1): s_t = φ s_{t-1} + ε
  const s = spread.map(p=>p.y);
  const phi = ar1_phi(s);
  const halflife = phi < 1 ? Math.log(0.5) / Math.log(Math.max(1e-9, phi)) : Infinity;

  return {
    title: "Volatility & Mean Reversion",
    metrics: {
      "Annualized Vol (Price, last)": vLast(vP),
      "Annualized Vol (EV, last)": vLast(vE),
      "AR(1) φ (spread)": phi,
      "Spread Half-life (steps)": halflife,
    },
    series: [
      { name: "Rolling Ann. Vol — Price", lines: [{ points: vP, color: "#ef4444" }] },
      { name: "Rolling Ann. Vol — EV",    lines: [{ points: vE, color: "#38bdf8" }] },
    ],
    notes: "Half-life computed from AR(1) on EV−Price spread.",
  };
}

function stdev(a){ const m=a.reduce((s,v)=>s+v,0)/Math.max(1,a.length); return Math.sqrt(a.reduce((s,v)=>s+(v-m)**2,0)/Math.max(1,a.length)); }
function vLast(v){ const y = v?.[v.length-1]?.y; return Number.isFinite(y) ? y : null; }
function ar1_phi(x){
  if (x.length < 3) return 0;
  const y = x.slice(1), z = x.slice(0,-1);
  const mx = mean(z), my = mean(y);
  let num=0, den=0;
  for (let i=0;i<z.length;i++){ num += (z[i]-mx)*(y[i]-my); den += (z[i]-mx)**2; }
  return den ? num/den : 0;
}
function mean(a){ return a.reduce((s,v)=>s+v,0)/Math.max(1,a.length); }
