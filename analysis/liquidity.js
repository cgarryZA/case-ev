// analysis/liquidity.js
export async function compute(providers, settings) {
  const { price } = providers.getAligned();
  // Without volume/order-flow we can only proxy "illiquidity" using absolute returns.
  const absRet = price.slice(1).map((p,i)=>({ x:p.x, y: Math.abs(Math.log(p.y) - Math.log(price[i].y)) }));
  const avgAbs = absRet.reduce((s,p)=>s+p.y,0)/Math.max(1,absRet.length);

  return {
    title: "Liquidity Proxies",
    metrics: {
      "Avg |Δlog Price|": avgAbs,
      "Note": "Amihud/Kyle need volume data — wire in market volumes to unlock true microstructure metrics.",
    },
    series: [
      { name: "|Δlog Price| over time (proxy illiquidity)", lines: [{ points: absRet }] },
    ],
  };
}
