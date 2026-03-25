# Case EV vs Case Price Dynamics

Investigation into how CS2 case prices and the expected value (EV) of their contents evolve over time. Tests whether case prices lead or lag the EV of their contents — an analogue to spot-forward basis or NAV deviations in traditional markets.

## Quick Start

```bash
python -m http.server 8000
# Open http://localhost:8000
```

The dashboard works immediately — all analysis is precomputed into `data/precomputed/`.

## Regenerating Precomputed Data

If you have access to the raw price CSVs:

```bash
# Download raw price data (requires Google Drive access)
pip install gdown
python setup_data.py

# Regenerate all 42 case analysis JSONs
python src/precompute.py
```

## What It Does

For each of the 42 CS2 cases:

1. Computes daily **Expected Value** of case contents using Valve unboxing odds, wear probabilities, and multi-provider median prices
2. Compares EV to the case market price across 6 timescales (1W to ALL)
3. Runs 10 quantitative analysis modules:
   - Core Statistics (correlation, return distributions)
   - Efficiency (lead-lag / Granger-like analysis)
   - Volatility (rolling vol, mean-reversion half-life)
   - Cross-Section (EV/Price ratio over time)
   - Hurst Exponent (trending vs mean-reverting)
   - Autocorrelation structure
   - Cointegration (ADF test, error-correction)
   - Regime Detection
   - Trading Signals (z-score bands)
   - Liquidity (absolute return proxy)

## Key Findings

- **All 42 cases trend** (Hurst > 0.6) — no mean-reversion in the EV-price basis
- **Price leads EV in 27/42 cases** — speculative demand drives case prices first
- **EV/Price ratio is the strongest cross-sectional signal** — cases with high EV relative to price outperform
- **5% round-trip fees destroy most short-horizon strategies** — this is a momentum market, not a stat-arb market

## Data Sources

- Price data: [PriceEmpire API](https://pricempire.com/) aggregating 70+ marketplaces
- Case contents: CS2 game files and community databases
- Unboxing odds: Valve official probabilities

## Author

Christian Garry — CS2 Quant Research Series
