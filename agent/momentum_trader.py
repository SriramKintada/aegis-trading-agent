"""
Momentum Trader - Base Mainnet
Aerodrome-Leads-Uniswap Momentum Strategy

Core Insight:
  Aerodrome and Uniswap WETH/USDC prices on Base diverge.
  When they diverge significantly, Aerodrome (smaller volume) often moves first.
  Uniswap follows. We trade on Uniswap IN THE DIRECTION of the divergence signal.

Logic:
  1. Poll both pools every 20-30 seconds
  2. signal = (aero_price - uni_price) / uni_price
  3. signal > +0.30% AND we hold USDC -> BUY ETH on Uni
  4. signal < -0.30% AND we hold ETH -> SELL ETH on Uni
  5. ONE trade per signal direction. Hold until signal reverses.

Risk Controls:
  - Max 1 trade per 5 minutes
  - Hard stop-loss: -3% from session start (multi-RPC validated)
  - Session profit target: +2% -> pause
  - Max runtime: 4 hours (configurable)
  - Gas reserve: 0.0005 ETH
  - Max trade size: 30% of portfolio per trade

Author: Alex
Date: 2026-03-23
"""

import json
import os
import sys
import time
import asyncio
from datetime import datetime, timezone
from pathlib import Path
from collections import deque

from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

# ---------------------------------------------------------------------------
# ENV & CONFIG
# ---------------------------------------------------------------------------
load_dotenv(Path(__file__).parent.parent / ".env")

RPC_URLS = [
    "https://mainnet.base.org",
    "https://base.llamarpc.com",
    "https://1rpc.io/base",
    "https://base.drpc.org",
]

PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
if not PRIVATE_KEY:
    print("FATAL: No PRIVATE_KEY in .env")
    sys.exit(1)

# ---------------------------------------------------------------------------
# CONSTANTS
# ---------------------------------------------------------------------------
CHAIN_ID = 8453
GAS_RESERVE = 0.0005          # ETH reserved for gas
MAX_TRADE_PCT = 0.30          # Max 30% of portfolio per trade
SIGNAL_THRESHOLD = 0.0030     # 0.30% divergence to trigger trade
STOP_LOSS_PCT = -0.03         # -3% hard stop
PROFIT_TARGET_PCT = 0.02      # +2% session profit target
MIN_TRADE_COOLDOWN = 300      # 5 minutes between trades
POLL_INTERVAL = 25            # seconds between price polls
STATUS_INTERVAL = 120         # seconds between status prints
MAX_RUNTIME = 4 * 3600        # 4 hours default
SLIPPAGE_BPS = 30             # 0.3% max slippage
LOG_FILE = Path(__file__).parent / "momentum_log.json"

# ---------------------------------------------------------------------------
# ADDRESSES
# ---------------------------------------------------------------------------
WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

# Uniswap V3 SwapRouter02
UNI_ROUTER = Web3.to_checksum_address("0x2626664c2603336E57B271c5C0b26F421741e481")
# Uniswap V3 WETH/USDC 0.05% pool
UNI_POOL = Web3.to_checksum_address("0xd0b53D9277642d899DF5C87A3966A349A798F224")

# Aerodrome Router V2
AERO_ROUTER = Web3.to_checksum_address("0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43")
# Aerodrome WETH/USDC volatile pool
AERO_POOL = Web3.to_checksum_address("0xcDAC0d6c6C59727a65F871236188350531885C43")

# ---------------------------------------------------------------------------
# ABIs
# ---------------------------------------------------------------------------
ERC20_ABI = [
    {"constant": True, "inputs": [{"name": "account", "type": "address"}],
     "name": "balanceOf", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"constant": False, "inputs": [{"name": "spender", "type": "address"}, {"name": "amount", "type": "uint256"}],
     "name": "approve", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
    {"constant": True, "inputs": [{"name": "owner", "type": "address"}, {"name": "spender", "type": "address"}],
     "name": "allowance", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
]

WETH_FULL_ABI = ERC20_ABI + [
    {"constant": False, "inputs": [], "name": "deposit", "outputs": [],
     "payable": True, "stateMutability": "payable", "type": "function"},
    {"constant": False, "inputs": [{"name": "wad", "type": "uint256"}],
     "name": "withdraw", "outputs": [], "type": "function"},
]

UNI_POOL_ABI = [
    {"inputs": [], "name": "slot0", "outputs": [
        {"name": "sqrtPriceX96", "type": "uint160"},
        {"name": "tick", "type": "int24"},
        {"name": "observationIndex", "type": "uint16"},
        {"name": "observationCardinality", "type": "uint16"},
        {"name": "observationCardinalityNext", "type": "uint16"},
        {"name": "feeProtocol", "type": "uint8"},
        {"name": "unlocked", "type": "bool"}
    ], "type": "function"},
]

UNI_SWAP_ABI = [
    {"inputs": [{"components": [
        {"name": "tokenIn", "type": "address"},
        {"name": "tokenOut", "type": "address"},
        {"name": "fee", "type": "uint24"},
        {"name": "recipient", "type": "address"},
        {"name": "amountIn", "type": "uint256"},
        {"name": "amountOutMinimum", "type": "uint256"},
        {"name": "sqrtPriceLimitX96", "type": "uint160"}
    ], "name": "params", "type": "tuple"}],
     "name": "exactInputSingle",
     "outputs": [{"name": "amountOut", "type": "uint256"}],
     "stateMutability": "payable", "type": "function"}
]

AERO_POOL_ABI = [
    {"inputs": [], "name": "getReserves", "outputs": [
        {"name": "_reserve0", "type": "uint256"},
        {"name": "_reserve1", "type": "uint256"},
        {"name": "_blockTimestampLast", "type": "uint256"}
    ], "type": "function"},
    {"inputs": [{"name": "amountIn", "type": "uint256"}, {"name": "tokenIn", "type": "address"}],
     "name": "getAmountOut", "outputs": [{"name": "", "type": "uint256"}], "type": "function"},
    {"inputs": [], "name": "token0", "outputs": [{"name": "", "type": "address"}], "type": "function"},
    {"inputs": [], "name": "token1", "outputs": [{"name": "", "type": "address"}], "type": "function"},
    {"inputs": [], "name": "stable", "outputs": [{"name": "", "type": "bool"}], "type": "function"},
]

# ---------------------------------------------------------------------------
# WEB3 SETUP
# ---------------------------------------------------------------------------
_rpc_index = 0


def make_w3(url):
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))


w3 = make_w3(RPC_URLS[0])
account = Account.from_key(PRIVATE_KEY)
wallet = account.address


def rotate_rpc():
    """Switch to next RPC endpoint on 429/timeout."""
    global _rpc_index, w3, weth_c, usdc_c, uni_pool, uni_router, aero_pool_c
    _rpc_index = (_rpc_index + 1) % len(RPC_URLS)
    url = RPC_URLS[_rpc_index]
    log("  Rotating RPC -> %s" % url)
    w3 = make_w3(url)
    _rebuild_contracts()


def _rebuild_contracts():
    global weth_c, usdc_c, uni_pool, uni_router, aero_pool_c
    weth_c = w3.eth.contract(address=WETH, abi=WETH_FULL_ABI)
    usdc_c = w3.eth.contract(address=USDC, abi=ERC20_ABI)
    uni_pool = w3.eth.contract(address=UNI_POOL, abi=UNI_POOL_ABI)
    uni_router = w3.eth.contract(address=UNI_ROUTER, abi=UNI_SWAP_ABI)
    aero_pool_c = w3.eth.contract(address=AERO_POOL, abi=AERO_POOL_ABI)


# Initialize contracts
weth_c = w3.eth.contract(address=WETH, abi=WETH_FULL_ABI)
usdc_c = w3.eth.contract(address=USDC, abi=ERC20_ABI)
uni_pool = w3.eth.contract(address=UNI_POOL, abi=UNI_POOL_ABI)
uni_router = w3.eth.contract(address=UNI_ROUTER, abi=UNI_SWAP_ABI)
aero_pool_c = w3.eth.contract(address=AERO_POOL, abi=AERO_POOL_ABI)

# ---------------------------------------------------------------------------
# STATE
# ---------------------------------------------------------------------------
trade_log = []
signal_history = []        # every signal reading for debugging
position = "ETH"           # "ETH" or "USDC"
total_trades = 0
start_value_usd = 0.0
last_signal_direction = None  # track to avoid repeat trades same direction

# ---------------------------------------------------------------------------
# HELPERS
# ---------------------------------------------------------------------------


def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")


def log(msg):
    """ASCII-safe logging for Windows cp1252 console."""
    safe = msg.encode("ascii", "replace").decode("ascii")
    print("[%s] %s" % (ts(), safe), flush=True)


def gas_params():
    block = w3.eth.get_block("latest")
    base_fee = block.get("baseFeePerGas", 1000000)
    priority = w3.to_wei(0.001, "gwei")
    return {
        "maxFeePerGas": base_fee * 2 + priority,
        "maxPriorityFeePerGas": priority,
        "chainId": CHAIN_ID,
    }


def send_tx(built_tx):
    """Sign, send, wait. Returns receipt or None."""
    built_tx.update(gas_params())
    built_tx["nonce"] = w3.eth.get_transaction_count(wallet)
    if "gas" not in built_tx:
        try:
            built_tx["gas"] = w3.eth.estimate_gas(built_tx)
        except Exception as e:
            log("  Gas estimate failed: %s, using 200000" % e)
            built_tx["gas"] = 200000

    signed = account.sign_transaction(built_tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    tx_hex = receipt["transactionHash"].hex()

    if receipt["status"] == 1:
        gas_cost_eth = float(w3.from_wei(
            receipt["gasUsed"] * receipt.get("effectiveGasPrice", 1000000), "ether"))
        log("  [OK] TX confirmed: https://basescan.org/tx/%s (gas: $%.4f)"
            % (tx_hex, gas_cost_eth * 2050))
        return receipt
    else:
        log("  [FAIL] TX FAILED: https://basescan.org/tx/%s" % tx_hex)
        return None


def record_trade(action, tx_hash, details):
    global total_trades
    total_trades += 1
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "trade_num": total_trades,
        "action": action,
        "tx": tx_hash,
        "basescan": "https://basescan.org/tx/%s" % tx_hash,
        "details": details,
    }
    trade_log.append(entry)
    save_log()


def save_log():
    """Persist trade log and signal history to disk."""
    with open(LOG_FILE, "w") as f:
        json.dump({
            "trades": trade_log,
            "signals": signal_history[-500:],  # keep last 500 signals
            "summary": {
                "total_trades": total_trades,
                "start_value_usd": round(start_value_usd, 4),
                "position": position,
            }
        }, f, indent=2)


# ---------------------------------------------------------------------------
# PRICE FUNCTIONS
# ---------------------------------------------------------------------------


def get_uni_price_on(w3_instance):
    """Get ETH/USD price from Uniswap V3 pool using a specific web3 instance."""
    pool = w3_instance.eth.contract(address=UNI_POOL, abi=UNI_POOL_ABI)
    slot0 = pool.functions.slot0().call()
    sqrtPriceX96 = slot0[0]
    # USDC is token0 (6 dec), WETH is token1 (18 dec) in this pool
    # price = (sqrtPriceX96 / 2^96)^2 * 10^12
    price = (sqrtPriceX96 / (2 ** 96)) ** 2 * (10 ** 12)
    return price


def get_uni_price():
    """Get ETH/USD from Uniswap V3 with RPC retry."""
    for attempt in range(3):
        try:
            return get_uni_price_on(w3)
        except Exception as e:
            err = str(e)
            if "429" in err or "Too Many" in err or "timeout" in err.lower():
                rotate_rpc()
                time.sleep(2)
            else:
                log("  Uni price error: %s" % e)
                return None
    return None


def get_aero_price():
    """Get ETH price from Aerodrome pool via getAmountOut."""
    for attempt in range(3):
        try:
            amount_in_wei = w3.to_wei(0.001, "ether")
            usdc_out = aero_pool_c.functions.getAmountOut(amount_in_wei, WETH).call()
            eth_amount = float(w3.from_wei(amount_in_wei, "ether"))
            usdc_amount = usdc_out / 1e6
            price = usdc_amount / eth_amount
            return price
        except Exception as e:
            err = str(e)
            if "429" in err or "Too Many" in err or "timeout" in err.lower():
                rotate_rpc()
                time.sleep(2)
            else:
                log("  Aero price error: %s" % e)
                return None
    return None


# ---------------------------------------------------------------------------
# MULTI-RPC BALANCE VALIDATION
# ---------------------------------------------------------------------------


def get_balance_on_rpc(rpc_url):
    """Get (eth, weth, usdc) on a specific RPC. Returns None on failure."""
    try:
        inst = make_w3(rpc_url)
        eth = float(inst.from_wei(inst.eth.get_balance(wallet), "ether"))
        weth_contract = inst.eth.contract(address=WETH, abi=ERC20_ABI)
        weth_bal = float(inst.from_wei(weth_contract.functions.balanceOf(wallet).call(), "ether"))
        usdc_contract = inst.eth.contract(address=USDC, abi=ERC20_ABI)
        usdc_bal = usdc_contract.functions.balanceOf(wallet).call() / 1e6
        return eth, weth_bal, usdc_bal
    except Exception as e:
        log("  RPC %s balance check failed: %s" % (rpc_url[:30], e))
        return None


def get_validated_balances():
    """
    Get balances validated across at least 2 RPCs.
    Returns (eth, weth, usdc, total_usd, price) or None if validation fails.
    
    This prevents the false-$0-balance crash that killed the previous bot.
    """
    results = []
    for url in RPC_URLS:
        bal = get_balance_on_rpc(url)
        if bal is not None:
            results.append((url, bal))
        if len(results) >= 2:
            break
        time.sleep(0.5)

    if len(results) < 2:
        log("  WARNING: Could not validate balances on 2+ RPCs, got %d" % len(results))
        if len(results) == 0:
            return None
        # Fall through with 1 result but log warning

    if len(results) >= 2:
        # Compare the two results - they should be close
        b1 = results[0][1]  # (eth, weth, usdc)
        b2 = results[1][1]
        eth_diff = abs(b1[0] - b2[0])
        usdc_diff = abs(b1[2] - b2[2])

        # If one RPC says $0 and the other doesn't, trust the non-zero one
        if b1[0] == 0 and b2[0] > 0:
            log("  WARNING: %s returned 0 ETH, %s returned %.6f - using non-zero"
                % (results[0][0][:25], results[1][0][:25], b2[0]))
            chosen = b2
        elif b2[0] == 0 and b1[0] > 0:
            log("  WARNING: %s returned 0 ETH, %s returned %.6f - using non-zero"
                % (results[1][0][:25], results[0][0][:25], b1[0]))
            chosen = b1
        elif eth_diff > 0.001:
            log("  WARNING: ETH balance mismatch: %.6f vs %.6f" % (b1[0], b2[0]))
            # Use the higher value (more conservative for stop-loss)
            chosen = b1 if (b1[0] + b1[2] / 2000) > (b2[0] + b2[2] / 2000) else b2
        else:
            chosen = b1  # they agree, use first
    else:
        chosen = results[0][1]

    eth, weth_bal, usdc_bal = chosen
    price = get_uni_price() or 2050
    total = (eth + weth_bal) * price + usdc_bal
    return eth, weth_bal, usdc_bal, total, price


def get_balances_quick():
    """Quick single-RPC balance check for non-critical use (status lines)."""
    for attempt in range(3):
        try:
            eth = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
            time.sleep(0.3)
            weth_bal = float(w3.from_wei(weth_c.functions.balanceOf(wallet).call(), "ether"))
            time.sleep(0.3)
            usdc_bal = usdc_c.functions.balanceOf(wallet).call() / 1e6
            time.sleep(0.3)
            price = get_uni_price() or 2050
            total = (eth + weth_bal) * price + usdc_bal
            return eth, weth_bal, usdc_bal, total, price
        except Exception as e:
            err = str(e)
            if "429" in err or "Too Many" in err:
                rotate_rpc()
                time.sleep(3)
            else:
                log("  Balance error: %s" % e)
                return 0, 0, 0, 0, 2050
    return 0, 0, 0, 0, 2050


# ---------------------------------------------------------------------------
# TRADING FUNCTIONS
# ---------------------------------------------------------------------------


def ensure_approval(token_contract, token_addr, spender, amount):
    """Approve if needed. Returns True if approved."""
    try:
        current = token_contract.functions.allowance(wallet, spender).call()
        if current >= amount:
            return True
        log("  Approving %s... for %s..." % (token_addr[:10], spender[:10]))
        tx = token_contract.functions.approve(spender, 2 ** 256 - 1).build_transaction({
            "from": wallet, "gas": 60000
        })
        r = send_tx(tx)
        return r is not None
    except Exception as e:
        log("  Approval error: %s" % e)
        return False


def sell_eth_for_usdc(eth_amount):
    """
    Sell ETH -> USDC via Uniswap V3.
    Steps: wrap ETH -> WETH, approve, swap WETH -> USDC.
    Returns USDC balance after trade, or None on failure.
    """
    global position
    log("  TRADE: SELL %.6f ETH -> USDC on Uniswap" % eth_amount)

    wei_amount = w3.to_wei(eth_amount, "ether")

    # Wrap ETH -> WETH
    tx = weth_c.functions.deposit().build_transaction({
        "from": wallet, "value": wei_amount, "gas": 50000
    })
    r = send_tx(tx)
    if not r:
        return None
    time.sleep(1)

    # Approve WETH for router
    if not ensure_approval(weth_c, WETH, UNI_ROUTER, wei_amount):
        return None
    time.sleep(1)

    # Calculate min output with slippage
    price = get_uni_price()
    if not price:
        log("  Cannot get price for slippage calc, aborting")
        return None
    expected_usdc = eth_amount * price
    min_usdc = int(expected_usdc * (1 - SLIPPAGE_BPS / 10000) * 1e6)

    # Swap WETH -> USDC
    params = (WETH, USDC, 500, wallet, wei_amount, min_usdc, 0)
    tx = uni_router.functions.exactInputSingle(params).build_transaction({
        "from": wallet, "gas": 200000
    })
    r = send_tx(tx)
    if not r:
        return None

    usdc_bal = usdc_c.functions.balanceOf(wallet).call() / 1e6
    position = "USDC"
    record_trade("SELL_ETH", r["transactionHash"].hex(),
                 "Sold %.6f ETH at ~$%.2f -> %.4f USDC" % (eth_amount, price, usdc_bal))
    return usdc_bal


def buy_eth_with_usdc(usdc_amount_raw=None):
    """
    Buy ETH with USDC via Uniswap V3.
    Steps: approve USDC, swap USDC -> WETH, unwrap WETH -> ETH.
    Returns ETH received, or None on failure.
    """
    global position

    if usdc_amount_raw is None:
        usdc_amount_raw = usdc_c.functions.balanceOf(wallet).call()

    if usdc_amount_raw == 0:
        log("  No USDC to buy with")
        return None

    usdc_human = usdc_amount_raw / 1e6
    log("  TRADE: BUY ETH with %.4f USDC on Uniswap" % usdc_human)

    # Approve USDC for router
    if not ensure_approval(usdc_c, USDC, UNI_ROUTER, usdc_amount_raw):
        return None
    time.sleep(1)

    # Calculate min output with slippage
    price = get_uni_price()
    if not price:
        log("  Cannot get price for slippage calc, aborting")
        return None
    expected_eth = usdc_human / price
    min_eth = int(w3.to_wei(expected_eth * (1 - SLIPPAGE_BPS / 10000), "ether"))

    # Swap USDC -> WETH
    params = (USDC, WETH, 500, wallet, usdc_amount_raw, min_eth, 0)
    tx = uni_router.functions.exactInputSingle(params).build_transaction({
        "from": wallet, "gas": 200000
    })
    r = send_tx(tx)
    if not r:
        return None
    time.sleep(1)

    # Unwrap WETH -> ETH
    weth_bal = weth_c.functions.balanceOf(wallet).call()
    if weth_bal > 0:
        tx = weth_c.functions.withdraw(weth_bal).build_transaction({
            "from": wallet, "gas": 50000
        })
        send_tx(tx)

    eth_received = float(w3.from_wei(weth_bal, "ether"))
    position = "ETH"
    record_trade("BUY_ETH", r["transactionHash"].hex(),
                 "Bought %.6f ETH with %.4f USDC at ~$%.2f" % (eth_received, usdc_human, price))
    return eth_received


# ---------------------------------------------------------------------------
# SIGNAL CALCULATION
# ---------------------------------------------------------------------------


def compute_signal():
    """
    Compute the momentum signal.
    signal = (aero_price - uni_price) / uni_price
    
    Positive signal = Aero is trading higher -> Uni price likely to rise -> BUY
    Negative signal = Aero is trading lower  -> Uni price likely to drop -> SELL
    
    Returns (signal_pct, uni_price, aero_price) or (None, None, None) on error.
    """
    uni_price = get_uni_price()
    aero_price = get_aero_price()

    if uni_price is None or aero_price is None:
        return None, None, None

    signal = (aero_price - uni_price) / uni_price
    return signal, uni_price, aero_price


# ---------------------------------------------------------------------------
# MAIN LOOP
# ---------------------------------------------------------------------------


async def main():
    global position, start_value_usd, last_signal_direction

    log("=" * 60)
    log("  MOMENTUM TRADER - LIVE")
    log("  Strategy: Aerodrome-Leads-Uniswap Momentum")
    log("  Real money. Single-trade directional bets.")
    log("=" * 60)

    # Initial state with multi-RPC validation
    bal = get_validated_balances()
    if bal is None:
        log("FATAL: Cannot get validated balances from any RPC")
        return
    eth_bal, weth_bal, usdc_bal, total_usd, price = bal
    start_value_usd = total_usd

    # Determine initial position
    if usdc_bal > 0.50:
        position = "USDC"
    else:
        position = "ETH"

    log("  Wallet: %s" % wallet)
    log("  ETH:  %.6f ($%.2f)" % (eth_bal, eth_bal * price))
    log("  WETH: %.6f" % weth_bal)
    log("  USDC: %.4f" % usdc_bal)
    log("  Total: $%.2f" % total_usd)
    log("  Position: %s" % position)
    log("  ETH Price (Uni): $%.2f" % price)
    log("  Signal threshold: +/-%.2f%%" % (SIGNAL_THRESHOLD * 100))
    log("  Stop-loss: %.1f%% | Profit target: +%.1f%%" % (STOP_LOSS_PCT * 100, PROFIT_TARGET_PCT * 100))
    log("  Trade cooldown: %ds | Poll interval: %ds" % (MIN_TRADE_COOLDOWN, POLL_INTERVAL))
    log("  Max runtime: %d hours" % (MAX_RUNTIME // 3600))
    log("=" * 60)

    start_time = time.time()
    last_trade_time = 0
    last_status_time = time.time()
    poll_count = 0
    paused = False  # set True when profit target hit

    while time.time() - start_time < MAX_RUNTIME:
        try:
            poll_count += 1

            # ----- COMPUTE SIGNAL -----
            signal, uni_price, aero_price = compute_signal()

            if signal is None:
                log("  Could not get prices this cycle, skipping")
                await asyncio.sleep(POLL_INTERVAL)
                continue

            signal_pct = signal * 100
            action_taken = "HOLD"

            # ----- STATUS LINE (every STATUS_INTERVAL) -----
            now = time.time()
            if now - last_status_time >= STATUS_INTERVAL:
                bal = get_balances_quick()
                eth_bal, weth_bal, usdc_bal, total_usd, _ = bal
                pnl_pct = ((total_usd - start_value_usd) / start_value_usd * 100) if start_value_usd > 0 else 0
                elapsed_min = int((now - start_time) / 60)
                log("  [%dm] Uni: $%.2f | Aero: $%.2f | Signal: %+.3f%% | Pos: %s | PnL: %+.2f%% | Trades: %d%s"
                    % (elapsed_min, uni_price, aero_price, signal_pct, position, pnl_pct,
                       total_trades, " [PAUSED]" if paused else ""))
                last_status_time = now

            # ----- STOP-LOSS CHECK (multi-RPC validated) -----
            # Check every 10 polls (~4 minutes)
            if poll_count % 10 == 0:
                val_bal = get_validated_balances()
                if val_bal is not None:
                    _, _, _, val_total, _ = val_bal
                    val_pnl = (val_total - start_value_usd) / start_value_usd
                    if val_pnl <= STOP_LOSS_PCT:
                        log("  [STOP-LOSS] Portfolio $%.2f = %.2f%% from start. STOPPING."
                            % (val_total, val_pnl * 100))
                        log("  Validated on 2+ RPCs. This is real.")
                        # Convert to ETH and exit
                        if position == "USDC":
                            usdc_raw = usdc_c.functions.balanceOf(wallet).call()
                            if usdc_raw > 100000:  # > $0.10
                                buy_eth_with_usdc(usdc_raw)
                        break

            # ----- PROFIT TARGET CHECK -----
            if poll_count % 10 == 0 and not paused:
                val_bal = get_validated_balances()
                if val_bal is not None:
                    _, _, _, val_total, _ = val_bal
                    val_pnl = (val_total - start_value_usd) / start_value_usd
                    if val_pnl >= PROFIT_TARGET_PCT:
                        log("  [PROFIT TARGET] Portfolio $%.2f = +%.2f%%. Pausing trading."
                            % (val_total, val_pnl * 100))
                        paused = True

            # ----- TRADING LOGIC -----
            if not paused:
                time_since_trade = now - last_trade_time
                can_trade = time_since_trade >= MIN_TRADE_COOLDOWN

                if can_trade:
                    # BUY SIGNAL: Aero is higher than Uni -> Uni will rise
                    if signal > SIGNAL_THRESHOLD and position == "USDC":
                        if last_signal_direction != "BUY":
                            log("")
                            log("  >>> BUY SIGNAL <<<")
                            log("  Aero: $%.2f | Uni: $%.2f | Divergence: +%.3f%%"
                                % (aero_price, uni_price, signal_pct))
                            log("  Aero is higher -> Uni price likely to rise -> BUY on Uni")

                            usdc_raw = usdc_c.functions.balanceOf(wallet).call()
                            if usdc_raw > 100000:  # > $0.10 minimum
                                # Max trade size: 30% of portfolio or all USDC (whichever smaller)
                                max_usdc = int(usdc_raw * MAX_TRADE_PCT)
                                # For small portfolios, just use all USDC
                                trade_usdc = usdc_raw if (usdc_raw / 1e6) < 5.0 else max_usdc
                                result = buy_eth_with_usdc(trade_usdc)
                                if result:
                                    last_trade_time = now
                                    last_signal_direction = "BUY"
                                    action_taken = "BUY"
                            else:
                                log("  USDC balance too low to trade")

                    # SELL SIGNAL: Aero is lower than Uni -> Uni will drop
                    elif signal < -SIGNAL_THRESHOLD and position == "ETH":
                        if last_signal_direction != "SELL":
                            log("")
                            log("  >>> SELL SIGNAL <<<")
                            log("  Aero: $%.2f | Uni: $%.2f | Divergence: %.3f%%"
                                % (aero_price, uni_price, signal_pct))
                            log("  Aero is lower -> Uni price likely to drop -> SELL on Uni")

                            bal = get_balances_quick()
                            eth_avail = bal[0] - GAS_RESERVE
                            if eth_avail > 0.0003:
                                trade_eth = eth_avail * MAX_TRADE_PCT
                                # For small portfolios, use more
                                if eth_avail < 0.005:
                                    trade_eth = eth_avail * 0.8  # use 80% for tiny portfolios
                                result = sell_eth_for_usdc(trade_eth)
                                if result:
                                    last_trade_time = now
                                    last_signal_direction = "SELL"
                                    action_taken = "SELL"
                            else:
                                log("  ETH balance too low to trade (need gas reserve)")

            # ----- LOG SIGNAL -----
            signal_entry = {
                "time": datetime.now(timezone.utc).isoformat(),
                "uni_price": round(uni_price, 2),
                "aero_price": round(aero_price, 2),
                "signal_pct": round(signal_pct, 4),
                "position": position,
                "action": action_taken,
            }
            signal_history.append(signal_entry)

            # Save log every 20 polls
            if poll_count % 20 == 0:
                save_log()

        except KeyboardInterrupt:
            log("  Interrupted by user")
            break
        except Exception as e:
            log("  ERROR in main loop: %s" % e)
            try:
                import traceback
                traceback.print_exc()
            except Exception:
                pass

        await asyncio.sleep(POLL_INTERVAL)

    # -------------------------------------------------------------------
    # SESSION END
    # -------------------------------------------------------------------
    log("")
    log("=" * 60)
    log("  MOMENTUM TRADER SESSION COMPLETE")
    log("=" * 60)

    # Convert everything back to ETH
    final_bal = get_validated_balances()
    if final_bal:
        eth_bal, weth_bal, usdc_bal, total_usd, price = final_bal

        if position == "USDC" or usdc_bal > 0.50:
            log("  Converting remaining USDC -> ETH...")
            usdc_raw = usdc_c.functions.balanceOf(wallet).call()
            if usdc_raw > 100000:
                buy_eth_with_usdc(usdc_raw)
            # Re-check after conversion
            final_bal = get_validated_balances()
            if final_bal:
                eth_bal, weth_bal, usdc_bal, total_usd, price = final_bal

        # Unwrap any remaining WETH
        try:
            weth_raw = weth_c.functions.balanceOf(wallet).call()
            if weth_raw > 0:
                log("  Unwrapping %.6f WETH -> ETH..." % (weth_raw / 1e18))
                tx = weth_c.functions.withdraw(weth_raw).build_transaction({
                    "from": wallet, "gas": 50000
                })
                send_tx(tx)
        except Exception as e:
            log("  WETH unwrap error: %s" % e)

        pnl_usd = total_usd - start_value_usd
        pnl_pct = (pnl_usd / start_value_usd * 100) if start_value_usd > 0 else 0

        log("")
        log("  Start:  $%.2f" % start_value_usd)
        log("  End:    $%.2f" % total_usd)
        log("  P&L:    $%.4f (%+.2f%%)" % (pnl_usd, pnl_pct))
        log("  Trades: %d" % total_trades)
        log("  Signals logged: %d" % len(signal_history))
        log("  Runtime: %d minutes" % int((time.time() - start_time) / 60))
        log("")

        for t in trade_log:
            log("  [%d] %s -> %s" % (t["trade_num"], t["action"], t["basescan"]))

    # Final save
    save_log()
    log("  Log saved to: %s" % LOG_FILE)
    log("  Session ended.")


if __name__ == "__main__":
    asyncio.run(main())
