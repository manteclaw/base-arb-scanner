"""
base-arb-scanner configuration
===============================
All addresses are public Base mainnet contracts. No secrets live here —
the RPC endpoint comes from the environment (see .env.example).

Token list is as of May 2026, except AERO, which was updated to the
post-migration contract (Oct 2026).
"""

import os
from dataclasses import dataclass
from typing import List, Tuple

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# === RPC ===
# Any Base JSON-RPC endpoint works. Public default needs no key.
BASE_RPC = os.getenv("BASE_RPC_URL", "https://mainnet.base.org")

# === Scan Parameters ===
MIN_PROFIT_BPS = int(os.getenv("MIN_PROFIT_BPS", "15"))  # 0.15% minimum profit after gas
MAX_GAS_GWEI = int(os.getenv("MAX_GAS_GWEI", "5"))       # Max gas price

# === DEX Configuration ===
@dataclass
class DEX:
    name: str
    router: str
    type: str  # "uniswap_v3", "aerodrome", "uniswap_v2"
    factory: str = ""
    quoter: str = ""

DEXS = [
    DEX(
        name="uniswap_v3",
        router="0x2626664c2603336E57B271c5C0b26F421741e481",  # SwapRouter02
        type="uniswap_v3",
        factory="0x33128a8fC17869897dcE68Ed026d694621f6FDfD",
        quoter="0x3d4e44Eb1374240CE5F1B871ab261CD16335B76a",  # QuoterV2
    ),
    DEX(
        name="aerodrome",
        router="0xcF77a3Ba9A5CA399B7c97c74d54E5b1Beb874E43",
        type="aerodrome",
        factory="0x420DD381b31aEf6683db6B902084cB0FFECe40Da",
    ),
    DEX(
        name="baseswap",
        router="0x327Df1E6de05895d2ab08513aaDD9313Fe505d86",  # BaseSwap Router
        type="uniswap_v2",  # v2 style
    ),
    DEX(
        name="sushiswap_v3",
        router="0x2E2Ed0Cfd3AD2f267d06194DA7D2F5877A0Ef48A",  # Universal Router
        type="uniswap_v3",
        factory="0xB45e53277a7e0F1D35f2a77160e9e62F395597D5",
    ),
]

# === Token Addresses (Base) ===
TOKENS = {
    "WETH": "0x4200000000000000000000000000000000000006",
    "USDC": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
    "cbBTC": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf",
    "cbETH": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0EDc62",
    "DAI": "0x50c5725949A6F0c72E6C4a641F24049A917DB0Cb",
    "AERO": "0x3f5bF3EdAc59d650D8fDf9657a71F2f904A3a4Ee",
    "BRETT": "0x532f27101965dd16442e59d40670faf5ebb142e4",
    "USDbC": "0xd9aAEc86B65D86f6A7B5B1b0c42FFA531710b6CA",  # USDC bridged
}

# === Arbitrage Pairs ===
# Format: (token_in, token_out, amount_in, description)
# amount_in is in wei (for ETH) or in token units * 10^decimals
ARBITRAGE_PAIRS: List[Tuple[str, str, int, str]] = [
    # WETH → USDC (high liquidity)
    ("WETH", "USDC", 1_000_000_000_000_000_000, "1 WETH → USDC"),          # 1 WETH
    ("WETH", "USDC", 5_000_000_000_000_000_000, "5 WETH → USDC"),          # 5 WETH
    ("WETH", "USDC", 10_000_000_000_000_000_000, "10 WETH → USDC"),        # 10 WETH

    # USDC → WETH
    ("USDC", "WETH", 2_000_000_000, "2000 USDC → WETH"),                   # 2000 USDC (6 dec)
    ("USDC", "WETH", 10_000_000_000, "10000 USDC → WETH"),                 # 10000 USDC

    # cbBTC → USDC
    ("cbBTC", "USDC", 100_000_000, "1 cbBTC → USDC"),                      # 1 cbBTC (8 dec)
    ("cbBTC", "USDC", 500_000_000, "5 cbBTC → USDC"),

    # WETH → cbETH
    ("WETH", "cbETH", 1_000_000_000_000_000_000, "1 WETH → cbETH"),
    ("cbETH", "WETH", 1_000_000_000_000_000_000, "1 cbETH → WETH"),

    # USDC → DAI
    ("USDC", "DAI", 5_000_000_000, "5000 USDC → DAI"),
    ("DAI", "USDC", 5_000_000_000_000_000_000_000, "5000 DAI → USDC"),      # DAI has 18 dec

    # AERO → WETH
    ("AERO", "WETH", 10_000_000_000_000_000_000_000, "10000 AERO → WETH"), # 10000 AERO
]

# === Uniswap V3 Fee Tiers ===
FEE_TIERS = [100, 500, 3000, 10000]  # 0.01%, 0.05%, 0.3%, 1%

# === Contract ABIs (view-only — this repo never sends transactions) ===
ERC20_ABI = [
    {"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"owner","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
]

UNISWAP_V3_QUOTER_ABI = [
    {"inputs":[{"components":[{"internalType":"address","name":"tokenIn","type":"address"},{"internalType":"address","name":"tokenOut","type":"address"},{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"uint24","name":"fee","type":"uint24"},{"internalType":"uint160","name":"sqrtPriceLimitX96","type":"uint160"}],"internalType":"struct IQuoterV2.QuoteExactInputSingleParams","name":"params","type":"tuple"}],"name":"quoteExactInputSingle","outputs":[{"internalType":"uint256","name":"amountOut","type":"uint256"},{"internalType":"uint160","name":"sqrtPriceX96After","type":"uint160"},{"internalType":"uint32","name":"initializedTicksCrossed","type":"uint32"},{"internalType":"uint256","name":"gasEstimate","type":"uint256"}],"stateMutability":"nonpayable","type":"function"},
]

UNISWAP_V2_ROUTER_ABI = [
    {"inputs":[{"internalType":"uint256","name":"amountIn","type":"uint256"},{"internalType":"address[]","name":"path","type":"address[]"}],"name":"getAmountsOut","outputs":[{"internalType":"uint256[]","name":"amounts","type":"uint256[]"}],"stateMutability":"view","type":"function"},
]

# === Logging ===
LOG_LEVEL = os.getenv("LOG_LEVEL", "INFO")
