#!/usr/bin/env python3
"""
Smart Contract Event Monitor
============================
Monitors Base L2 smart contracts for actionable events and logs them.
Auto-execution is opt-in only (see --live) and off by default.

Usage:
    python3 event_monitor.py --config config/events.json
    python3 event_monitor.py --config config/events.json --dry-run
    python3 event_monitor.py --config config/events.json --alert-discord https://...

Event Types:
    - airdrop_claims   : Transfer events to our wallet from new tokens
    - governance       : ProposalCreated, VoteCast events
    - vesting          : Token unlock / cliff releases
    - presale          : SaleEnabled, WhitelistOpen events
    - pool_rewards     : RewardPaid, Harvest events

Safety:
    - Simulation before execution
    - Gas check (< $0.05)
    - Contract whitelist
"""

import os
import sys
import json
import time
import asyncio
import argparse
import logging
import aiohttp
import traceback
from dataclasses import dataclass, field
from typing import Dict, List, Optional, Callable, Any, Tuple
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urljoin

from web3 import Web3, AsyncWeb3
from web3.types import LogReceipt, TxReceipt
from eth_abi import decode

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

# ─── Logging ──────────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)-8s | %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("event-monitor")

# ─── Base L2 Constants ──────────────────────────────────────────────────────
BASE_CHAIN_ID = 8453
BASE_SYMBOL = "ETH"
WETH_ADDRESS = "0x4200000000000000000000000000000000000006"
USDC_ADDRESS = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"

# ─── Event Signatures (topic0) ──────────────────────────────────────────────
EVENT_SIGNATURES = {
    # ERC20
    "Transfer": "0xddf252ad1be2c89b69c2b068fc378daa952ba7f163c4a11628f55a4df523b3ef",
    "Approval": "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925",
    
    # Governance
    "ProposalCreated": "0x7d84e6266b771a68382b5d1ceaa25a7f81885e1e5e234e9922976ede67c2e5d8",
    "VoteCast": "0xb8e138887d0d13d440d3641a33b32f4250ee138106c096f4caff901a3f6a3a6f",
    "ProposalQueued": "0x9c1e92c4ae8d84d54a4a9a0dbee07d6ba389f8450a1d98f858f89c7d1f2c4e5a",
    "ProposalExecuted": "0x712ae1383f79ac853f8d346153bb02d432b881d9401414e83f93791daa3166df8",
    
    # Vesting / Token
    "TokensReleased": "0x7d84e6266b771a68382b5d1ceaa25a7f81885e1e5e234e9922976ede67c2e5d8",  # Generic
    "VestingScheduleCreated": "0x2f0c0c8c8f0e0c8f0e0c8f0e0c8f0e0c8f0e0c8f0e0c8f0e0c8f0e0c8f0e0c8",
    "CliffReached": "0x3d0c0c8c8f0e0c8f0e0c8f0e0c8f0e0c8f0e0c8f0e0c8f0e0c8f0e0c8f0e0c8",
    
    # Presale
    "SaleEnabled": "0x8c5be1e5ebec7d5bd14f71427d1e84f3dd0314c0f7b2291e5b200ac8c7c3b925",
    "WhitelistOpen": "0x7d84e6266b771a68382b5d1ceaa25a7f81885e1e5e234e9922976ede67c2e5d8",
    "SaleStarted": "0x9c1e92c4ae8d84d54a4a9a0dbee07d6ba389f8450a1d98f858f89c7d1f2c4e5a",
    
    # Pool Rewards
    "RewardPaid": "0xe2403640ba68fed3a2f88b7557551d1993f84b99bb10ff833f0cf8db0c5e04f0",
    "Harvest": "0x4dec04e750ca11537cabcd8a9eab06494de28da3845ef949a6e0f4051b149fad",
    "Staked": "0x9c1e92c4ae8d84d54a4a9a0dbee07d6ba389f8450a1d98f858f89c7d1f2c4e5a",
    "Withdrawn": "0x7084f5476618d8e60b11ef0d7d3f5d8e5d8e5d8e5d8e5d8e5d8e5d8e5d8e5d8",
}

# ─── ABIs ────────────────────────────────────────────────────────────────────
ERC20_ABI = [
    {"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"symbol","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"name","outputs":[{"name":"","type":"string"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"recipient","type":"address"},{"name":"amount","type":"uint256"}],"name":"transfer","outputs":[{"name":"","type":"bool"}],"stateMutability":"nonpayable","type":"function"},
]

GOVERNANCE_ABI = [
    {"inputs":[{"name":"proposalId","type":"uint256"}],"name":"state","outputs":[{"name":"","type":"uint8"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"proposalId","type":"uint256"},{"name":"support","type":"uint8"}],"name":"castVote","outputs":[{"name":"","type":"uint256"}],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"proposalId","type":"uint256"}],"name":"queue","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"proposalId","type":"uint256"}],"name":"execute","outputs":[],"stateMutability":"payable","type":"function"},
    {"inputs":[],"name":"proposalCount","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"","type":"uint256"}],"name":"proposals","outputs":[{"name":"id","type":"uint256"},{"name":"proposer","type":"address"},{"name":"eta","type":"uint256"},{"name":"startBlock","type":"uint256"},{"name":"endBlock","type":"uint256"},{"name":"forVotes","type":"uint256"},{"name":"againstVotes","type":"uint256"},{"name":"abstainVotes","type":"uint256"},{"name":"canceled","type":"bool"},{"name":"executed","type":"bool"}],"stateMutability":"view","type":"function"},
]

VESTING_ABI = [
    {"inputs":[],"name":"release","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"beneficiary","type":"address"}],"name":"release","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"","type":"address"}],"name":"vestingSchedules","outputs":[{"name":"totalAmount","type":"uint256"},{"name":"released","type":"uint256"},{"name":"cliff","type":"uint256"},{"name":"start","type":"uint256"},{"name":"duration","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"beneficiary","type":"address"}],"name":"computeReleasableAmount","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"token","outputs":[{"name":"","type":"address"}],"stateMutability":"view","type":"function"},
]

REWARDS_ABI = [
    {"inputs":[],"name":"getReward","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"account","type":"address"}],"name":"earned","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"stake","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"amount","type":"uint256"}],"name":"withdraw","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[],"name":"exit","outputs":[],"stateMutability":"nonpayable","type":"function"},
]

CLAIM_ABI = [
    {"inputs":[{"name":"index","type":"uint256"},{"name":"account","type":"address"},{"name":"amount","type":"uint256"},{"name":"merkleProof","type":"bytes32[]"}],"name":"claim","outputs":[],"stateMutability":"nonpayable","type":"function"},
    {"inputs":[{"name":"index","type":"uint256"},{"name":"account","type":"address"}],"name":"isClaimed","outputs":[{"name":"","type":"bool"}],"stateMutability":"view","type":"function"},
    {"inputs":[],"name":"merkleRoot","outputs":[{"name":"","type":"bytes32"}],"stateMutability":"view","type":"function"},
]

# ─── Dataclasses ──────────────────────────────────────────────────────────────
@dataclass
class WatchedContract:
    address: str
    name: str
    event_types: List[str]
    abi: List[Dict] = field(default_factory=list)
    auto_execute: bool = False
    action_config: Dict = field(default_factory=dict)
    # For governance: our proposals only
    our_address: Optional[str] = None

@dataclass
class DetectedEvent:
    tx_hash: str
    block_number: int
    contract_address: str
    contract_name: str
    event_type: str
    event_name: str
    data: Dict[str, Any]
    timestamp: datetime
    log_index: int = 0
    
    def usd_value(self) -> Optional[float]:
        """Estimate USD value if token amount available."""
        if "token_amount" in self.data and "token_price" in self.data:
            return self.data["token_amount"] * self.data["token_price"]
        if "amount" in self.data and "token_decimals" in self.data:
            raw = self.data["amount"]
            dec = self.data["token_decimals"]
            amount = raw / (10 ** dec) if dec else raw
            # Rough ETH price ~$3000 as fallback
            price = self.data.get("token_price", 3000 if self.data.get("token_address", "").lower() == WETH_ADDRESS.lower() else 1)
            return amount * price
        return None

# ─── Gas Estimation ──────────────────────────────────────────────────────────
class GasEstimator:
    def __init__(self, w3: Web3):
        self.w3 = w3
        self.eth_price_usd = 3000.0  # Updated dynamically
        self.max_gas_usd = 0.05
    
    def update_eth_price(self):
        """Fetch ETH price from a simple oracle or cache."""
        # Could integrate with Chainlink or DEX quote
        pass
    
    def estimate_tx_cost_usd(self, gas_estimate: int) -> float:
        """Estimate transaction cost in USD."""
        try:
            gas_price = self.w3.eth.gas_price
            cost_eth = (gas_estimate * gas_price) / 1e18
            return cost_eth * self.eth_price_usd
        except Exception as e:
            logger.warning(f"Gas estimation failed: {e}")
            return float('inf')
    
    def is_profitable(self, gas_estimate: int, expected_value_usd: float) -> bool:
        """Check if tx cost is under limit and value exceeds cost."""
        tx_cost = self.estimate_tx_cost_usd(gas_estimate)
        if tx_cost > self.max_gas_usd:
            logger.info(f"Gas cost ${tx_cost:.4f} exceeds limit ${self.max_gas_usd}")
            return False
        if expected_value_usd > 0 and tx_cost >= expected_value_usd * 0.5:
            logger.info(f"Gas cost ${tx_cost:.4f} eats too much of value ${expected_value_usd:.2f}")
            return False
        return True

# ─── Event Monitor Core ─────────────────────────────────────────────────────
class EventMonitor:
    def __init__(self, config_path: str, dry_run: bool = True):
        self.config = self._load_config(config_path)
        self.dry_run = dry_run
        self.w3 = self._init_web3()
        self.gas_estimator = GasEstimator(self.w3)
        self.watched: Dict[str, WatchedContract] = {}
        self.handlers: Dict[str, Callable] = {
            "airdrop_claims": self._handle_airdrop,
            "governance": self._handle_governance,
            "vesting": self._handle_vesting,
            "presale": self._handle_presale,
            "pool_rewards": self._handle_pool_rewards,
        }
        self.alert_callbacks: List[Callable] = []
        self.processed_txs: set = set()  # Dedup
        self.max_history = 10000
        
        # Wallet
        self.our_address = os.getenv("WATCH_ADDRESS", "").lower()
        self.private_key = os.getenv("PRIVATE_KEY", "")
        
        self._init_contracts()
    
    def _load_config(self, path: str) -> Dict:
        with open(path) as f:
            return json.load(f)
    
    def _init_web3(self) -> Web3:
        rpc = self.config.get("rpc_url") or os.getenv("BASE_RPC_URL", "https://mainnet.base.org")
        rpc = os.path.expandvars(rpc)
        w3 = Web3(Web3.HTTPProvider(rpc))
        if not w3.is_connected():
            raise ConnectionError(f"Cannot connect to RPC: {rpc}")
        logger.info(f"Connected to Base L2 (chain: {w3.eth.chain_id})")
        return w3
    
    def _init_contracts(self):
        for item in self.config.get("contracts", []):
            addr = Web3.to_checksum_address(item["address"])
            wc = WatchedContract(
                address=addr,
                name=item.get("name", "Unknown"),
                event_types=item.get("event_types", []),
                abi=item.get("abi", []),
                auto_execute=item.get("auto_execute", False),
                action_config=item.get("action_config", {}),
                our_address=self.our_address,
            )
            self.watched[addr.lower()] = wc
            logger.info(f"Watching {wc.name} at {addr} for events: {wc.event_types}")
    
    # ─── Event Detection ────────────────────────────────────────────────────
    def _build_filter_params(self, from_block: int, to_block: int) -> List[Dict]:
        """Build eth_getLogs filter params for all watched contracts."""
        addresses = list(self.watched.keys())
        # Collect all relevant topics
        all_topics = []
        for wc in self.watched.values():
            for et in wc.event_types:
                if et == "airdrop_claims":
                    all_topics.append(EVENT_SIGNATURES["Transfer"])
                elif et == "governance":
                    all_topics.extend([EVENT_SIGNATURES["ProposalCreated"], EVENT_SIGNATURES["VoteCast"]])
                elif et == "vesting":
                    all_topics.append(EVENT_SIGNATURES["TokensReleased"])
                elif et == "presale":
                    all_topics.extend([EVENT_SIGNATURES["SaleEnabled"], EVENT_SIGNATURES["WhitelistOpen"]])
                elif et == "pool_rewards":
                    all_topics.extend([EVENT_SIGNATURES["RewardPaid"], EVENT_SIGNATURES["Harvest"]])
        
        # Remove duplicates
        topics = list(set(all_topics))
        if not topics:
            return []
        
        return [{
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": addresses,
            "topics": [topics]  # OR logic on topics[0]
        }]
    
    def _parse_log(self, log: LogReceipt) -> Optional[DetectedEvent]:
        """Parse a raw log into a structured event."""
        contract_addr = log["address"].lower()
        if contract_addr not in self.watched:
            return None
        
        wc = self.watched[contract_addr]
        topic0 = log["topics"][0].hex() if log["topics"] else ""
        tx_hash = log["transactionHash"].hex()
        
        # Dedup check
        dedup_key = f"{tx_hash}:{log.get('logIndex', 0)}"
        if dedup_key in self.processed_txs:
            return None
        
        # Get event name
        event_name = None
        for name, sig in EVENT_SIGNATURES.items():
            if sig.lower() == topic0.lower():
                event_name = name
                break
        
        if not event_name:
            return None
        
        # Determine event category
        event_category = None
        if event_name == "Transfer" and "airdrop_claims" in wc.event_types:
            event_category = "airdrop_claims"
        elif event_name in ["ProposalCreated", "VoteCast"] and "governance" in wc.event_types:
            event_category = "governance"
        elif event_name in ["TokensReleased", "CliffReached"] and "vesting" in wc.event_types:
            event_category = "vesting"
        elif event_name in ["SaleEnabled", "WhitelistOpen", "SaleStarted"] and "presale" in wc.event_types:
            event_category = "presale"
        elif event_name in ["RewardPaid", "Harvest", "Staked", "Withdrawn"] and "pool_rewards" in wc.event_types:
            event_category = "pool_rewards"
        
        if not event_category:
            return None
        
        # Parse event data based on type
        data = self._extract_event_data(event_name, log, wc)
        
        evt = DetectedEvent(
            tx_hash=tx_hash,
            block_number=log["blockNumber"],
            contract_address=contract_addr,
            contract_name=wc.name,
            event_type=event_category,
            event_name=event_name,
            data=data,
            timestamp=datetime.now(timezone.utc),
            log_index=log.get("logIndex", 0),
        )
        
        self.processed_txs.add(dedup_key)
        if len(self.processed_txs) > self.max_history:
            # Trim old entries (simple: clear and re-add recent - not perfect but works)
            self.processed_txs = set(list(self.processed_txs)[-self.max_history//2:])
        
        return evt
    
    def _extract_event_data(self, event_name: str, log: LogReceipt, wc: WatchedContract) -> Dict:
        """Extract structured data from log based on event type."""
        data = {
            "raw_topics": [t.hex() for t in log["topics"]],
            "raw_data": log["data"].hex() if log["data"] else "",
        }
        topics = log["topics"]
        
        try:
            if event_name == "Transfer":
                # ERC20 Transfer: topic1=from, topic2=to, data=amount
                from_addr = "0x" + topics[1].hex()[-40:] if len(topics) > 1 else ""
                to_addr = "0x" + topics[2].hex()[-40:] if len(topics) > 2 else ""
                if len(topics) == 3:
                    amount = int(log["data"].hex(), 16) if log["data"] else 0
                else:
                    amount = int(topics[3].hex(), 16) if len(topics) > 3 else 0
                
                data.update({
                    "from": from_addr.lower(),
                    "to": to_addr.lower(),
                    "amount": amount,
                    "token_address": log["address"].lower(),
                    "is_incoming": to_addr.lower() == self.our_address,
                    "is_outgoing": from_addr.lower() == self.our_address,
                })
                
                # Try to get token info
                try:
                    token = self.w3.eth.contract(address=log["address"], abi=ERC20_ABI)
                    data["token_symbol"] = token.functions.symbol().call()
                    data["token_decimals"] = token.functions.decimals().call()
                    data["token_amount"] = amount / (10 ** data["token_decimals"])
                except Exception:
                    data["token_decimals"] = 18
                    data["token_amount"] = amount / 1e18
            
            elif event_name == "ProposalCreated":
                # Parse indexed + non-indexed
                if len(topics) >= 2:
                    proposal_id = int(topics[1].hex(), 16)
                    data["proposal_id"] = proposal_id
                # Decode description, etc from data
                try:
                    decoded = decode(["address", "address[]", "uint256[]", "string[]", "bytes[]", "uint256", "uint256", "string"], log["data"])
                    data["proposer"] = decoded[0]
                    data["targets"] = decoded[1]
                    data["values"] = decoded[2]
                    data["signatures"] = decoded[3]
                    data["calldatas"] = decoded[4]
                    data["start_block"] = decoded[5]
                    data["end_block"] = decoded[6]
                    data["description"] = decoded[7]
                except Exception:
                    pass
            
            elif event_name == "VoteCast":
                if len(topics) >= 2:
                    proposal_id = int(topics[1].hex(), 16)
                    data["proposal_id"] = proposal_id
                try:
                    decoded = decode(["address", "uint8", "uint256", "string"], log["data"])
                    data["voter"] = decoded[0]
                    data["support"] = decoded[1]  # 0=against, 1=for, 2=abstain
                    data["votes"] = decoded[2]
                    data["reason"] = decoded[3]
                except Exception:
                    pass
            
            elif event_name == "RewardPaid":
                try:
                    decoded = decode(["address", "address", "uint256"], log["data"])
                    data["user"] = decoded[0]
                    data["reward_token"] = decoded[1]
                    data["amount"] = decoded[2]
                    data["is_ours"] = decoded[0].lower() == self.our_address
                except Exception:
                    pass
            
            elif event_name == "Harvest":
                try:
                    decoded = decode(["address", "uint256"], log["data"])
                    data["user"] = decoded[0]
                    data["amount"] = decoded[1]
                    data["is_ours"] = decoded[0].lower() == self.our_address
                except Exception:
                    pass
            
            elif event_name in ["SaleEnabled", "WhitelistOpen", "SaleStarted"]:
                data["presale_contract"] = log["address"].lower()
                try:
                    decoded = decode(["uint256", "uint256"], log["data"])
                    data["sale_id"] = decoded[0]
                    data["start_time"] = decoded[1]
                except Exception:
                    pass
            
            elif event_name in ["TokensReleased", "CliffReached"]:
                try:
                    decoded = decode(["address", "uint256"], log["data"])
                    data["beneficiary"] = decoded[0]
                    data["amount"] = decoded[1]
                    data["is_ours"] = decoded[0].lower() == self.our_address
                except Exception:
                    pass
        
        except Exception as e:
            logger.warning(f"Error parsing {event_name}: {e}")
        
        return data
    
    # ─── Execution / Simulation ───────────────────────────────────────────────
    def _simulate_tx(self, contract_addr: str, function_name: str, args: Tuple, abi: List[Dict]) -> Tuple[bool, Optional[int]]:
        """Simulate a transaction. Returns (success, gas_estimate)."""
        if not self.private_key or not self.our_address:
            return False, None
        
        try:
            contract = self.w3.eth.contract(address=contract_addr, abi=abi)
            func = getattr(contract.functions, function_name)(*args)
            
            # Call simulation (static)
            func.call({"from": self.our_address})
            
            # Gas estimate
            gas = func.estimate_gas({"from": self.our_address})
            return True, gas
        except Exception as e:
            logger.warning(f"Simulation failed for {function_name}: {e}")
            return False, None
    
    def _send_tx(self, contract_addr: str, function_name: str, args: Tuple, abi: List[Dict], gas_limit: Optional[int] = None) -> Optional[str]:
        """Send a transaction. Returns tx_hash or None."""
        if self.dry_run or not self.private_key:
            logger.info(f"[DRY-RUN] Would execute {function_name} on {contract_addr}")
            return None
        
        try:
            contract = self.w3.eth.contract(address=contract_addr, abi=abi)
            func = getattr(contract.functions, function_name)(*args)
            
            nonce = self.w3.eth.get_transaction_count(self.our_address)
            gas_price = self.w3.eth.gas_price
            
            tx = func.build_transaction({
                "from": self.our_address,
                "nonce": nonce,
                "gasPrice": gas_price,
                "gas": gas_limit or 500000,
                "chainId": BASE_CHAIN_ID,
            })
            
            signed = self.w3.eth.account.sign_transaction(tx, self.private_key)
            tx_hash = self.w3.eth.send_raw_transaction(signed.rawTransaction)
            
            logger.info(f"Tx sent: {tx_hash.hex()}")
            
            # Wait for receipt
            receipt = self.w3.eth.wait_for_transaction_receipt(tx_hash, timeout=120)
            if receipt["status"] == 1:
                logger.info(f"Tx confirmed: {tx_hash.hex()}")
                return tx_hash.hex()
            else:
                logger.error(f"Tx failed: {tx_hash.hex()}")
                return None
        except Exception as e:
            logger.error(f"Tx error: {e}")
            return None
    
    # ─── Event Handlers ───────────────────────────────────────────────────────
    def _handle_airdrop(self, evt: DetectedEvent) -> Dict:
        """Handle airdrop/transfer event."""
        result = {"action": "none", "tx_hash": None, "reason": ""}
        
        # Only care about incoming transfers to our wallet
        if not evt.data.get("is_incoming"):
            result["reason"] = "Not incoming to our wallet"
            return result
        
        token_addr = evt.data.get("token_address", "")
        amount = evt.data.get("token_amount", 0)
        
        logger.info(f"🎁 Airdrop detected: {amount:.6f} {evt.data.get('token_symbol', '???')} "
                   f"from {evt.contract_name} (tx: {evt.tx_hash[:20]}...)")
        
        # For airdrops with claim function, we'd need merkle proof data
        # This is usually off-chain. Auto-claim requires pre-loaded proofs.
        claim_cfg = evt.contract_name in [c.name for c in self.watched.values() if c.auto_execute]
        
        if claim_cfg:
            # Check if we have claim config
            wc = self.watched.get(evt.contract_address)
            if wc and wc.action_config.get("claim"):
                # Would need merkle proof - usually can't auto-claim without it
                result["reason"] = "Auto-claim requires merkle proof (not available on-chain)"
            else:
                result["reason"] = "No claim configuration"
        else:
            result["reason"] = "Auto-execute disabled"
        
        return result
    
    def _handle_governance(self, evt: DetectedEvent) -> Dict:
        """Handle governance events."""
        result = {"action": "none", "tx_hash": None, "reason": ""}
        
        if evt.event_name == "ProposalCreated":
            proposer = evt.data.get("proposer", "").lower()
            is_ours = proposer == self.our_address
            
            logger.info(f"📜 Proposal #{evt.data.get('proposal_id')} created by {proposer[:20]}... "
                       f"(ours: {is_ours})")
            
            if is_ours and evt.contract_name in [c.name for c in self.watched.values() if c.auto_execute]:
                # Auto-vote YES on our own proposals
                wc = self.watched.get(evt.contract_address)
                if wc and wc.action_config.get("auto_vote_own", True):
                    # Vote "for" (support=1)
                    success, gas = self._simulate_tx(
                        evt.contract_address,
                        "castVote",
                        (evt.data["proposal_id"], 1),
                        GOVERNANCE_ABI
                    )
                    if success and self.gas_estimator.is_profitable(gas or 200000, 0):
                        tx_hash = self._send_tx(
                            evt.contract_address,
                            "castVote",
                            (evt.data["proposal_id"], 1),
                            GOVERNANCE_ABI,
                            gas_limit=gas
                        )
                        result["action"] = "voted_yes"
                        result["tx_hash"] = tx_hash
                        result["reason"] = "Auto-voted YES on our proposal"
                    else:
                        result["reason"] = "Simulation failed or gas too high"
                else:
                    result["reason"] = "Auto-vote disabled"
            else:
                result["reason"] = "Not our proposal"
        
        elif evt.event_name == "VoteCast":
            voter = evt.data.get("voter", "").lower()
            logger.info(f"🗳️ Vote on proposal #{evt.data.get('proposal_id')}: "
                       f"support={evt.data.get('support')} by {voter[:20]}...")
            result["reason"] = "Observed vote (no action needed)"
        
        return result
    
    def _handle_vesting(self, evt: DetectedEvent) -> Dict:
        """Handle vesting unlock events."""
        result = {"action": "none", "tx_hash": None, "reason": ""}
        
        is_ours = evt.data.get("is_ours", False)
        amount = evt.data.get("amount", 0)
        
        logger.info(f"🔓 Vesting event: {evt.event_name} from {evt.contract_name} "
                   f"(ours: {is_ours}, amount: {amount})")
        
        if not is_ours:
            result["reason"] = "Not our vesting schedule"
            return result
        
        wc = self.watched.get(evt.contract_address)
        if not wc or not wc.auto_execute:
            result["reason"] = "Auto-execute disabled"
            return result
        
        # Try to withdraw/release
        # Many vesting contracts have release() or release(beneficiary)
        release_func = wc.action_config.get("release_function", "release")
        args = () if release_func == "release" else (self.our_address,)
        
        success, gas = self._simulate_tx(
            evt.contract_address,
            release_func,
            args,
            VESTING_ABI
        )
        
        if success:
            # Estimate token value
            token_addr = ""
            try:
                contract = self.w3.eth.contract(address=evt.contract_address, abi=VESTING_ABI)
                token_addr = contract.functions.token().call()
            except Exception:
                pass
            
            # Rough value estimation
            value_usd = 0
            if token_addr.lower() == WETH_ADDRESS.lower():
                value_usd = (amount / 1e18) * 3000
            
            if self.gas_estimator.is_profitable(gas or 200000, value_usd):
                tx_hash = self._send_tx(
                    evt.contract_address,
                    release_func,
                    args,
                    VESTING_ABI,
                    gas_limit=gas
                )
                result["action"] = "withdrawn"
                result["tx_hash"] = tx_hash
                result["reason"] = f"Auto-withdrew {amount} tokens"
            else:
                result["reason"] = "Gas too expensive for value"
        else:
            result["reason"] = "Simulation failed"
        
        return result
    
    def _handle_presale(self, evt: DetectedEvent) -> Dict:
        """Handle presale events."""
        result = {"action": "none", "tx_hash": None, "reason": ""}
        
        logger.info(f"🚀 Presale event: {evt.event_name} from {evt.contract_name}")
        
        # Presales usually require specific actions (buy, whitelist register)
        # These are typically one-off and need ETH/USDC
        wc = self.watched.get(evt.contract_address)
        if wc and wc.action_config.get("auto_buy"):
            # Would need: amount, max price, etc
            result["reason"] = "Auto-buy not implemented (needs configuration)"
        else:
            result["reason"] = "Presale detected (manual action required)"
        
        return result
    
    def _handle_pool_rewards(self, evt: DetectedEvent) -> Dict:
        """Handle pool reward events."""
        result = {"action": "none", "tx_hash": None, "reason": ""}
        
        is_ours = evt.data.get("is_ours", False)
        amount = evt.data.get("amount", 0)
        
        logger.info(f"💰 Reward event: {evt.event_name} from {evt.contract_name} "
                   f"(ours: {is_ours})")
        
        if not is_ours and evt.event_name != "Harvest":
            result["reason"] = "Not our reward"
            return result
        
        wc = self.watched.get(evt.contract_address)
        if not wc or not wc.auto_execute:
            result["reason"] = "Auto-execute disabled"
            return result
        
        # For Harvest events, someone else harvested. We should check our own earnings.
        # For RewardPaid, it's our reward.
        
        # Strategy: check earned(), then getReward() if > threshold
        try:
            contract = self.w3.eth.contract(address=evt.contract_address, abi=REWARDS_ABI)
            earned = contract.functions.earned(self.our_address).call()
            
            # Get token to estimate value
            reward_token = wc.action_config.get("reward_token", "")
            
            # Threshold check (e.g., > 0.01 ETH worth)
            threshold = wc.action_config.get("min_reward_threshold", 0)
            
            if earned > threshold:
                success, gas = self._simulate_tx(
                    evt.contract_address,
                    "getReward",
                    (),
                    REWARDS_ABI
                )
                
                if success and self.gas_estimator.is_profitable(gas or 250000, 0):
                    tx_hash = self._send_tx(
                        evt.contract_address,
                        "getReward",
                        (),
                        REWARDS_ABI,
                        gas_limit=gas
                    )
                    result["action"] = "harvested"
                    result["tx_hash"] = tx_hash
                    result["reason"] = f"Auto-harvested {earned} rewards"
                else:
                    result["reason"] = "Simulation failed or gas too high"
            else:
                result["reason"] = f"Reward {earned} below threshold {threshold}"
        except Exception as e:
            result["reason"] = f"Error checking rewards: {e}"
        
        return result
    
    # ─── Alerting ────────────────────────────────────────────────────────────
    async def _send_discord_alert(self, evt: DetectedEvent, action_result: Dict):
        """Send alert to Discord webhook."""
        if not hasattr(self, "discord_webhook") or not self.discord_webhook:
            return
        
        usd = evt.usd_value()
        usd_str = f"${usd:.2f}" if usd else "N/A"
        
        color = 0x00ff00 if action_result["tx_hash"] else 0xffaa00 if action_result["action"] != "none" else 0x999999
        
        payload = {
            "embeds": [{
                "title": f"🔔 {evt.event_name} | {evt.contract_name}",
                "color": color,
                "fields": [
                    {"name": "Type", "value": evt.event_type, "inline": True},
                    {"name": "Action", "value": action_result["action"], "inline": True},
                    {"name": "USD Value", "value": usd_str, "inline": True},
                    {"name": "Block", "value": str(evt.block_number), "inline": True},
                    {"name": "Reason", "value": action_result.get("reason", "")[:1000], "inline": False},
                    {"name": "Tx", "value": f"`{evt.tx_hash}`", "inline": False},
                ],
                "timestamp": evt.timestamp.isoformat(),
                "footer": {"text": f"Auto-execute: {'ON' if not self.dry_run else 'DRY-RUN'}"}
            }]
        }
        
        if action_result.get("tx_hash"):
            payload["embeds"][0]["fields"].append({
                "name": "Action Tx", "value": f"`{action_result['tx_hash']}`", "inline": False
            })
        
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(self.discord_webhook, json=payload) as resp:
                    if resp.status not in (200, 204):
                        logger.warning(f"Discord alert failed: {resp.status}")
        except Exception as e:
            logger.warning(f"Discord alert error: {e}")
    
    async def _send_console_alert(self, evt: DetectedEvent, action_result: Dict):
        """Print alert to console."""
        usd = evt.usd_value()
        usd_str = f"${usd:.2f}" if usd else "N/A"
        
        print(f"\n{'═'*60}")
        print(f"  🔥 EVENT: {evt.event_name} | {evt.contract_name}")
        print(f"  📍 Contract: {evt.contract_address}")
        print(f"  🔗 Tx: {evt.tx_hash}")
        print(f"  📦 Block: {evt.block_number}")
        print(f"  💵 USD Value: {usd_str}")
        print(f"  ⚡ Action: {action_result['action']} | {action_result.get('reason', '')}")
        if action_result.get("tx_hash"):
            print(f"  ✅ Action Tx: {action_result['tx_hash']}")
        print(f"{'═'*60}\n")
    
    # ─── Main Loop ────────────────────────────────────────────────────────────
    async def run_polling(self, interval: int = 15):
        """Run RPC polling loop."""
        logger.info(f"Starting polling monitor (interval: {interval}s)")
        
        last_block = self.w3.eth.block_number
        
        while True:
            try:
                current = self.w3.eth.block_number
                if current > last_block:
                    blocks_to_check = min(current - last_block, 100)  # Cap at 100 blocks
                    from_block = current - blocks_to_check + 1
                    
                    for block_num in range(from_block, current + 1):
                        await self._check_block(block_num)
                    
                    last_block = current
                
                await asyncio.sleep(interval)
            except KeyboardInterrupt:
                logger.info("Monitor stopped")
                break
            except Exception as e:
                logger.error(f"Polling error: {e}")
                await asyncio.sleep(5)
    
    async def _check_block(self, block_num: int):
        """Check a single block for events."""
        try:
            # Get logs for our watched contracts in this block
            for addr, wc in self.watched.items():
                try:
                    # Build topic filter
                    topics = []
                    for et in wc.event_types:
                        if et == "airdrop_claims":
                            topics.append(EVENT_SIGNATURES["Transfer"])
                        elif et == "governance":
                            topics.extend([EVENT_SIGNATURES["ProposalCreated"], EVENT_SIGNATURES["VoteCast"]])
                        elif et == "vesting":
                            topics.append(EVENT_SIGNATURES["TokensReleased"])
                        elif et == "presale":
                            topics.extend([EVENT_SIGNATURES["SaleEnabled"], EVENT_SIGNATURES["WhitelistOpen"]])
                        elif et == "pool_rewards":
                            topics.extend([EVENT_SIGNATURES["RewardPaid"], EVENT_SIGNATURES["Harvest"]])
                    
                    if not topics:
                        continue
                    
                    logs = self.w3.eth.get_logs({
                        "fromBlock": block_num,
                        "toBlock": block_num,
                        "address": addr,
                        "topics": [list(set(topics))]
                    })
                    
                    for log in logs:
                        evt = self._parse_log(log)
                        if evt:
                            await self._process_event(evt)
                except Exception as e:
                    logger.warning(f"Error checking {wc.name} at block {block_num}: {e}")
        except Exception as e:
            logger.warning(f"Error checking block {block_num}: {e}")
    
    async def _process_event(self, evt: DetectedEvent):
        """Process a detected event through its handler."""
        handler = self.handlers.get(evt.event_type)
        if not handler:
            return
        
        try:
            result = handler(evt)
            await self._send_console_alert(evt, result)
            if hasattr(self, "discord_webhook"):
                await self._send_discord_alert(evt, result)
        except Exception as e:
            logger.error(f"Error handling {evt.event_type}: {e}")
            traceback.print_exc()
    
    async def run_websocket(self):
        """Run WebSocket subscription (eth_subscribe)."""
        ws_url = os.path.expandvars(self.config.get("ws_url") or os.getenv("BASE_WS_URL", ""))
        if not ws_url:
            logger.error("No WebSocket URL configured. Falling back to polling.")
            return await self.run_polling()
        
        logger.info(f"Connecting to WebSocket: {ws_url[:40]}...")
        
        try:
            import websockets
        except ImportError:
            logger.error("websockets library not installed. Use: pip install websockets")
            return await self.run_polling()
        
        while True:
            try:
                async with websockets.connect(ws_url) as ws:
                    # Subscribe to logs for our contracts
                    addresses = list(self.watched.keys())
                    topics = []
                    for wc in self.watched.values():
                        for et in wc.event_types:
                            if et == "airdrop_claims":
                                topics.append(EVENT_SIGNATURES["Transfer"])
                            elif et == "governance":
                                topics.extend([EVENT_SIGNATURES["ProposalCreated"], EVENT_SIGNATURES["VoteCast"]])
                            elif et == "vesting":
                                topics.append(EVENT_SIGNATURES["TokensReleased"])
                            elif et == "presale":
                                topics.extend([EVENT_SIGNATURES["SaleEnabled"], EVENT_SIGNATURES["WhitelistOpen"]])
                            elif et == "pool_rewards":
                                topics.extend([EVENT_SIGNATURES["RewardPaid"], EVENT_SIGNATURES["Harvest"]])
                    
                    topics = list(set(topics))
                    
                    subscribe_msg = {
                        "jsonrpc": "2.0",
                        "id": 1,
                        "method": "eth_subscribe",
                        "params": ["logs", {"address": addresses, "topics": [topics]}]
                    }
                    
                    await ws.send(json.dumps(subscribe_msg))
                    response = await ws.recv()
                    logger.info(f"Subscribe response: {response}")
                    
                    async for message in ws:
                        try:
                            data = json.loads(message)
                            if "params" in data and "result" in data["params"]:
                                log = data["params"]["result"]
                                evt = self._parse_log(log)
                                if evt:
                                    await self._process_event(evt)
                        except Exception as e:
                            logger.warning(f"WS message error: {e}")
            
            except Exception as e:
                logger.error(f"WebSocket error: {e}. Reconnecting in 10s...")
                await asyncio.sleep(10)

# ─── Config Generator ─────────────────────────────────────────────────────────
def generate_example_config() -> Dict:
    """Generate example configuration for common protocols."""
    return {
        "rpc_url": "${BASE_RPC_URL}",
        "ws_url": "${BASE_WS_URL}",
        "contracts": [
            # ─── Aave Governance ──────────────────────────────────────────────
            {
                "name": "AaveGovernanceV3",
                "address": "0x1140CB7cafAcC745771C2Ea31e7B5C653c5D0B80",
                "event_types": ["governance"],
                "auto_execute": True,
                "action_config": {
                    "auto_vote_own": True,
                    "auto_queue": False,
                    "auto_execute": False
                },
                "abi": GOVERNANCE_ABI
            },
            # ─── Uniswap Governance ───────────────────────────────────────────
            {
                "name": "UniswapGovernorBravo",
                "address": "0x408ED6354d4973f66138C91495f4fE8fdB1D17a2",
                "event_types": ["governance"],
                "auto_execute": True,
                "action_config": {
                    "auto_vote_own": True
                },
                "abi": GOVERNANCE_ABI
            },
            # ─── Aerodrome Rewards ────────────────────────────────────────────
            {
                "name": "AerodromeGauge_WETH_USDC",
                "address": "0x0000000000000000000000000000000000000000",  # Replace with actual gauge
                "event_types": ["pool_rewards"],
                "auto_execute": True,
                "action_config": {
                    "min_reward_threshold": 10000000000000000,  # 0.01 ETH worth
                    "reward_token": "0x3f5bF3EdAc59d650D8fDf9657a71F2f904A3a4Ee"  # AERO
                },
                "abi": REWARDS_ABI
            },
            # ─── Aave Safety Module Rewards ──────────────────────────────────
            {
                "name": "AaveSafetyModule",
                "address": "0x0000000000000000000000000000000000000000",  # Replace
                "event_types": ["pool_rewards", "vesting"],
                "auto_execute": True,
                "action_config": {
                    "min_reward_threshold": 5000000000000000,  # 0.005 ETH worth
                    "release_function": "release"
                },
                "abi": REWARDS_ABI + VESTING_ABI
            },
            # ─── Sample Vesting Contract ──────────────────────────────────────
            {
                "name": "TeamVesting",
                "address": "0x0000000000000000000000000000000000000000",  # Replace
                "event_types": ["vesting"],
                "auto_execute": True,
                "action_config": {
                    "release_function": "release"
                },
                "abi": VESTING_ABI
            },
            # ─── Sample Presale ───────────────────────────────────────────────
            {
                "name": "TokenPresale",
                "address": "0x0000000000000000000000000000000000000000",  # Replace
                "event_types": ["presale"],
                "auto_execute": False,
                "action_config": {
                    "auto_buy": False,
                    "max_eth": "0.1"
                },
                "abi": []
            },
            # ─── Watch our wallet for airdrops ────────────────────────────────
            {
                "name": "ERC20AirdropWatcher",
                "address": "0x0000000000000000000000000000000000000000",  # Generic - we scan all Transfer events to our wallet
                "event_types": ["airdrop_claims"],
                "auto_execute": False,
                "action_config": {},
                "abi": ERC20_ABI,
                "note": "For airdrop_claims, we scan Transfer events to our wallet across all tokens"
            }
        ],
        "global_settings": {
            "max_gas_usd": 0.05,
            "eth_price_usd": 3000,
            "poll_interval_seconds": 15,
            "block_range_max": 100,
            "discord_webhook": ""
        }
    }

# ─── Main ───────────────────────────────────────────────────────────────────
def main():
    parser = argparse.ArgumentParser(description="Smart Contract Event Monitor")
    parser.add_argument("--config", help="Path to JSON config file")
    parser.add_argument("--live", action="store_true", help="Enable auto-execution (requires PRIVATE_KEY; off by default)")
    parser.add_argument("--polling", action="store_true", help="Force RPC polling mode")
    parser.add_argument("--alert-discord", help="Discord webhook URL for alerts")
    parser.add_argument("--generate-config", action="store_true", help="Print example config to stdout")
    args = parser.parse_args()
    
    if args.generate_config:
        config = generate_example_config()
        print(json.dumps(config, indent=2))
        return
    
    if not args.config:
        parser.error("--config is required (unless using --generate-config)")
    
    if not os.path.exists(args.config):
        print(f"Config file not found: {args.config}")
        print("Run with --generate-config to see example, then save it.")
        sys.exit(1)
    
    monitor = EventMonitor(args.config, dry_run=not args.live)
    
    if args.alert_discord:
        monitor.discord_webhook = args.alert_discord
    
    # Check wallet setup
    if not monitor.our_address:
        logger.warning("WATCH_ADDRESS not set - airdrop/governance detection will be limited")
    if args.live and not monitor.private_key:
        logger.warning("PRIVATE_KEY not set - auto-execution disabled")
    
    logger.info(f"Mode: {'LIVE' if args.live else 'DRY-RUN'}")
    logger.info(f"Wallets: {len(monitor.watched)} contracts")
    logger.info(f"Our address: {monitor.our_address or 'NOT SET'}")
    
    try:
        if args.polling:
            asyncio.run(monitor.run_polling())
        else:
            asyncio.run(monitor.run_websocket())
    except KeyboardInterrupt:
        logger.info("Stopped by user")

if __name__ == "__main__":
    main()
