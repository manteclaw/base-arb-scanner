#!/usr/bin/env python3
"""
Base L2 Multi-DEX Arbitrage Scanner
====================================
Monitors prices across Uniswap V3, Aerodrome, BaseSwap, and SushiSwap V3
on Base L2. Finds price discrepancies that could yield profitable arbitrage.

Usage:
    python3 scanner.py           # One-off scan
    python3 scanner.py --loop    # Continuous monitoring
"""

import os
import sys
import json
import asyncio
import argparse
from dataclasses import dataclass
from decimal import Decimal
from typing import Dict, List, Optional, Tuple
from datetime import datetime

from web3 import Web3
from eth_abi import abi as eth_abi

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from config import (
    BASE_RPC, DEXS, TOKENS, ARBITRAGE_PAIRS, FEE_TIERS,
    ERC20_ABI, UNISWAP_V3_QUOTER_ABI, UNISWAP_V2_ROUTER_ABI,
    MIN_PROFIT_BPS, MAX_GAS_GWEI
)

# ─── Setup ────────────────────────────────────────────────────────────────────
w3 = Web3(Web3.HTTPProvider(BASE_RPC))
if not w3.is_connected():
    print(f"❌ RPC connection failed: {BASE_RPC[:50]}...")
    sys.exit(1)

# ─── Data Classes ───────────────────────────────────────────────────────────
@dataclass
class PriceQuote:
    dex: str
    token_in: str
    token_out: str
    amount_in: int
    amount_out: int
    fee_tier: Optional[int] = None  # For V3
    gas_estimate: int = 0
    
    @property
    def price(self) -> Decimal:
        """Price as amount_out / amount_in (with decimals correction)."""
        return Decimal(self.amount_out) / Decimal(self.amount_in)

@dataclass  
class ArbOpportunity:
    pair_desc: str
    token_in: str
    token_out: str
    amount_in: int
    buy_dex: str
    buy_price: Decimal
    buy_quote: PriceQuote
    sell_dex: str
    sell_price: Decimal
    sell_quote: PriceQuote
    profit_bps: int
    profit_usd_estimate: float
    timestamp: str

# ─── Contract Helpers ─────────────────────────────────────────────────────────
def load_contract(address: str, abi: list):
    return w3.eth.contract(address=Web3.to_checksum_address(address), abi=abi)

# Cache token decimals
decimals_cache: Dict[str, int] = {}

def get_decimals(token_symbol: str) -> int:
    if token_symbol in decimals_cache:
        return decimals_cache[token_symbol]
    addr = TOKENS.get(token_symbol)
    if not addr:
        return 18  # Default
    token = load_contract(addr, ERC20_ABI)
    try:
        dec = token.functions.decimals().call()
    except Exception:
        dec = 18
    decimals_cache[token_symbol] = dec
    return dec

def get_symbol(token_addr: str) -> str:
    for sym, addr in TOKENS.items():
        if addr.lower() == token_addr.lower():
            return sym
    return token_addr[:8]

# ─── DEX Price Fetchers ──────────────────────────────────────────────────────
# Load quoters per DEX (UniV3 and SushiV3 have different quoters)
QUOTERS = {}
for dex in DEXS:
    if dex.quoter:
        QUOTERS[dex.name] = load_contract(dex.quoter, UNISWAP_V3_QUOTER_ABI)

async def quote_uniswap_v3(token_in: str, token_out: str, amount_in: int, dex: "DEX") -> List[PriceQuote]:
    """Quote all fee tiers for a Uniswap V3 DEX."""
    quotes = []
    quoter = QUOTERS.get(dex.name)
    if not quoter:
        return quotes
    for fee in FEE_TIERS:
        try:
            params = (
                Web3.to_checksum_address(TOKENS[token_in]),
                Web3.to_checksum_address(TOKENS[token_out]),
                amount_in,
                fee,
                0  # sqrtPriceLimitX96
            )
            result = quoter.functions.quoteExactInputSingle(params).call()
            amount_out, sqrt_price_after, ticks_crossed, gas_est = result
            if amount_out > 0:
                quotes.append(PriceQuote(
                    dex=dex.name,
                    token_in=token_in,
                    token_out=token_out,
                    amount_in=amount_in,
                    amount_out=amount_out,
                    fee_tier=fee,
                    gas_estimate=gas_est
                ))
        except Exception:
            pass
    return quotes

async def quote_aerodrome(token_in: str, token_out: str, amount_in: int, dex: "DEX") -> List[PriceQuote]:
    """Quote Aerodrome (Solidly-style, uses Route[] struct)."""
    try:
        AERODROME_ROUTER_ABI = [
            {"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"components":[{"internalType":"address","name":"from","type":"address"},{"internalType":"address","name":"to","type":"address"},{"internalType":"bool","name":"stable","type":"bool"},{"internalType":"address","name":"factory","type":"address"}],"internalType":"struct IRouter.Route[]","name":"routes","type":"tuple[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"view","type":"function"},
        ]
        router = w3.eth.contract(address=Web3.to_checksum_address(dex.router), abi=AERODROME_ROUTER_ABI)
        for stable in [False, True]:
            routes = [(
                Web3.to_checksum_address(TOKENS[token_in]),
                Web3.to_checksum_address(TOKENS[token_out]),
                stable,
                Web3.to_checksum_address(dex.factory)
            )]
            try:
                amounts = router.functions.getAmountsOut(amount_in, routes).call()
                if len(amounts) >= 2 and amounts[-1] > 0:
                    return [PriceQuote(
                        dex=dex.name,
                        token_in=token_in,
                        token_out=token_out,
                        amount_in=amount_in,
                        amount_out=amounts[-1],
                        fee_tier=None,
                        gas_estimate=200000
                    )]
            except Exception:
                pass
    except Exception:
        pass
    return []

async def quote_baseswap(token_in: str, token_out: str, amount_in: int, dex: "DEX") -> List[PriceQuote]:
    """Quote BaseSwap (Uniswap V2 fork)."""
    try:
        router = load_contract(dex.router, UNISWAP_V2_ROUTER_ABI)
        path = [Web3.to_checksum_address(TOKENS[token_in]), Web3.to_checksum_address(TOKENS[token_out])]
        amounts = router.functions.getAmountsOut(amount_in, path).call()
        if len(amounts) >= 2 and amounts[-1] > 0:
            return [PriceQuote(
                dex=dex.name,
                token_in=token_in,
                token_out=token_out,
                amount_in=amount_in,
                amount_out=amounts[-1],
                fee_tier=None,
                gas_estimate=120000
            )]
    except Exception:
        pass
    return []

async def quote_dex(dex: "DEX", token_in: str, token_out: str, amount_in: int) -> List[PriceQuote]:
    """Route to the appropriate quote function."""
    if dex.type == "uniswap_v3":
        return await quote_uniswap_v3(token_in, token_out, amount_in, dex)
    elif dex.type == "aerodrome":
        return await quote_aerodrome(token_in, token_out, amount_in, dex)
    elif dex.type == "uniswap_v2":
        return await quote_baseswap(token_in, token_out, amount_in, dex)
    return []

# ─── Arbitrage Detection ─────────────────────────────────────────────────────
async def scan_pair(token_in: str, token_out: str, amount_in: int, desc: str) -> Optional[ArbOpportunity]:
    """Scan all DEXs for a single pair and find the best arbitrage."""
    all_quotes: List[PriceQuote] = []
    
    # Collect quotes from all DEXs
    for dex in DEXS:
        quotes = await quote_dex(dex, token_in, token_out, amount_in)
        all_quotes.extend(quotes)
    
    if len(all_quotes) < 2:
        return None
    
    # Find best buy (highest amount_out) and worst buy (lowest amount_out)
    best_quote = max(all_quotes, key=lambda q: q.amount_out)
    worst_quote = min(all_quotes, key=lambda q: q.amount_out)
    
    # Calculate spread
    spread_bps = int((best_quote.amount_out - worst_quote.amount_out) * 10000 / worst_quote.amount_out)
    
    # To make this a real arb, we'd need:
    # Buy on the cheaper DEX (worst_quote = you get less, meaning it's expensive)
    # Wait — we need to think about this correctly:
    # - If we want to BUY token_out with token_in, we want MORE token_out
    # - Best_quote gives us the MOST token_out → that's the cheapest DEX to BUY
    # - Worst_quote gives us the LEAST token_out → that's the most expensive DEX to BUY
    # 
    # For pure arbitrage (buy low, sell high), we need:
    # 1. BUY token_out on the DEX where it's cheapest (best_quote)
    # 2. SELL token_out back to token_in on the DEX where token_out is most expensive
    #    → That means we need the reverse direction quote
    
    # For a simple two-legged arb, we need reverse quotes too
    reverse_quotes = []
    for dex in DEXS:
        rq = await quote_dex(dex, token_out, token_in, best_quote.amount_out)
        reverse_quotes.extend(rq)
    
    if not reverse_quotes:
        return None
    
    best_reverse = max(reverse_quotes, key=lambda q: q.amount_out)
    
    # Calculate full-cycle profit
    final_amount = best_reverse.amount_out
    if final_amount <= amount_in:
        return None  # No profit
    
    profit = final_amount - amount_in
    profit_bps = int(profit * 10000 / amount_in)
    
    # Gas cost estimate (Base L2: ~$0.01-0.05 per swap)
    gas_cost_wei = (best_quote.gas_estimate + best_reverse.gas_estimate) * w3.to_wei(MAX_GAS_GWEI, 'gwei')
    
    # Convert profit to USD-ish (rough: assume token_in is ~$1-3000 range)
    # Use decimals for display
    dec_in = get_decimals(token_in)
    profit_human = profit / (10 ** dec_in)
    
    if profit_bps >= MIN_PROFIT_BPS:
        return ArbOpportunity(
            pair_desc=desc,
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            buy_dex=best_quote.dex,
            buy_price=best_quote.price,
            buy_quote=best_quote,
            sell_dex=best_reverse.dex,
            sell_price=best_reverse.price,
            sell_quote=best_reverse,
            profit_bps=profit_bps,
            profit_usd_estimate=profit_human,  # Rough estimate
            timestamp=datetime.utcnow().isoformat()
        )
    return None

# ─── Display ─────────────────────────────────────────────────────────────────
def print_opportunity(arb: ArbOpportunity):
    dec_in = get_decimals(arb.token_in)
    dec_out = get_decimals(arb.token_out)
    amount_in_human = arb.amount_in / (10 ** dec_in)
    
    print(f"\n{'='*60}")
    print(f"🚨 ARBITRAGE OPPORTUNITY: {arb.pair_desc}")
    print(f"{'='*60}")
    print(f"  Input:      {amount_in_human:.6f} {arb.token_in}")
    print(f"  Buy on:     {arb.buy_dex} @ {float(arb.buy_price):.8f}")
    if arb.buy_quote.fee_tier:
        print(f"              (fee: {arb.buy_quote.fee_tier/10000:.2%})")
    print(f"  Sell on:    {arb.sell_dex} @ {float(arb.sell_price):.8f}")
    if arb.sell_quote.fee_tier:
        print(f"              (fee: {arb.sell_quote.fee_tier/10000:.2%})")
    print(f"  Profit:     {arb.profit_bps} bps ({arb.profit_bps/100:.2f}%)")
    print(f"  Est. gain:  {arb.profit_usd_estimate:.6f} {arb.token_in}")
    print(f"  Gas est:    {arb.buy_quote.gas_estimate + arb.sell_quote.gas_estimate:,} units")
    print(f"  Time:       {arb.timestamp}")
    print(f"{'='*60}\n")

# ─── Main ────────────────────────────────────────────────────────────────────
async def run_scan():
    print(f"🔍 Base L2 Arbitrage Scanner")
    print(f"   RPC: {BASE_RPC[:50]}...")
    print(f"   DEXs monitored: {len(DEXS)}")
    print(f"   Pairs: {len(ARBITRAGE_PAIRS)}")
    print(f"   Min profit: {MIN_PROFIT_BPS} bps")
    print(f"   Block: {w3.eth.block_number:,}")
    print(f"{'─'*60}\n")
    
    opportunities = []
    for token_in, token_out, amount_in, desc in ARBITRAGE_PAIRS:
        try:
            arb = await scan_pair(token_in, token_out, amount_in, desc)
            if arb:
                opportunities.append(arb)
                print_opportunity(arb)
            else:
                print(f"  ✗ {desc:30s} — no arb >{MIN_PROFIT_BPS} bps")
        except Exception as e:
            print(f"  ✗ {desc:30s} — error: {e}")
    
    print(f"\n{'─'*60}")
    print(f"Found {len(opportunities)} opportunities above {MIN_PROFIT_BPS} bps threshold")
    return opportunities

async def run_loop(interval: int = 30):
    """Continuous monitoring loop."""
    print(f"🔄 Starting continuous monitoring (interval: {interval}s)")
    print(f"   Press Ctrl+C to stop\n")
    
    while True:
        try:
            await run_scan()
            print(f"\n⏳ Sleeping {interval}s...\n")
            await asyncio.sleep(interval)
        except KeyboardInterrupt:
            print("\n👋 Stopping scanner")
            break
        except Exception as e:
            print(f"❌ Loop error: {e}")
            await asyncio.sleep(5)

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Base L2 Arbitrage Scanner")
    parser.add_argument("--loop", action="store_true", help="Run continuous monitoring")
    parser.add_argument("--interval", type=int, default=30, help="Loop interval in seconds")
    args = parser.parse_args()
    
    if args.loop:
        asyncio.run(run_loop(args.interval))
    else:
        asyncio.run(run_scan())
