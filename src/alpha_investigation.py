"""Alpha investigation: Can we find tradeable signals in CS2 case/skin data?

Tests:
1. EV change as predictor of 7/14-day forward case returns
2. Cross-sectional momentum (do winners keep winning?)
3. EV/Price ratio as cross-sectional predictor
4. Basis mean-reversion at 7+ day horizons
5. Cross-exchange arbitrage (multi-provider spreads)
6. Seasonality / day-of-week effects
7. Volatility breakout signals
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
PRICES = ROOT / "data" / "prices"

# ─── Helpers ───

def load_all_cases():
    """Load ALL timescale data for every case."""
    cases = {}
    for f in sorted(glob.glob(str(PRECOMPUTED / "*.json"))):
        with open(f) as fh:
            d = json.load(fh)
        name = d.get("case_name", os.path.basename(f).replace(".json", ""))
        ts = d.get("timescales", {}).get("ALL", {})
        if not ts:
            continue
        case_price = ts.get("case_price", [])
        ev = ts.get("ev", [])
        basis = ts.get("basis", [])
        if len(case_price) < 30 or len(ev) < 30:
            continue
        cases[name] = {
            "case_price": [p[1] for p in case_price],  # [x, y] -> y
            "ev": [p[1] for p in ev],
            "basis": [p[1] for p in basis] if basis else [],
            "x": [p[0] for p in case_price],  # normalized x
            "n": len(case_price),
        }
    return cases


def returns(series, lag=1):
    """Compute log returns with given lag."""
    out = []
    for i in range(lag, len(series)):
        if series[i - lag] > 0 and series[i] > 0:
            out.append(math.log(series[i] / series[i - lag]))
        else:
            out.append(0.0)
    return out


def forward_returns(series, horizon):
    """Forward returns: ret[i] = log(series[i+horizon] / series[i])."""
    out = []
    for i in range(len(series) - horizon):
        if series[i] > 0 and series[i + horizon] > 0:
            out.append(math.log(series[i + horizon] / series[i]))
        else:
            out.append(0.0)
    return out


def corr(x, y):
    """Pearson correlation."""
    n = min(len(x), len(y))
    if n < 5:
        return float("nan")
    x, y = x[:n], y[:n]
    mx, my = sum(x) / n, sum(y) / n
    sx = math.sqrt(sum((xi - mx) ** 2 for xi in x) / n)
    sy = math.sqrt(sum((yi - my) ** 2 for yi in y) / n)
    if sx < 1e-12 or sy < 1e-12:
        return 0.0
    cov = sum((x[i] - mx) * (y[i] - my) for i in range(n)) / n
    return cov / (sx * sy)


def rank_corr(x, y):
    """Spearman rank correlation."""
    n = min(len(x), len(y))
    if n < 5:
        return float("nan")
    rx = rank(x[:n])
    ry = rank(y[:n])
    return corr(rx, ry)


def rank(arr):
    """Rank values (1-based, average ties)."""
    indexed = sorted(enumerate(arr), key=lambda t: t[1])
    ranks = [0.0] * len(arr)
    i = 0
    while i < len(indexed):
        j = i
        while j < len(indexed) and indexed[j][1] == indexed[i][1]:
            j += 1
        avg_rank = (i + j + 1) / 2  # average rank for ties
        for k in range(i, j):
            ranks[indexed[k][0]] = avg_rank
        i = j
    return ranks


def sharpe(returns_list):
    """Annualized Sharpe assuming daily returns."""
    if len(returns_list) < 10:
        return float("nan")
    mu = statistics.mean(returns_list)
    sd = statistics.stdev(returns_list)
    if sd < 1e-12:
        return 0.0
    return (mu / sd) * math.sqrt(365)


def cumulative_returns(returns_list):
    """Cumulative sum of returns."""
    out = [0.0]
    for r in returns_list:
        out.append(out[-1] + r)
    return out


def max_drawdown(cum_returns):
    """Max drawdown from cumulative returns."""
    peak = cum_returns[0]
    mdd = 0.0
    for v in cum_returns:
        peak = max(peak, v)
        mdd = min(mdd, v - peak)
    return mdd


# ─── Test 1: EV change predicts forward case price returns ───

def test_ev_predicts_price(cases):
    print("=" * 80)
    print("TEST 1: Does EV change predict 7-day and 14-day forward case price returns?")
    print("=" * 80)
    print("  If EV rises today, does the case price follow within 7-14 days?")
    print()

    results = []
    for name, d in cases.items():
        cp = d["case_price"]
        ev = d["ev"]
        n = min(len(cp), len(ev))
        if n < 30:
            continue

        ev_ret_1d = returns(ev[:n], 1)
        ev_ret_5d = returns(ev[:n], 5)

        for horizon in [7, 14, 21]:
            fwd = forward_returns(cp[:n], horizon)
            # Align: ev_ret_1d starts at index 1, fwd ends at n-horizon
            m = min(len(ev_ret_1d), len(fwd) - 1)
            if m < 20:
                continue
            # ev_ret_1d[i] = log(ev[i+1]/ev[i]), fwd[i+1] = log(cp[i+1+h]/cp[i+1])
            c = corr(ev_ret_1d[:m], fwd[1 : m + 1])
            results.append((name, horizon, "1d_ev_ret", c, m))

        for horizon in [7, 14]:
            fwd = forward_returns(cp[:n], horizon)
            m = min(len(ev_ret_5d), len(fwd) - 5)
            if m < 20:
                continue
            c = corr(ev_ret_5d[:m], fwd[5 : m + 5])
            results.append((name, horizon, "5d_ev_ret", c, m))

    # Aggregate by (horizon, signal)
    agg = defaultdict(list)
    for name, horizon, sig, c, m in results:
        if math.isfinite(c):
            agg[(horizon, sig)].append((c, name))

    for (horizon, sig), vals in sorted(agg.items()):
        corrs = [v[0] for v in vals]
        avg_c = statistics.mean(corrs)
        pos = sum(1 for c in corrs if c > 0)
        print(f"  Signal={sig}, Horizon={horizon}d: avg_corr={avg_c:+.4f}, "
              f"positive in {pos}/{len(corrs)} cases")

        # Show top 5 strongest
        vals.sort(key=lambda v: abs(v[0]), reverse=True)
        for c, name in vals[:5]:
            print(f"    {name:<35} corr={c:+.4f}")
        print()


# ─── Test 2: Cross-sectional momentum ───

def test_momentum(cases):
    print("=" * 80)
    print("TEST 2: Cross-sectional momentum — do past winners keep winning?")
    print("=" * 80)
    print("  Sort cases by past N-day return, go long top quintile, short bottom.")
    print()

    # Build aligned daily return matrix
    all_names = list(cases.keys())
    all_rets = {}
    min_len = min(len(cases[n]["case_price"]) for n in all_names)

    for name in all_names:
        cp = cases[name]["case_price"][:min_len]
        all_rets[name] = returns(cp, 1)

    n_days = len(all_rets[all_names[0]])

    for lookback, hold in [(7, 7), (14, 7), (30, 14), (60, 30)]:
        strategy_rets = []
        for t in range(lookback, n_days - hold, hold):
            # Rank by past lookback return
            past = {}
            for name in all_names:
                r = sum(all_rets[name][t - lookback : t])
                past[name] = r

            sorted_names = sorted(past, key=lambda n: past[n])
            q = max(1, len(sorted_names) // 5)
            longs = sorted_names[-q:]  # top quintile (winners)
            shorts = sorted_names[:q]  # bottom quintile (losers)

            # Forward return over hold period
            long_ret = statistics.mean(
                sum(all_rets[n][t : t + hold]) for n in longs
            )
            short_ret = statistics.mean(
                sum(all_rets[n][t : t + hold]) for n in shorts
            )
            strategy_rets.append(long_ret - short_ret)

        if len(strategy_rets) < 5:
            continue
        sr = sharpe(strategy_rets)
        cum = cumulative_returns(strategy_rets)
        mdd = max_drawdown(cum)
        total = cum[-1]
        win_rate = sum(1 for r in strategy_rets if r > 0) / len(strategy_rets)
        print(f"  Lookback={lookback}d, Hold={hold}d: "
              f"Sharpe={sr:.2f}, Total={total:+.1%}, "
              f"MaxDD={mdd:.1%}, WinRate={win_rate:.1%}, "
              f"N_trades={len(strategy_rets)}")


# ─── Test 3: EV/Price ratio as cross-sectional signal ───

def test_ev_price_signal(cases):
    print()
    print("=" * 80)
    print("TEST 3: EV/Price ratio — do undervalued cases (high EV/P) outperform?")
    print("=" * 80)
    print("  Sort by EV/Price, long top quintile (high EV/P), short bottom.")
    print()

    all_names = list(cases.keys())
    min_len = min(len(cases[n]["case_price"]) for n in all_names)

    for hold in [7, 14, 30]:
        strategy_rets = []
        for t in range(0, min_len - hold, hold):
            ev_p = {}
            for name in all_names:
                cp = cases[name]["case_price"][t]
                ev = cases[name]["ev"][t]
                if cp > 0:
                    ev_p[name] = ev / cp
                else:
                    ev_p[name] = 0

            sorted_names = sorted(ev_p, key=lambda n: ev_p[n])
            q = max(1, len(sorted_names) // 5)
            longs = sorted_names[-q:]  # high EV/P (undervalued)
            shorts = sorted_names[:q]  # low EV/P (overvalued)

            # Forward return
            long_ret = statistics.mean(
                math.log(cases[n]["case_price"][t + hold] / cases[n]["case_price"][t])
                if cases[n]["case_price"][t] > 0 and cases[n]["case_price"][t + hold] > 0
                else 0
                for n in longs
            )
            short_ret = statistics.mean(
                math.log(cases[n]["case_price"][t + hold] / cases[n]["case_price"][t])
                if cases[n]["case_price"][t] > 0 and cases[n]["case_price"][t + hold] > 0
                else 0
                for n in shorts
            )
            strategy_rets.append(long_ret - short_ret)

        if len(strategy_rets) < 5:
            continue
        sr = sharpe(strategy_rets)
        cum = cumulative_returns(strategy_rets)
        mdd = max_drawdown(cum)
        total = cum[-1]
        win_rate = sum(1 for r in strategy_rets if r > 0) / len(strategy_rets)
        print(f"  Hold={hold}d: Sharpe={sr:.2f}, Total={total:+.1%}, "
              f"MaxDD={mdd:.1%}, WinRate={win_rate:.1%}, "
              f"N_trades={len(strategy_rets)}")


# ─── Test 4: Basis mean-reversion at weekly+ horizons ───

def test_basis_mr(cases):
    print()
    print("=" * 80)
    print("TEST 4: Basis mean-reversion — does extreme basis predict reversion?")
    print("=" * 80)
    print("  Buy case when basis z-score < -2, sell when > +2. Hold for 7/14/21 days.")
    print()

    for name, d in sorted(cases.items()):
        basis = d["basis"]
        cp = d["case_price"]
        if len(basis) < 60 or len(cp) < 60:
            continue

        # Rolling z-score of basis (30-day window)
        win = 30
        z_scores = []
        for i in range(win, len(basis)):
            window = basis[i - win : i]
            mu = statistics.mean(window)
            sd = statistics.stdev(window) if len(window) > 1 else 1
            if sd < 1e-9:
                sd = 1
            z_scores.append((basis[i] - mu) / sd)

        for horizon in [7, 14, 21]:
            buy_rets = []
            sell_rets = []
            for i, z in enumerate(z_scores):
                t = i + win
                if t + horizon >= len(cp):
                    break
                fwd = math.log(cp[t + horizon] / cp[t]) if cp[t] > 0 and cp[t + horizon] > 0 else 0
                if z < -2:
                    buy_rets.append(fwd)
                elif z > 2:
                    sell_rets.append(fwd)

            if len(buy_rets) >= 5 or len(sell_rets) >= 5:
                buy_avg = statistics.mean(buy_rets) if buy_rets else 0
                sell_avg = statistics.mean(sell_rets) if sell_rets else 0
                print(f"  {name:<32} H={horizon}d: "
                      f"BUY(z<-2) n={len(buy_rets):>3} avg={buy_avg:+.4f} | "
                      f"SELL(z>+2) n={len(sell_rets):>3} avg={sell_avg:+.4f}")


# ─── Test 5: Cross-exchange arbitrage ───

def test_cross_exchange(cases):
    print()
    print("=" * 80)
    print("TEST 5: Cross-exchange spread — price dispersion across providers")
    print("=" * 80)
    print("  How wide are provider spreads? Is there persistent arbitrage?")
    print()

    import csv
    case_dirs = list((PRICES / "cases").iterdir()) if (PRICES / "cases").exists() else []

    spreads = []
    for d in sorted(case_dirs)[:10]:
        csvs = list(d.glob("*.csv"))
        if not csvs:
            continue
        # Read the CSV and compute per-date spread
        rows_by_date = defaultdict(list)
        for csvf in csvs:
            with open(csvf) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    date = row.get("date", "")
                    try:
                        price = float(row.get("price_usd", 0))
                    except (ValueError, TypeError):
                        continue
                    if price > 0:
                        rows_by_date[date].append(price)

        if not rows_by_date:
            continue

        daily_spreads = []
        for date, prices in sorted(rows_by_date.items())[-90:]:
            if len(prices) >= 3:
                lo, hi = min(prices), max(prices)
                mid = statistics.median(prices)
                if mid > 0:
                    daily_spreads.append((hi - lo) / mid)

        if daily_spreads:
            avg_spread = statistics.mean(daily_spreads)
            max_spread = max(daily_spreads)
            name = d.name
            spreads.append((name, avg_spread, max_spread, len(daily_spreads)))
            print(f"  {name:<40} avg_spread={avg_spread:.1%} "
                  f"max_spread={max_spread:.1%} (n={len(daily_spreads)}d)")

    if not spreads:
        print("  No multi-provider CSV data found for cases.")


# ─── Test 6: Day-of-week / seasonality ───

def test_seasonality(cases):
    print()
    print("=" * 80)
    print("TEST 6: Seasonality — are certain days/periods systematically better?")
    print("=" * 80)
    print()

    # Pool all case returns by day-of-week proxy
    # Since we only have normalized x, we'll use the raw price CSVs for actual dates
    import csv
    from datetime import datetime

    dow_returns = defaultdict(list)  # 0=Mon ... 6=Sun
    month_returns = defaultdict(list)

    case_dirs = list((PRICES / "cases").iterdir()) if (PRICES / "cases").exists() else []
    for d in sorted(case_dirs):
        csvs = list(d.glob("*.csv"))
        if not csvs:
            continue

        # Collect median price per date
        date_prices = defaultdict(list)
        for csvf in csvs:
            with open(csvf) as f:
                reader = csv.DictReader(f)
                for row in reader:
                    try:
                        price = float(row.get("price_usd", 0))
                        date = row["date"]
                    except (ValueError, TypeError, KeyError):
                        continue
                    if price > 0:
                        date_prices[date].append(price)

        # Compute daily median series
        sorted_dates = sorted(date_prices.keys())
        daily = [(dt, statistics.median(date_prices[dt])) for dt in sorted_dates
                 if len(date_prices[dt]) >= 2]

        for i in range(1, len(daily)):
            dt_str, price = daily[i]
            _, prev_price = daily[i - 1]
            if prev_price > 0 and price > 0:
                ret = math.log(price / prev_price)
                try:
                    dt = datetime.strptime(dt_str, "%Y-%m-%d")
                    dow_returns[dt.weekday()].append(ret)
                    month_returns[dt.month].append(ret)
                except ValueError:
                    pass

    dow_names = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"]
    print("  Day-of-week average returns (pooled across all cases):")
    for dow in range(7):
        rets = dow_returns[dow]
        if rets:
            avg = statistics.mean(rets)
            sd = statistics.stdev(rets) if len(rets) > 1 else 0
            t_stat = (avg / (sd / math.sqrt(len(rets)))) if sd > 0 else 0
            print(f"    {dow_names[dow]}: avg={avg:+.5f} sd={sd:.5f} "
                  f"t={t_stat:+.2f} n={len(rets)}")

    print()
    print("  Monthly average returns:")
    month_names = ["", "Jan", "Feb", "Mar", "Apr", "May", "Jun",
                   "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
    for m in range(1, 13):
        rets = month_returns[m]
        if rets:
            avg = statistics.mean(rets)
            sd = statistics.stdev(rets) if len(rets) > 1 else 0
            t_stat = (avg / (sd / math.sqrt(len(rets)))) if sd > 0 else 0
            print(f"    {month_names[m]}: avg={avg:+.5f} sd={sd:.5f} "
                  f"t={t_stat:+.2f} n={len(rets)}")


# ─── Test 7: Volatility breakout ───

def test_vol_breakout(cases):
    print()
    print("=" * 80)
    print("TEST 7: Volatility breakout — does high vol predict direction?")
    print("=" * 80)
    print("  When rolling vol spikes above 2x its mean, what happens next?")
    print()

    for name, d in sorted(cases.items()):
        cp = d["case_price"]
        if len(cp) < 60:
            continue
        rets = returns(cp, 1)
        if len(rets) < 50:
            continue

        # Rolling 10-day vol
        win = 10
        vols = []
        for i in range(win, len(rets)):
            window = rets[i - win : i]
            vols.append(statistics.stdev(window) if len(window) > 1 else 0)

        if not vols:
            continue
        mean_vol = statistics.mean(vols)
        if mean_vol < 1e-9:
            continue

        # When vol > 2x mean, what's the 7-day forward return?
        fwd_after_spike = []
        fwd_after_calm = []
        for i, v in enumerate(vols):
            t = i + win
            if t + 7 >= len(cp):
                break
            fwd = math.log(cp[t + 7] / cp[t]) if cp[t] > 0 and cp[t + 7] > 0 else 0
            if v > 2 * mean_vol:
                fwd_after_spike.append(fwd)
            elif v < 0.5 * mean_vol:
                fwd_after_calm.append(fwd)

        if len(fwd_after_spike) >= 5:
            spike_avg = statistics.mean(fwd_after_spike)
            calm_avg = statistics.mean(fwd_after_calm) if fwd_after_calm else 0
            print(f"  {name:<32} "
                  f"After vol spike: avg_7d={spike_avg:+.4f} (n={len(fwd_after_spike)}) | "
                  f"After calm: avg_7d={calm_avg:+.4f} (n={len(fwd_after_calm)})")


# ─── Test 8: Combined signal backtest ───

def test_combined_signal(cases):
    print()
    print("=" * 80)
    print("TEST 8: Combined signal backtest")
    print("=" * 80)
    print("  Long cases where: EV/P > 1.5 AND basis z < -1 AND 14d momentum > 0")
    print("  Hold 14 days. Account for 5% round-trip fees (Buff163).")
    print()

    FEE = 0.05
    all_names = list(cases.keys())
    min_len = min(len(cases[n]["case_price"]) for n in all_names)

    hold = 14
    win = 30
    trades = []

    for t in range(max(win, 14), min_len - hold, hold):
        selected = []
        for name in all_names:
            cp = cases[name]["case_price"]
            ev = cases[name]["ev"]
            basis = cases[name]["basis"]

            if t >= len(cp) or t >= len(ev) or len(basis) < t:
                continue

            # EV/P ratio
            ev_p = ev[t] / cp[t] if cp[t] > 0 else 0

            # Basis z-score
            b_window = basis[max(0, t - win) : t]
            if len(b_window) < 10:
                continue
            b_mu = statistics.mean(b_window)
            b_sd = statistics.stdev(b_window) if len(b_window) > 1 else 1
            if b_sd < 1e-9:
                b_sd = 1
            z = (basis[t] - b_mu) / b_sd

            # 14d momentum
            if t >= 14 and cp[t - 14] > 0 and cp[t] > 0:
                mom = math.log(cp[t] / cp[t - 14])
            else:
                mom = 0

            if ev_p > 1.5 and z < -1 and mom > 0:
                selected.append(name)

        if not selected:
            continue

        # Equal-weight the selected cases
        period_rets = []
        for name in selected:
            cp = cases[name]["case_price"]
            if cp[t] > 0 and cp[t + hold] > 0:
                gross = math.log(cp[t + hold] / cp[t])
                net = gross - FEE  # deduct fees
                period_rets.append(net)

        if period_rets:
            avg_ret = statistics.mean(period_rets)
            trades.append({
                "t": t,
                "n_cases": len(selected),
                "cases": selected,
                "gross": statistics.mean(period_rets) + FEE,
                "net": avg_ret,
            })

    if not trades:
        print("  No trades triggered.")
        return

    net_rets = [t["net"] for t in trades]
    gross_rets = [t["gross"] for t in trades]
    cum = cumulative_returns(net_rets)
    mdd = max_drawdown(cum)

    print(f"  Total trades: {len(trades)}")
    print(f"  Avg cases per trade: {statistics.mean(t['n_cases'] for t in trades):.1f}")
    print(f"  Gross Sharpe: {sharpe(gross_rets):.2f}")
    print(f"  Net Sharpe (after 5% fees): {sharpe(net_rets):.2f}")
    print(f"  Total return (net): {cum[-1]:+.1%}")
    print(f"  Max drawdown: {mdd:.1%}")
    print(f"  Win rate: {sum(1 for r in net_rets if r > 0) / len(net_rets):.1%}")
    print(f"  Avg gross return per trade: {statistics.mean(gross_rets):+.2%}")
    print(f"  Avg net return per trade: {statistics.mean(net_rets):+.2%}")

    print()
    print("  Last 10 trades:")
    for t in trades[-10:]:
        print(f"    t={t['t']:>4} n={t['n_cases']} gross={t['gross']:+.2%} "
              f"net={t['net']:+.2%} cases={t['cases'][:3]}{'...' if len(t['cases'])>3 else ''}")


# ─── Run all ───

def main():
    print("Loading precomputed data...")
    cases = load_all_cases()
    print(f"Loaded {len(cases)} cases with sufficient data.\n")

    test_ev_predicts_price(cases)
    print()
    test_momentum(cases)
    test_ev_price_signal(cases)
    test_basis_mr(cases)
    test_cross_exchange(cases)
    test_seasonality(cases)
    test_vol_breakout(cases)
    test_combined_signal(cases)

    print()
    print("=" * 80)
    print("INVESTIGATION COMPLETE")
    print("=" * 80)


if __name__ == "__main__":
    main()
