# base-arb-scanner

A read-only multi-DEX arbitrage scanner for Base L2. It watches prices across **Uniswap V3**, **Aerodrome**, **BaseSwap**, and **SushiSwap V3**, and reports cross-DEX spreads — nothing more. It holds no keys, signs nothing, and sends no transactions.

This is a **spread-observability / research tool**, not a money printer.

## Honest results

A dry-run of this scanner on Nov 18, 2026 — 12 pairs across 4 DEXs at block 59,277,999 — found **0 opportunities above 15 bps (0.15% after gas)**. Plain two-pool arbitrage on Base is a solved, crowded game: the spreads a solo scanner can see are real, but they are almost always below the threshold where gas and slippage leave anything behind. What this repo is good for:

- watching how spreads move over time (the SQLite log + dashboard),
- learning how multi-DEX quoting works on Base (QuoterV2, Solidly-style routers, V2 `getAmountsOut`),
- using spread data as an input to other research (volatility, liquidity, new-pool discovery).

If you came here expecting passive income, this repo will disappoint you honestly rather than expensively.

## What is in here

| File | Purpose |
|------|---------|
| `scanner.py` | Price quotes + spread detection across 4 DEXs. One-off scan or `--loop`. |
| `event_monitor.py` | Watches contracts for Transfer / governance / vesting / presale / reward events. Detect-only by default. |
| `config.py` | DEX routers, token addresses, pairs, fee tiers, view-only ABIs. Env-based. |
| `config/events.json` | Example watchlist for `event_monitor.py` (all `auto_execute: false`). |
| `dashboard/` | Flask dashboard + SQLite layer for spread history (`arb_db.py`, `dashboard.py`). |
| `run-dryrun.sh` | One-off scan. |
| `run-scanner.sh` | Continuous monitoring loop. |

What is **deliberately not** in here: the executor, flash-loan contracts, mempool/sandwich code, the compounder, and any wallet or private-key handling. Those were part of the original bot and are out of scope for this scanner-only repo.

## Quick start

```bash
pip install -r requirements.txt

# One-off scan (uses the public Base RPC, no key needed)
./run-dryrun.sh
# or: python3 scanner.py

# Continuous monitoring, 30s interval
./run-scanner.sh
# or: python3 scanner.py --loop --interval 30
```

### Configuration

```bash
cp .env.example .env
# edit .env — BASE_RPC_URL defaults to https://mainnet.base.org
```

| Variable | Default | What it does |
|----------|---------|--------------|
| `BASE_RPC_URL` | `https://mainnet.base.org` | Base JSON-RPC endpoint |
| `BASE_WS_URL` | (empty) | WebSocket endpoint for `event_monitor.py` (falls back to polling) |
| `MIN_PROFIT_BPS` | `15` | Minimum spread, in basis points, before an opportunity is reported |
| `MAX_GAS_GWEI` | `5` | Gas cap used in profit estimates |
| `WATCH_ADDRESS` | (empty) | Your address, for incoming-transfer / governance detection in `event_monitor.py` |
| `DISCORD_WEBHOOK_URL` | (empty) | Alert webhook (or pass `--alert-discord` directly) |

Edit pairs, tokens, and fee tiers in `config.py`.

### Event monitor

```bash
# Detect only (default) — logs events, sends nothing
python3 event_monitor.py --config config/events.json

# Polling instead of WebSocket
python3 event_monitor.py --config config/events.json --polling

# Generate an example config
python3 event_monitor.py --generate-config

# Live auto-execution is opt-in and needs PRIVATE_KEY set — off by default
python3 event_monitor.py --config config/events.json --live
```

### Dashboard

```bash
cd dashboard
python3 dashboard.py
# open http://localhost:5050
```

Spread observations are stored in `dashboard/arb_db.db` (SQLite, created on first run, gitignored).

## How it works

1. For each pair, the scanner quotes every DEX: `QuoterV2.quoteExactInputSingle` for V3 pools (all four fee tiers), Aerodrome's `getAmountsOut` (stable + volatile routes), and V2 `getAmountsOut` for BaseSwap.
2. It takes the best and worst quotes, then quotes the reverse direction to price the full round trip.
3. If the round-trip profit clears `MIN_PROFIT_BPS` after the gas estimate, it prints the opportunity. Otherwise it prints nothing — which, most of the time, is the correct answer.

## Arbitrage pairs (default)

| Pair | DEXs |
|------|------|
| WETH/USDC (1, 5, 10) | UniV3, Aerodrome, BaseSwap |
| USDC/WETH (2k, 10k) | UniV3, Aerodrome, BaseSwap |
| cbBTC/USDC (1, 5) | UniV3, Aerodrome |
| WETH/cbETH, cbETH/WETH | UniV3, Aerodrome |
| USDC/DAI, DAI/USDC (5k) | UniV3, BaseSwap |
| AERO/WETH (10k) | Aerodrome, UniV3 |

## Output example

```
🔍 Base L2 Arbitrage Scanner
   RPC: https://mainnet.base.org...
   DEXs monitored: 4
   Pairs: 12
   Min profit: 15 bps
   Block: 59,277,999
────────────────────────────────────────────────────────────

  ✗ 1 WETH → USDC                 — no arb >15 bps
  ✗ 5 WETH → USDC                 — no arb >15 bps
  ...
────────────────────────────────────────────────────────────
Found 0 opportunities above 15 bps threshold
```

## License

MIT — use at your own risk. This is experimental software. Not financial advice.
