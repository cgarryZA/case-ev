"""Market-neutral case rotation strategy.

You hold a fixed portfolio of ~100 cases across N different case types.
Each day, rank cases by some signal.
Rotate: sell worst-ranked cases you hold, buy best-ranked cases you don't.
This is MARKET NEUTRAL — always holding ~100 cases, just changing the mix.

The returns come from RELATIVE performance (picking better cases),
not from the overall market going up.

Tests multiple signals:
1. Basis z-score (buy cheap-basis, sell rich-basis)
2. EV/Price ratio (buy high EV/P, sell low)
3. Short-term reversal (buy recent losers, sell recent winners)
4. EV momentum (buy cases where EV is rising fastest)
5. Combined signal
"""

import json
import glob
import os
import math
import statistics
from collections import defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PRECOMPUTED = ROOT / "data" / "precomputed"


def load_cases():
    cases = {}
    for f in sorted(glob.glob(str(PRECOMPUTED / "*.json"))):
        with open(f) as fh:
            d = json.load(fh)
        name = d.get("case_name", os.path.basename(f).replace(".json", ""))
        ts = d.get("timescales", {}).get("ALL", {})
        if not ts:
            continue
        cp = [p[1] for p in ts.get("case_price", [])]
        ev = [p[1] for p in ts.get("ev", [])]
        basis = [p[1] for p in ts.get("basis", [])]
        if len(cp) < 60 or len(ev) < 60:
            continue
        cases[name] = {"price": cp, "ev": ev, "basis": basis}
    return cases


def align_length(cases):
    """Ensure all series are the same length."""
    min_len = min(len(d["price"]) for d in cases.values())
    for d in cases.values():
        d["price"] = d["price"][:min_len]
        d["ev"] = d["ev"][:min_len]
        d["basis"] = d["basis"][:min_len]
    return min_len


def daily_returns(prices):
    """Simple returns (not log) for portfolio math."""
    return [0.0] + [(prices[i] / prices[i-1] - 1) if prices[i-1] > 0 else 0
                     for i in range(1, len(prices))]


def equal_weight_benchmark(cases, n_days):
    """Equal-weight all cases — this is the 'market' return."""
    all_rets = {name: daily_returns(d["price"]) for name, d in cases.items()}
    bench = []
    for t in range(n_days):
        day_rets = [all_rets[n][t] for n in cases]
        bench.append(statistics.mean(day_rets))
    return bench


def rolling_z(series, window=30):
    """Rolling z-score."""
    out = [0.0] * len(series)
    for i in range(window, len(series)):
        w = series[i-window:i]
        mu = statistics.mean(w)
        sd = statistics.stdev(w) if len(w) > 1 else 1
        if sd < 1e-9:
            sd = 1
        out[i] = (series[i] - mu) / sd
    return out


def run_rotation(cases, n_days, signal_fn, rebalance_every=7,
                 hold_period=7, top_n=8, bottom_n=8, label=""):
    """
    Market-neutral rotation:
    - Every `rebalance_every` days, compute signal for each case
    - Go long top_n cases, short bottom_n cases (equal weight)
    - Hold for hold_period days (respecting 7-day constraint)
    - Return daily L/S spread returns
    """
    names = list(cases.keys())
    all_rets = {n: daily_returns(cases[n]["price"]) for n in names}
    bench = equal_weight_benchmark(cases, n_days)

    ls_returns = []  # long-short daily returns
    active_returns = []  # active vs benchmark
    long_names_log = []

    warmup = 30  # need history for signals

    for t in range(warmup, n_days):
        if (t - warmup) % rebalance_every != 0:
            # Not a rebalance day — use previous positions
            if ls_returns:
                # Carry forward last allocation's return
                pass
            continue

        # Compute signal for each case at time t
        signals = {}
        for name in names:
            sig = signal_fn(cases[name], t)
            if sig is not None and math.isfinite(sig):
                signals[name] = sig

        if len(signals) < top_n + bottom_n:
            continue

        ranked = sorted(signals, key=lambda n: signals[n])
        longs = ranked[-top_n:]   # highest signal = long
        shorts = ranked[:bottom_n]  # lowest signal = short

        # Compute returns over the hold period
        period_rets_long = []
        period_rets_short = []
        period_rets_bench = []

        for d in range(min(hold_period, n_days - t)):
            day = t + d
            if day >= n_days:
                break
            l_ret = statistics.mean(all_rets[n][day] for n in longs)
            s_ret = statistics.mean(all_rets[n][day] for n in shorts)
            b_ret = bench[day]
            ls_returns.append(l_ret - s_ret)
            active_returns.append(
                (l_ret + s_ret) / 2 - b_ret  # active return vs equal-weight
            )

        long_names_log.append((t, longs, shorts))

    return ls_returns, active_returns, long_names_log


def evaluate(returns_list, label, fee_per_rebalance=0.0, rebal_freq=7):
    """Print strategy statistics."""
    if len(returns_list) < 10:
        print(f"  {label}: insufficient data ({len(returns_list)} obs)")
        return

    # Deduct fees: spread across rebalance period
    daily_fee = fee_per_rebalance / rebal_freq
    net = [r - daily_fee for r in returns_list]

    cum_gross = [0.0]
    cum_net = [0.0]
    for i, r in enumerate(returns_list):
        cum_gross.append(cum_gross[-1] + r)
        cum_net.append(cum_net[-1] + net[i])

    mu = statistics.mean(returns_list)
    sd = statistics.stdev(returns_list)
    sr = (mu / sd * math.sqrt(365)) if sd > 0 else 0

    mu_net = statistics.mean(net)
    sd_net = statistics.stdev(net)
    sr_net = (mu_net / sd_net * math.sqrt(365)) if sd_net > 0 else 0

    # Max drawdown
    peak = cum_net[0]
    mdd = 0
    for v in cum_net:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)

    # Win rate
    wr = sum(1 for r in net if r > 0) / len(net)

    # Hit ratio of rebalance periods
    period_rets = []
    for i in range(0, len(net), 7):
        chunk = net[i:i+7]
        if chunk:
            period_rets.append(sum(chunk))
    period_wr = sum(1 for r in period_rets if r > 0) / len(period_rets) if period_rets else 0

    # Calmar ratio
    calmar = (mu_net * 365 / abs(mdd)) if mdd != 0 else 0

    print(f"  {label}")
    print(f"    Gross:  Sharpe={sr:.2f}  Total={cum_gross[-1]:+.1%}  "
          f"AvgDaily={mu:+.4%}")
    print(f"    Net(5% RT fee): Sharpe={sr_net:.2f}  Total={cum_net[-1]:+.1%}  "
          f"AvgDaily={mu_net:+.4%}")
    print(f"    MaxDD={mdd:.1%}  Calmar={calmar:.2f}  "
          f"DailyWin={wr:.1%}  WeeklyWin={period_wr:.1%}")
    print(f"    Observations={len(returns_list)} days")
    print()


# ─── Signal Functions ───

def signal_basis_z(data, t):
    """Buy negative z-score (basis cheap), sell positive (basis rich)."""
    basis = data["basis"]
    if t < 30 or t >= len(basis):
        return None
    w = basis[t-30:t]
    mu = statistics.mean(w)
    sd = statistics.stdev(w) if len(w) > 1 else 1
    if sd < 1e-9:
        return None
    z = (basis[t] - mu) / sd
    return -z  # negative z = cheap = BUY signal (high rank)


def signal_ev_price(data, t):
    """Buy high EV/Price, sell low EV/Price."""
    if t >= len(data["ev"]) or t >= len(data["price"]):
        return None
    p = data["price"][t]
    if p <= 0:
        return None
    return data["ev"][t] / p


def signal_reversal_7d(data, t):
    """Buy recent losers (7-day), sell recent winners."""
    prices = data["price"]
    if t < 7 or t >= len(prices) or prices[t-7] <= 0 or prices[t] <= 0:
        return None
    ret = math.log(prices[t] / prices[t-7])
    return -ret  # negative = reversal (buy losers)


def signal_reversal_3d(data, t):
    """Buy recent losers (3-day), sell recent winners."""
    prices = data["price"]
    if t < 3 or t >= len(prices) or prices[t-3] <= 0 or prices[t] <= 0:
        return None
    ret = math.log(prices[t] / prices[t-3])
    return -ret


def signal_ev_momentum(data, t):
    """Buy cases where EV is rising fastest (7d EV return)."""
    ev = data["ev"]
    if t < 7 or t >= len(ev) or ev[t-7] <= 0 or ev[t] <= 0:
        return None
    return math.log(ev[t] / ev[t-7])


def signal_momentum_14d(data, t):
    """Buy recent winners (14-day momentum)."""
    prices = data["price"]
    if t < 14 or t >= len(prices) or prices[t-14] <= 0 or prices[t] <= 0:
        return None
    return math.log(prices[t] / prices[t-14])


def signal_combined(data, t):
    """Combine: basis_z + ev/price + 3d reversal + ev_momentum."""
    bz = signal_basis_z(data, t)
    ep = signal_ev_price(data, t)
    rev = signal_reversal_3d(data, t)
    evm = signal_ev_momentum(data, t)

    if any(v is None for v in [bz, ep, rev, evm]):
        return None

    # Z-score each signal across time (approximate with simple scaling)
    # Just use rank-like combination: normalize each to similar scale
    return bz * 0.3 + ep * 0.2 + rev * 0.3 + evm * 0.2


def signal_demeaned_basis_z(data, t):
    """Basis z-score, but relative to cross-sectional median (removes market drift)."""
    return signal_basis_z(data, t)


# ─── Main ───

def main():
    print("Loading data...")
    cases = load_cases()
    n_days = align_length(cases)
    print(f"Loaded {len(cases)} cases, {n_days} daily observations each.\n")

    bench = equal_weight_benchmark(cases, n_days)
    bench_cum = sum(bench)
    print(f"Equal-weight benchmark total return: {bench_cum:+.1%}")
    print(f"  (This is the market drift you need to remove)\n")
    print("=" * 80)
    print("MARKET-NEUTRAL STRATEGIES (Long/Short, 8 cases each side)")
    print("  Rebalance every 7 days, 7-day hold, 5% round-trip fees")
    print("=" * 80)
    print()

    signals = [
        ("Basis Z-score (buy cheap, sell rich)", signal_basis_z),
        ("EV/Price ratio (buy high, sell low)", signal_ev_price),
        ("3-day reversal (buy losers, sell winners)", signal_reversal_3d),
        ("7-day reversal (buy losers, sell winners)", signal_reversal_7d),
        ("14-day momentum (buy winners, sell losers)", signal_momentum_14d),
        ("EV momentum (buy rising EV, sell falling)", signal_ev_momentum),
        ("Combined (basis_z + EV/P + 3d_rev + EV_mom)", signal_combined),
    ]

    for label, sig_fn in signals:
        ls, active, log = run_rotation(
            cases, n_days, sig_fn,
            rebalance_every=7, hold_period=7,
            top_n=8, bottom_n=8, label=label
        )
        evaluate(ls, label, fee_per_rebalance=0.05, rebal_freq=7)

    # Also test with different portfolio sizes
    print("=" * 80)
    print("SENSITIVITY: Varying portfolio size (Basis Z-score signal)")
    print("=" * 80)
    print()
    for top_n in [4, 8, 12, 16]:
        ls, active, log = run_rotation(
            cases, n_days, signal_basis_z,
            rebalance_every=7, hold_period=7,
            top_n=top_n, bottom_n=top_n,
        )
        evaluate(ls, f"Top/Bottom {top_n}", fee_per_rebalance=0.05)

    # Test different rebalance frequencies
    print("=" * 80)
    print("SENSITIVITY: Varying rebalance frequency (Basis Z-score, top/bottom 8)")
    print("=" * 80)
    print()
    for freq in [1, 3, 7, 14, 30]:
        ls, active, log = run_rotation(
            cases, n_days, signal_basis_z,
            rebalance_every=freq, hold_period=max(freq, 7),
            top_n=8, bottom_n=8,
        )
        evaluate(ls, f"Rebal every {freq}d, hold {max(freq,7)}d",
                 fee_per_rebalance=0.05, rebal_freq=max(freq, 7))

    # Show what the combined signal is actually picking
    print("=" * 80)
    print("RECENT COMBINED SIGNAL PICKS")
    print("=" * 80)
    ls, active, log = run_rotation(
        cases, n_days, signal_combined,
        rebalance_every=7, hold_period=7,
        top_n=8, bottom_n=8,
    )
    for t, longs, shorts in log[-5:]:
        print(f"\n  Day {t}:")
        print(f"    LONG:  {', '.join(longs)}")
        print(f"    SHORT: {', '.join(shorts)}")

    print()
    print("=" * 80)
    print("INVESTIGATION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
