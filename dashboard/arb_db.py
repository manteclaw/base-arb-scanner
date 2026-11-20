"""SQLite database layer for arbitrage tracking."""
import sqlite3
import json
from datetime import datetime
from pathlib import Path
from typing import Optional

DB_PATH = Path(__file__).with_suffix('.db')


def _get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def init_db() -> None:
    """Create tables if they don't exist."""
    conn = _get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS spreads (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            dex1 TEXT NOT NULL,
            dex2 TEXT NOT NULL,
            token_pair TEXT NOT NULL,
            spread_pct REAL NOT NULL,
            timestamp REAL NOT NULL,
            amount_in REAL,
            gas_cost REAL,
            net_profit REAL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS executions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            tx_hash TEXT NOT NULL,
            status TEXT NOT NULL CHECK(status IN ('success','pending','failed')),
            profit_eth REAL,
            gas_used REAL,
            block_number INTEGER,
            timestamp REAL NOT NULL
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS opportunities (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            found_at REAL NOT NULL,
            executed_at REAL,
            spread_at_discovery REAL NOT NULL,
            actual_profit REAL,
            slippage REAL
        )
    """)

    # Indexes for fast queries
    cur.execute("CREATE INDEX IF NOT EXISTS idx_spreads_ts ON spreads(timestamp)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_spreads_pair ON spreads(token_pair)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_exec_ts ON executions(timestamp)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_opp_found ON opportunities(found_at)")

    conn.commit()
    conn.close()


def log_spread(dex1: str, dex2: str, token_pair: str, spread_pct: float,
                amount_in: Optional[float] = None,
                gas_cost: Optional[float] = None,
                net_profit: Optional[float] = None,
                timestamp: Optional[float] = None) -> int:
    """Log a spread observation. Returns the row id."""
    ts = timestamp or datetime.utcnow().timestamp()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO spreads (dex1, dex2, token_pair, spread_pct, timestamp, amount_in, gas_cost, net_profit)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
    """, (dex1, dex2, token_pair, spread_pct, ts, amount_in, gas_cost, net_profit))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def log_execution(tx_hash: str, status: str, profit_eth: Optional[float] = None,
                  gas_used: Optional[float] = None, block_number: Optional[int] = None,
                  timestamp: Optional[float] = None) -> int:
    """Log a transaction execution. Returns the row id."""
    ts = timestamp or datetime.utcnow().timestamp()
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO executions (tx_hash, status, profit_eth, gas_used, block_number, timestamp)
        VALUES (?, ?, ?, ?, ?, ?)
    """, (tx_hash, status, profit_eth, gas_used, block_number, ts))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def log_opportunity(found_at: float, spread_at_discovery: float,
                    executed_at: Optional[float] = None,
                    actual_profit: Optional[float] = None,
                    slippage: Optional[float] = None) -> int:
    """Log an arbitrage opportunity. Returns the row id."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO opportunities (found_at, executed_at, spread_at_discovery, actual_profit, slippage)
        VALUES (?, ?, ?, ?, ?)
    """, (found_at, executed_at, spread_at_discovery, actual_profit, slippage))
    row_id = cur.lastrowid
    conn.commit()
    conn.close()
    return row_id


def get_stats() -> dict:
    """Return aggregate statistics."""
    conn = _get_conn()
    cur = conn.cursor()

    # Spread stats
    cur.execute("SELECT COUNT(*) as total, AVG(spread_pct) as avg_spread, MAX(spread_pct) as max_spread FROM spreads")
    spread_row = cur.fetchone()

    # Execution stats
    cur.execute("""
        SELECT
            COUNT(*) as total_execs,
            SUM(CASE WHEN status='success' THEN 1 ELSE 0 END) as successes,
            SUM(CASE WHEN status='failed' THEN 1 ELSE 0 END) as failures,
            SUM(CASE WHEN status='pending' THEN 1 ELSE 0 END) as pending,
            SUM(profit_eth) as total_profit,
            AVG(profit_eth) as avg_profit,
            AVG(gas_used) as avg_gas
        FROM executions
    """)
    exec_row = cur.fetchone()

    # Opportunity stats
    cur.execute("""
        SELECT COUNT(*) as total_opps,
               SUM(CASE WHEN executed_at IS NOT NULL THEN 1 ELSE 0 END) as executed,
               AVG(actual_profit) as avg_actual_profit,
               AVG(slippage) as avg_slippage
        FROM opportunities
    """)
    opp_row = cur.fetchone()

    # Success rate
    total_execs = exec_row['total_execs'] or 0
    successes = exec_row['successes'] or 0
    success_rate = (successes / total_execs * 100) if total_execs > 0 else 0.0

    # Average spread by pair
    cur.execute("""
        SELECT token_pair, AVG(spread_pct) as avg_spread, COUNT(*) as count
        FROM spreads
        GROUP BY token_pair
        ORDER BY avg_spread DESC
    """)
    pair_spreads = [dict(r) for r in cur.fetchall()]

    conn.close()

    return {
        'spreads': {
            'total_logged': spread_row['total'] or 0,
            'avg_spread_pct': spread_row['avg_spread'] or 0.0,
            'max_spread_pct': spread_row['max_spread'] or 0.0,
        },
        'executions': {
            'total': total_execs,
            'successes': successes,
            'failures': exec_row['failures'] or 0,
            'pending': exec_row['pending'] or 0,
            'success_rate_pct': round(success_rate, 2),
            'total_profit_eth': exec_row['total_profit'] or 0.0,
            'avg_profit_eth': exec_row['avg_profit'] or 0.0,
            'avg_gas_used': exec_row['avg_gas'] or 0.0,
        },
        'opportunities': {
            'total_found': opp_row['total_opps'] or 0,
            'executed': opp_row['executed'] or 0,
            'avg_actual_profit': opp_row['avg_actual_profit'] or 0.0,
            'avg_slippage': opp_row['avg_slippage'] or 0.0,
        },
        'pair_spreads': pair_spreads,
    }


def get_recent_opportunities(limit: int = 50) -> list:
    """Return recent opportunities with spread info joined in."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM opportunities
        ORDER BY found_at DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_recent_spreads(limit: int = 50) -> list:
    """Return recent spread observations."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT * FROM spreads
        ORDER BY timestamp DESC
        LIMIT ?
    """, (limit,))
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_execution_scatter() -> list:
    """Return gas_cost vs profit data for scatter plot."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT gas_used, profit_eth, status
        FROM executions
        WHERE gas_used IS NOT NULL AND profit_eth IS NOT NULL
        ORDER BY timestamp DESC
        LIMIT 500
    """)
    rows = [dict(r) for r in cur.fetchall()]
    conn.close()
    return rows


def get_cumulative_profit() -> list:
    """Return cumulative profit over time."""
    conn = _get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT timestamp, profit_eth, status
        FROM executions
        WHERE status = 'success' AND profit_eth IS NOT NULL
        ORDER BY timestamp ASC
    """)
    rows = cur.fetchall()
    conn.close()

    cum = 0.0
    result = []
    for r in rows:
        cum += r['profit_eth']
        result.append({'timestamp': r['timestamp'], 'cumulative_profit': cum})
    return result


# Auto-init on import so the DB is ready
init_db()
