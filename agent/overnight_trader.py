"""
AEGIS Overnight Trader ? Base Mainnet
Real money. Real stakes. No backtests.

Strategy:
  1. Cross-DEX Arb: Uniswap V3 vs Aerodrome price comparison
  2. Micro-scalp: Range trading on ETH/USDC with tight bands
  
Safety:
  - HARD STOP if wallet value < $4.50
  - Max 30% of ETH in any single trade
  - Always keep gas reserve (0.0005 ETH)
  - All trades logged with TxIDs

Author: Alex (overnight autonomous session)
Date: 2026-03-23
"""

import json
import os
import sys
import time
import math
import asyncio
import aiohttp
from datetime import datetime, timezone
from pathlib import Path
from collections import deque

from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

# ?? Load env ???????????????????????????????????????????????????????????
load_dotenv(Path(__file__).parent.parent / ".env")

# Multiple RPCs for resilience against rate limits
RPC_URLS = [
    "https://mainnet.base.org",
    "https://base.llamarpc.com",
    "https://1rpc.io/base",
    "https://base.drpc.org",
]
RPC_URL = os.getenv("RPC_URL", RPC_URLS[0])
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
if not PRIVATE_KEY:
    print("FATAL: No PRIVATE_KEY in .env")
    sys.exit(1)

# ?? Constants ??????????????????????????????????????????????????????????
CHAIN_ID = 8453
GAS_RESERVE = 0.0005  # ETH reserved for gas (never trade this)
STOP_LOSS_USD = 4.50   # HARD STOP ? halt all trading
MAX_TRADE_PCT = 0.30   # Max 30% of portfolio per trade
CHECK_INTERVAL = 45    # seconds between checks (avoid RPC rate limits)
MAX_RUNTIME = 6 * 3600 # 6 hours
SLIPPAGE_BPS = 30      # 0.3% max slippage tolerance on swaps
LOG_FILE = Path(__file__).parent / "overnight_log.json"

# ?? Addresses ??????????????????????????????????????????????????????????
WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

# Uniswap V3 SwapRouter02
UNI_ROUTER = Web3.to_checksum_address("0x2626664c2603336E57B271c5C0b26F421741e481")
# Uniswap V3 WETH/USDC 0.05% pool
UNI_POOL = Web3.to_checksum_address("0xd0b53D9277642d899DF5C87A3966A349A798F224")

# Aerodrome Router V2
AERO_ROUTER = Web3.to_checksum_address("0xcF77a3Ba9A5CA399B7c97c74d54e5b1Beb874E43")
# Aerodrome WETH/USDC volatile pool (native USDC - verified)
AERO_POOL = Web3.to_checksum_address("0xcDAC0d6c6C59727a65F871236188350531885C43")

# ?? ABIs ???????????????????????????????????????????????????????????????
ERC20_ABI = [
    {"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":False,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},
    {"constant":True,"inputs":[{"name":"owner","type":"address"},{"name":"spender","type":"address"}],"name":"allowance","outputs":[{"name":"","type":"uint256"}],"type":"function"},
]

WETH_FULL_ABI = ERC20_ABI + [
    {"constant":False,"inputs":[],"name":"deposit","outputs":[],"payable":True,"stateMutability":"payable","type":"function"},
    {"constant":False,"inputs":[{"name":"wad","type":"uint256"}],"name":"withdraw","outputs":[],"type":"function"},
]

UNI_POOL_ABI = [
    {"inputs":[],"name":"slot0","outputs":[
        {"name":"sqrtPriceX96","type":"uint160"},
        {"name":"tick","type":"int24"},
        {"name":"observationIndex","type":"uint16"},
        {"name":"observationCardinality","type":"uint16"},
        {"name":"observationCardinalityNext","type":"uint16"},
        {"name":"feeProtocol","type":"uint8"},
        {"name":"unlocked","type":"bool"}
    ],"type":"function"},
    {"inputs":[],"name":"fee","outputs":[{"name":"","type":"uint24"}],"type":"function"},
    {"inputs":[],"name":"liquidity","outputs":[{"name":"","type":"uint128"}],"type":"function"},
]

UNI_SWAP_ABI = [
    {"inputs":[{"components":[
        {"name":"tokenIn","type":"address"},
        {"name":"tokenOut","type":"address"},
        {"name":"fee","type":"uint24"},
        {"name":"recipient","type":"address"},
        {"name":"amountIn","type":"uint256"},
        {"name":"amountOutMinimum","type":"uint256"},
        {"name":"sqrtPriceLimitX96","type":"uint160"}
    ],"name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}
]

# Aerodrome pool ABI (for getAmountOut / getReserves)
AERO_POOL_ABI = [
    {"inputs":[],"name":"getReserves","outputs":[
        {"name":"_reserve0","type":"uint256"},
        {"name":"_reserve1","type":"uint256"},
        {"name":"_blockTimestampLast","type":"uint256"}
    ],"type":"function"},
    {"inputs":[{"name":"amountIn","type":"uint256"},{"name":"tokenIn","type":"address"}],"name":"getAmountOut","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"inputs":[],"name":"token0","outputs":[{"name":"","type":"address"}],"type":"function"},
    {"inputs":[],"name":"token1","outputs":[{"name":"","type":"address"}],"type":"function"},
    {"inputs":[],"name":"stable","outputs":[{"name":"","type":"bool"}],"type":"function"},
]

AERO_ROUTER_ABI = [
    {"inputs":[
        {"name":"amountIn","type":"uint256"},
        {"name":"amountOutMin","type":"uint256"},
        {"components":[
            {"name":"from","type":"address"},
            {"name":"to","type":"address"},
            {"name":"stable","type":"bool"},
            {"name":"factory","type":"address"}
        ],"name":"routes","type":"tuple[]"},
        {"name":"to","type":"address"},
        {"name":"deadline","type":"uint256"}
    ],"name":"swapExactTokensForTokens","outputs":[{"name":"amounts","type":"uint256[]"}],"type":"function"},
    {"inputs":[
        {"name":"amountOutMin","type":"uint256"},
        {"components":[
            {"name":"from","type":"address"},
            {"name":"to","type":"address"},
            {"name":"stable","type":"bool"},
            {"name":"factory","type":"address"}
        ],"name":"routes","type":"tuple[]"},
        {"name":"to","type":"address"},
        {"name":"deadline","type":"uint256"}
    ],"name":"swapExactETHForTokens","outputs":[{"name":"amounts","type":"uint256[]"}],"stateMutability":"payable","type":"function"},
    {"inputs":[
        {"name":"amountIn","type":"uint256"},
        {"name":"amountOutMin","type":"uint256"},
        {"components":[
            {"name":"from","type":"address"},
            {"name":"to","type":"address"},
            {"name":"stable","type":"bool"},
            {"name":"factory","type":"address"}
        ],"name":"routes","type":"tuple[]"},
        {"name":"to","type":"address"},
        {"name":"deadline","type":"uint256"}
    ],"name":"swapExactTokensForETH","outputs":[{"name":"amounts","type":"uint256[]"}],"type":"function"},
]

# -- Web3 Setup with RPC rotation --
_rpc_index = 0

def make_w3(url):
    return Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 10}))

w3 = make_w3(RPC_URLS[0])
account = Account.from_key(PRIVATE_KEY)
wallet = account.address

def rotate_rpc():
    """Switch to next RPC endpoint on 429/timeout."""
    global _rpc_index, w3, weth_c, usdc_c, uni_pool, uni_router
    _rpc_index = (_rpc_index + 1) % len(RPC_URLS)
    url = RPC_URLS[_rpc_index]
    log(f"  Rotating RPC -> {url}")
    w3 = make_w3(url)
    weth_c = w3.eth.contract(address=WETH, abi=WETH_FULL_ABI)
    usdc_c = w3.eth.contract(address=USDC, abi=ERC20_ABI)
    uni_pool = w3.eth.contract(address=UNI_POOL, abi=UNI_POOL_ABI)
    uni_router = w3.eth.contract(address=UNI_ROUTER, abi=UNI_SWAP_ABI)

weth_c = w3.eth.contract(address=WETH, abi=WETH_FULL_ABI)
usdc_c = w3.eth.contract(address=USDC, abi=ERC20_ABI)
uni_pool = w3.eth.contract(address=UNI_POOL, abi=UNI_POOL_ABI)
uni_router = w3.eth.contract(address=UNI_ROUTER, abi=UNI_SWAP_ABI)

# ?? State ??????????????????????????????????????????????????????????????
trade_log = []
price_history = deque(maxlen=120)  # last 60 minutes at 30s intervals
position = "ETH"  # "ETH" or "USDC" ? what we're currently holding
total_trades = 0
total_pnl = 0.0
start_value_usd = 0.0

# ?? Helpers ????????????????????????????????????????????????????????????
def ts():
    return datetime.now(timezone.utc).strftime("%H:%M:%S")

def log(msg):
    # Strip emoji for Windows cp1252 console
    safe = msg.encode('ascii', 'replace').decode('ascii')
    print(f"[{ts()}] {safe}", flush=True)

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
            log(f"  Gas estimate failed: {e}, using 200000")
            built_tx["gas"] = 200000
    
    signed = account.sign_transaction(built_tx)
    tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
    receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
    tx_hex = receipt["transactionHash"].hex()
    
    if receipt["status"] == 1:
        gas_used = receipt["gasUsed"]
        gas_cost_eth = float(w3.from_wei(receipt["gasUsed"] * receipt.get("effectiveGasPrice", 1000000), "ether"))
        log(f"  [OK] TX confirmed: https://basescan.org/tx/{tx_hex} (gas: ${gas_cost_eth * 2050:.4f})")
        return receipt
    else:
        log(f"  [FAIL] TX FAILED: https://basescan.org/tx/{tx_hex}")
        return None

def record_trade(action, tx_hash, details, pnl=0.0):
    global total_trades, total_pnl
    total_trades += 1
    total_pnl += pnl
    entry = {
        "time": datetime.now(timezone.utc).isoformat(),
        "trade_num": total_trades,
        "action": action,
        "tx": tx_hash,
        "basescan": f"https://basescan.org/tx/{tx_hash}",
        "details": details,
        "pnl_usd": round(pnl, 6),
        "cumulative_pnl": round(total_pnl, 6),
    }
    trade_log.append(entry)
    with open(LOG_FILE, "w") as f:
        json.dump({"trades": trade_log, "summary": get_summary()}, f, indent=2)

def get_summary():
    return {
        "total_trades": total_trades,
        "total_pnl_usd": round(total_pnl, 6),
        "start_value_usd": round(start_value_usd, 4),
        "runtime_started": trade_log[0]["time"] if trade_log else None,
    }

# ?? Price Functions ????????????????????????????????????????????????????
def get_uni_price():
    """Get ETH/USD price from Uniswap V3 WETH/USDC pool on-chain."""
    for attempt in range(3):
        try:
            slot0 = uni_pool.functions.slot0().call()
            sqrtPriceX96 = slot0[0]
            price = (sqrtPriceX96 / (2**96))**2 * (10**12)
            return price
        except Exception as e:
            err = str(e)
            if "429" in err or "Too Many" in err or "timeout" in err.lower():
                rotate_rpc()
                time.sleep(2)
            else:
                log(f"  Uni price error: {e}")
                return None
    return None

def get_aero_price(amount_in_wei=None):
    """Get ETH price from Aerodrome WETH/USDC pool via getAmountOut."""
    for attempt in range(3):
        try:
            aero_pool_c = w3.eth.contract(address=AERO_POOL, abi=AERO_POOL_ABI)
            if amount_in_wei is None:
                amount_in_wei = w3.to_wei(0.001, "ether")
            
            usdc_out = aero_pool_c.functions.getAmountOut(amount_in_wei, WETH).call()
            eth_amount = float(w3.from_wei(amount_in_wei, "ether"))
            usdc_amount = usdc_out / 1e6
            price = usdc_amount / eth_amount
            return price, usdc_out
        except Exception as e:
            err = str(e)
            if "429" in err or "Too Many" in err or "timeout" in err.lower():
                rotate_rpc()
                time.sleep(2)
            else:
                log(f"  Aero price error: {e}")
                return None, None
    return None, None

async def get_external_price():
    """Get ETH/USD from CryptoCompare as sanity check."""
    try:
        async with aiohttp.ClientSession() as s:
            async with s.get("https://min-api.cryptocompare.com/data/price", 
                           params={"fsym": "ETH", "tsyms": "USD"}, timeout=aiohttp.ClientTimeout(total=5)) as r:
                data = await r.json()
                return float(data.get("USD", 0))
    except:
        return None

# ?? Balance Functions ??????????????????????????????????????????????????
def get_balances():
    """Returns (eth_balance, weth_balance, usdc_balance, total_usd, price)."""
    for attempt in range(3):
        try:
            eth = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
            time.sleep(0.5)
            weth = float(w3.from_wei(weth_c.functions.balanceOf(wallet).call(), "ether"))
            time.sleep(0.5)
            usdc = usdc_c.functions.balanceOf(wallet).call() / 1e6
            time.sleep(0.5)
            price = get_uni_price() or 2050
            total = (eth + weth) * price + usdc
            return eth, weth, usdc, total, price
        except Exception as e:
            err = str(e)
            if "429" in err or "Too Many" in err:
                rotate_rpc()
                time.sleep(3)
            else:
                log(f"  Balance error: {e}")
                return 0, 0, 0, 0, 2050
    return 0, 0, 0, 0, 2050

# ?? Trading Functions ??????????????????????????????????????????????????
def ensure_approval(token_contract, token_addr, spender, amount):
    """Approve if needed. Returns True if approved."""
    try:
        current = token_contract.functions.allowance(wallet, spender).call()
        if current >= amount:
            return True
        log(f"  Approving {token_addr[:10]}... for {spender[:10]}...")
        tx = token_contract.functions.approve(spender, 2**256 - 1).build_transaction({
            "from": wallet, "gas": 60000
        })
        r = send_tx(tx)
        return r is not None
    except Exception as e:
        log(f"  Approval error: {e}")
        return False

def sell_eth_for_usdc(eth_amount):
    """Sell ETH for USDC via Uniswap V3. Returns USDC received or None."""
    global position
    log(f"  SELL {eth_amount:.6f} ETH -> USDC")
    
    wei_amount = w3.to_wei(eth_amount, "ether")
    
    # First wrap ETH -> WETH
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
    
    # Get expected output for slippage calc
    price = get_uni_price()
    expected_usdc = eth_amount * price
    min_usdc = int(expected_usdc * (1 - SLIPPAGE_BPS/10000) * 1e6)
    
    # Swap WETH -> USDC
    params = (WETH, USDC, 500, wallet, wei_amount, min_usdc, 0)
    tx = uni_router.functions.exactInputSingle(params).build_transaction({
        "from": wallet, "gas": 200000
    })
    r = send_tx(tx)
    if not r:
        return None
    
    # Check USDC received
    usdc_bal = usdc_c.functions.balanceOf(wallet).call() / 1e6
    position = "USDC"
    record_trade("SELL_ETH", r["transactionHash"].hex(),
                f"Sold {eth_amount:.6f} ETH at ~${price:.2f}, got {usdc_bal:.4f} USDC")
    return usdc_bal

def buy_eth_with_usdc(usdc_amount_raw=None):
    """Buy ETH with USDC via Uniswap V3. Returns ETH received or None."""
    global position
    
    if usdc_amount_raw is None:
        usdc_amount_raw = usdc_c.functions.balanceOf(wallet).call()
    
    if usdc_amount_raw == 0:
        log("  No USDC to buy with")
        return None
    
    usdc_human = usdc_amount_raw / 1e6
    log(f"  BUY ETH with {usdc_human:.4f} USDC")
    
    # Approve USDC for router
    if not ensure_approval(usdc_c, USDC, UNI_ROUTER, usdc_amount_raw):
        return None
    time.sleep(1)
    
    # Get expected output for slippage
    price = get_uni_price()
    expected_eth = usdc_human / price
    min_eth = int(w3.to_wei(expected_eth * (1 - SLIPPAGE_BPS/10000), "ether"))
    
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
                f"Bought {eth_received:.6f} ETH with {usdc_human:.4f} USDC at ~${price:.2f}")
    return eth_received

# ?? Cross-DEX Arb ?????????????????????????????????????????????????????
def check_arb_opportunity():
    """
    Compare Uniswap V3 vs Aerodrome prices.
    Returns (direction, spread_pct) or (None, 0).
    direction: 'buy_uni_sell_aero' or 'buy_aero_sell_uni'
    """
    uni_price = get_uni_price()
    aero_price, _ = get_aero_price()
    
    if not uni_price or not aero_price:
        return None, 0, 0, 0
    
    spread = abs(aero_price - uni_price)
    spread_pct = spread / min(uni_price, aero_price) * 100
    
    # Need spread > combined fees (0.05% Uni + ~0.3% Aero volatile = 0.35%)
    # Plus slippage buffer
    MIN_SPREAD = 0.40  # 0.40% minimum to cover both pool fees + slippage
    
    if spread_pct < MIN_SPREAD:
        return None, spread_pct, uni_price, aero_price
    
    if aero_price > uni_price:
        return "buy_uni_sell_aero", spread_pct, uni_price, aero_price
    else:
        return "buy_aero_sell_uni", spread_pct, uni_price, aero_price

# ?? Scalp Strategy ?????????????????????????????????????????????????????
def analyze_trend():
    """
    Analyze recent price action to decide trade direction.
    Returns: ('buy', confidence), ('sell', confidence), or ('hold', 0)
    """
    if len(price_history) < 10:
        return 'hold', 0
    
    prices = list(price_history)
    current = prices[-1]
    
    # Short-term momentum (last 5 readings = 2.5 min)
    short_prices = prices[-5:]
    short_avg = sum(short_prices) / len(short_prices)
    short_momentum = (current - short_avg) / short_avg * 100
    
    # Medium-term momentum (last 20 readings = 10 min)
    if len(prices) >= 20:
        med_prices = prices[-20:]
        med_avg = sum(med_prices) / len(med_prices)
        med_momentum = (current - med_avg) / med_avg * 100
    else:
        med_momentum = short_momentum
    
    # Longer-term trend (last 60 readings = 30 min)
    if len(prices) >= 60:
        long_prices = prices[-60:]
        long_avg = sum(long_prices) / len(long_prices)
        long_momentum = (current - long_avg) / long_avg * 100
    else:
        long_momentum = med_momentum
    
    # Volatility (std dev of last 20 readings)
    if len(prices) >= 20:
        import statistics
        volatility = statistics.stdev(prices[-20:]) / med_avg * 100
    else:
        volatility = 0.1
    
    # Decision logic:
    # 1. If strong short-term dip AND medium trend is up ? BUY (mean reversion)
    # 2. If strong short-term rip AND medium trend is down ? SELL (mean reversion)
    # 3. If all three aligned strongly ? trend follow
    
    # Mean reversion signals (counter-trend)
    if short_momentum < -0.15 and med_momentum > -0.05 and volatility > 0.05:
        confidence = min(abs(short_momentum) / 0.5, 0.8)
        return 'buy', confidence
    
    if short_momentum > 0.15 and med_momentum < 0.05 and volatility > 0.05:
        confidence = min(abs(short_momentum) / 0.5, 0.8)
        return 'sell', confidence
    
    # Trend following (strong alignment)
    if short_momentum > 0.10 and med_momentum > 0.10 and long_momentum > 0.05:
        confidence = min((short_momentum + med_momentum) / 1.0, 0.8)
        return 'buy', confidence
    
    if short_momentum < -0.10 and med_momentum < -0.10 and long_momentum < -0.05:
        confidence = min((abs(short_momentum) + abs(med_momentum)) / 1.0, 0.8)
        return 'sell', confidence
    
    return 'hold', 0

# ?? Main Loop ??????????????????????????????????????????????????????????
async def main():
    global position, start_value_usd
    
    log("=" * 60)
    log("  AEGIS OVERNIGHT TRADER ? LIVE")
    log("  This is real money. Every trade counts.")
    log("=" * 60)
    
    # Initial state
    eth_bal, weth_bal, usdc_bal, total_usd, price = get_balances()
    start_value_usd = total_usd
    
    if usdc_bal > 0.5:
        position = "USDC"
    else:
        position = "ETH"
    
    log(f"  Wallet: {wallet}")
    log(f"  ETH:  {eth_bal:.6f} (${eth_bal * price:.2f})")
    log(f"  WETH: {weth_bal:.6f}")
    log(f"  USDC: {usdc_bal:.4f}")
    log(f"  Total: ${total_usd:.2f}")
    log(f"  Position: {position}")
    log(f"  ETH Price: ${price:.2f}")
    log(f"  Stop Loss: ${STOP_LOSS_USD}")
    log(f"  Gas cost/swap: ~$0.003")
    log("")
    log("  Strategy: Cross-DEX Arb + Micro-Scalp")
    log("  Checking every 30 seconds for 6 hours.")
    log("=" * 60)
    
    if total_usd < STOP_LOSS_USD:
        log(f"FATAL: Starting value ${total_usd:.2f} already below stop ${STOP_LOSS_USD}")
        return
    
    start_time = time.time()
    last_trade_time = 0
    min_trade_cooldown = 120  # minimum 2 minutes between trades
    check_count = 0
    arb_checks = 0
    
    # Track entry price for P&L
    if position == "USDC":
        entry_price = price  # we sold at this price
    else:
        entry_price = None
    
    while time.time() - start_time < MAX_RUNTIME:
        try:
            check_count += 1
            elapsed_min = int((time.time() - start_time) / 60)
            
            # Get current state
            eth_bal, weth_bal, usdc_bal, total_usd, price = get_balances()
            price_history.append(price)
            
            # ?? SAFETY CHECK ??
            if total_usd < STOP_LOSS_USD:
                log(f"[STOP] STOP LOSS HIT! Portfolio ${total_usd:.2f} < ${STOP_LOSS_USD}")
                log(f"   Converting everything to ETH and stopping.")
                if position == "USDC" and usdc_bal > 0.01:
                    buy_eth_with_usdc()
                break
            
            # ?? Status line (every 5 checks) ??
            if check_count % 5 == 0:
                pnl_pct = (total_usd - start_value_usd) / start_value_usd * 100
                ext_price = await get_external_price()
                ext_str = f" | CryptoCompare: ${ext_price:.2f}" if ext_price else ""
                log(f"  [{elapsed_min}m] ${price:.2f} | Portfolio: ${total_usd:.2f} ({pnl_pct:+.2f}%) | Pos: {position} | Trades: {total_trades}{ext_str}")
            
            # ?? Check cooldown ??
            time_since_trade = time.time() - last_trade_time
            if time_since_trade < min_trade_cooldown:
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            
            # ?? STRATEGY 1: Cross-DEX Arb ??
            arb_dir, spread_pct, uni_p, aero_p = check_arb_opportunity()
            arb_checks += 1
            
            if arb_dir and spread_pct > 0.40:
                log(f"")
                log(f"  [FIRE] ARB OPPORTUNITY! Spread: {spread_pct:.3f}%")
                log(f"     Uniswap: ${uni_p:.2f} | Aerodrome: ${aero_p:.2f}")
                log(f"     Direction: {arb_dir}")
                
                tradeable_eth = (eth_bal - GAS_RESERVE) * MAX_TRADE_PCT
                
                # Uni is cheaper, Aero is more expensive
                if arb_dir == "buy_uni_sell_aero" and position == "ETH" and tradeable_eth > 0.0005:
                    log(f"  Selling {tradeable_eth:.6f} ETH on Uni (Aero is higher)")
                    result = sell_eth_for_usdc(tradeable_eth)
                    if result:
                        entry_price = price
                        last_trade_time = time.time()
                        log(f"  Sold at ${price:.2f}. Will buy back when Aero price drops.")
                
                # Aero is cheaper, Uni is more expensive  
                elif arb_dir == "buy_aero_sell_uni" and position == "ETH" and tradeable_eth > 0.0005:
                    # Sell on Uni (higher price) — get USDC at the better rate
                    log(f"  Selling {tradeable_eth:.6f} ETH on Uni at ${uni_p:.2f} (Aero only ${aero_p:.2f})")
                    result = sell_eth_for_usdc(tradeable_eth)
                    if result:
                        entry_price = uni_p
                        last_trade_time = time.time()
                        log(f"  Sold at ${uni_p:.2f}. Will buy back when spread narrows.")
                
                elif arb_dir == "buy_aero_sell_uni" and position == "USDC" and usdc_bal > 0.5:
                    log(f"  Buying ETH with USDC (Aero is cheaper)")
                    result = buy_eth_with_usdc()
                    if result:
                        entry_price = None
                        last_trade_time = time.time()
                
                elif arb_dir == "buy_uni_sell_aero" and position == "USDC" and usdc_bal > 0.5:
                    log(f"  Buying ETH with USDC (Uni is cheaper)")
                    result = buy_eth_with_usdc()
                    if result:
                        entry_price = None
                        last_trade_time = time.time()
                
                await asyncio.sleep(CHECK_INTERVAL)
                continue
            
            # Log arb spread periodically
            if arb_checks % 20 == 0 and uni_p and aero_p:
                log(f"  [Arb scan] Uni: ${uni_p:.2f} | Aero: ${aero_p:.2f} | Spread: {spread_pct:.3f}% (need >0.40%)")
            
            # ?? STRATEGY 2: Micro-Scalp ??
            signal, confidence = analyze_trend()
            
            if signal == 'buy' and confidence > 0.3 and position == "USDC" and usdc_bal > 0.5:
                log(f"")
                log(f"  [UP] BUY SIGNAL (conf: {confidence:.2f})")
                log(f"     Price: ${price:.2f} | Short momentum dip detected")
                
                result = buy_eth_with_usdc()
                if result:
                    # Calculate P&L from the USDC->ETH round trip
                    if entry_price:
                        pnl = (entry_price - price) / entry_price * usdc_bal
                        log(f"  P&L on this trade: ${pnl:.4f}")
                    entry_price = None
                    last_trade_time = time.time()
            
            elif signal == 'sell' and confidence > 0.3 and position == "ETH":
                tradeable_eth = (eth_bal - GAS_RESERVE) * MAX_TRADE_PCT
                if tradeable_eth > 0.0005:
                    log(f"")
                    log(f"  [DOWN] SELL SIGNAL (conf: {confidence:.2f})")
                    log(f"     Price: ${price:.2f} | Short momentum rip detected")
                    
                    result = sell_eth_for_usdc(tradeable_eth)
                    if result:
                        entry_price = price
                        last_trade_time = time.time()
            
            # ?? POSITION MANAGEMENT ??
            # If we're in USDC and price dropped significantly, buy back (take profit)
            if position == "USDC" and entry_price and usdc_bal > 0.5:
                price_change = (price - entry_price) / entry_price * 100
                
                if price_change <= -0.20:  # Price dropped 0.20% ? take profit
                    log(f"")
                    log(f"  [MONEY] TAKE PROFIT! Sold at ${entry_price:.2f}, now ${price:.2f} ({price_change:.2f}%)")
                    result = buy_eth_with_usdc()
                    if result:
                        pnl = abs(price_change / 100) * usdc_bal
                        log(f"  Estimated profit: ${pnl:.4f}")
                        entry_price = None
                        last_trade_time = time.time()
                
                elif price_change >= 0.40:  # Price went UP 0.40% ? stop loss on short
                    log(f"")
                    log(f"  [STOP] POSITION STOP! Sold at ${entry_price:.2f}, now ${price:.2f} ({price_change:+.2f}%)")
                    result = buy_eth_with_usdc()
                    if result:
                        pnl = -(price_change / 100) * usdc_bal
                        log(f"  Loss on this trade: ${pnl:.4f}")
                        entry_price = None
                        last_trade_time = time.time()
            
        except Exception as e:
            log(f"  ERROR in main loop: {e}")
            try:
                import traceback
                traceback.print_exc()
            except Exception:
                pass  # don't let traceback printing crash us
        
        await asyncio.sleep(CHECK_INTERVAL)
    
    # ?? Final Summary ??
    log("")
    log("=" * 60)
    log("  OVERNIGHT SESSION COMPLETE")
    log("=" * 60)
    
    eth_bal, weth_bal, usdc_bal, total_usd, price = get_balances()
    
    # Convert everything back to ETH if in USDC
    if position == "USDC" and usdc_bal > 0.5:
        log("  Converting remaining USDC -> ETH...")
        buy_eth_with_usdc()
        eth_bal, weth_bal, usdc_bal, total_usd, price = get_balances()
    
    pnl_usd = total_usd - start_value_usd
    pnl_pct = pnl_usd / start_value_usd * 100
    
    log(f"  Start:  ${start_value_usd:.2f}")
    log(f"  End:    ${total_usd:.2f}")
    log(f"  P&L:    ${pnl_usd:.4f} ({pnl_pct:+.2f}%)")
    log(f"  Trades: {total_trades}")
    log(f"  Runtime: {int((time.time() - start_time) / 60)} minutes")
    log("")
    
    for t in trade_log:
        log(f"  [{t['trade_num']}] {t['action']} ? {t['basescan']}")
    
    # Save final log
    with open(LOG_FILE, "w") as f:
        json.dump({
            "trades": trade_log,
            "summary": {
                "start_value_usd": round(start_value_usd, 4),
                "end_value_usd": round(total_usd, 4),
                "pnl_usd": round(pnl_usd, 4),
                "pnl_pct": round(pnl_pct, 4),
                "total_trades": total_trades,
                "runtime_minutes": int((time.time() - start_time) / 60),
                "arb_checks": arb_checks,
            }
        }, f, indent=2)
    
    log(f"  Log saved to: {LOG_FILE}")
    log("  Done. zzz")


if __name__ == "__main__":
    asyncio.run(main())
