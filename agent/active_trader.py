"""
Active trader — tries to profit from ETH price movements.

Strategy: Monitor ETH price. If it drops > 0.5% from entry, buy more.
If it rises > 0.5% from our USDC position, swap back for profit.

We split: keep 0.002 ETH as gas reserve, trade with 0.002 ETH.
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import aiohttp
import asyncio
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

load_dotenv(Path(__file__).parent.parent / ".env")

RPC_URL = os.getenv("RPC_URL", "https://mainnet.base.org")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
CHAIN_ID = 8453

WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
SWAP_ROUTER = Web3.to_checksum_address("0x2626664c2603336E57B271c5C0b26F421741e481")

WETH_ABI = json.loads('[{"constant":false,"inputs":[],"name":"deposit","outputs":[],"payable":true,"stateMutability":"payable","type":"function"},{"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"wad","type":"uint256"}],"name":"withdraw","outputs":[],"type":"function"}]')
USDC_ABI = json.loads('[{"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]')
SWAP_ROUTER_ABI = json.loads('[{"inputs":[{"components":[{"name":"tokenIn","type":"address"},{"name":"tokenOut","type":"address"},{"name":"fee","type":"uint24"},{"name":"recipient","type":"address"},{"name":"amountIn","type":"uint256"},{"name":"amountOutMinimum","type":"uint256"},{"name":"sqrtPriceLimitX96","type":"uint160"}],"name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}]')

trade_log = []


def save_log():
    path = Path(__file__).parent / "active_trades.json"
    with open(path, "w") as f:
        json.dump(trade_log, f, indent=2)


def get_gas_params(w3):
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas", 1000000)
    max_priority = w3.to_wei(0.001, "gwei")
    return {"maxFeePerGas": base_fee * 2 + max_priority, "maxPriorityFeePerGas": max_priority}


def send_tx(w3, account, tx_dict):
    try:
        gas_params = get_gas_params(w3)
        tx_dict.update(gas_params)
        tx_dict["chainId"] = CHAIN_ID
        tx_dict["nonce"] = w3.eth.get_transaction_count(account.address)
        if "gas" not in tx_dict:
            tx_dict["gas"] = w3.eth.estimate_gas(tx_dict)
        signed = account.sign_transaction(tx_dict)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)
        h = receipt["transactionHash"].hex()
        status = "OK" if receipt["status"] == 1 else "FAIL"
        print(f"  TX {status}: https://basescan.org/tx/{h}")
        return receipt
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


async def get_eth_price():
    async with aiohttp.ClientSession() as s:
        async with s.get("https://min-api.cryptocompare.com/data/price", params={"fsym": "ETH", "tsyms": "USD"}) as r:
            data = await r.json()
            return float(data.get("USD", 0))


def do_swap(w3, account, wallet, token_in, token_out, amount_in, label):
    """Execute a single swap and log it."""
    swap_router = w3.eth.contract(address=SWAP_ROUTER, abi=SWAP_ROUTER_ABI)
    params = (token_in, token_out, 500, wallet, amount_in, 0, 0)
    tx = swap_router.functions.exactInputSingle(params).build_transaction({
        "from": wallet, "gas": 200000
    })
    receipt = send_tx(w3, account, tx)
    if receipt and receipt["status"] == 1:
        h = receipt["transactionHash"].hex()
        trade_log.append({
            "time": datetime.now(timezone.utc).isoformat(),
            "action": label,
            "tx": h,
            "link": f"https://basescan.org/tx/{h}",
        })
        save_log()
        return True
    return False


async def run():
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    account = Account.from_key(PRIVATE_KEY)
    wallet = account.address
    weth = w3.eth.contract(address=WETH, abi=WETH_ABI)
    usdc = w3.eth.contract(address=USDC, abi=USDC_ABI)

    print("=" * 50)
    print("AEGIS Active Trader")
    print("=" * 50)
    
    eth_bal = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
    print(f"Balance: {eth_bal:.6f} ETH")

    # Reserve 0.002 ETH for gas, trade with the rest
    trade_eth = min(0.001, eth_bal - 0.002)
    if trade_eth <= 0:
        print("Not enough ETH to trade (need > 0.002 for gas reserve)")
        return

    entry_price = await get_eth_price()
    print(f"ETH price: ${entry_price:.2f}")
    print(f"Trading with: {trade_eth:.6f} ETH (${trade_eth * entry_price:.2f})")
    print(f"Strategy: Swap to USDC now, buy back when ETH drops 0.3%+")
    print()

    # Step 1: Wrap ETH
    print("[1] Wrapping ETH -> WETH...")
    wrap_amt = w3.to_wei(trade_eth, "ether")
    tx = weth.functions.deposit().build_transaction({"from": wallet, "value": wrap_amt, "gas": 50000})
    r = send_tx(w3, account, tx)
    if not r or r["status"] != 1:
        print("Wrap failed")
        return
    trade_log.append({"time": datetime.now(timezone.utc).isoformat(), "action": "WRAP", "tx": r["transactionHash"].hex(), "link": f"https://basescan.org/tx/{r['transactionHash'].hex()}"})

    # Step 2: Approve WETH
    print("[2] Approving WETH...")
    tx = weth.functions.approve(SWAP_ROUTER, w3.to_wei(1, "ether")).build_transaction({"from": wallet, "gas": 60000})
    r = send_tx(w3, account, tx)
    if not r or r["status"] != 1:
        print("Approve failed")
        return
    trade_log.append({"time": datetime.now(timezone.utc).isoformat(), "action": "APPROVE_WETH", "tx": r["transactionHash"].hex(), "link": f"https://basescan.org/tx/{r['transactionHash'].hex()}"})

    # Step 3: Swap WETH -> USDC (sell ETH at current price)
    print(f"[3] Selling {trade_eth} ETH at ${entry_price:.2f}...")
    if not do_swap(w3, account, wallet, WETH, USDC, wrap_amt, f"SELL_ETH_AT_{entry_price:.0f}"):
        print("Sell failed")
        return

    usdc_bal = usdc.functions.balanceOf(wallet).call() / 1e6
    print(f"Got: {usdc_bal:.4f} USDC")
    print()

    # Step 4: Monitor and wait for dip
    print("Monitoring ETH price... (checking every 60s, waiting for 0.3% dip to buy back)")
    print("Will also buy back if price rises 0.5% (cut loss on short)")
    print("Max wait: 4 hours")
    print()

    cycles = 0
    max_cycles = 240  # 4 hours at 60s intervals
    bought_back = False

    while cycles < max_cycles:
        current_price = await get_eth_price()
        change_pct = (current_price - entry_price) / entry_price * 100

        now = datetime.now().strftime("%H:%M:%S")
        print(f"  [{now}] ETH: ${current_price:.2f} | Change: {change_pct:+.2f}% | Cycle {cycles+1}/{max_cycles}", end="\r")

        # Buy back if ETH dropped 0.3%+ (we profit)
        if change_pct <= -0.3:
            print(f"\n  ETH dropped {change_pct:.2f}% -> BUYING BACK!")
            
            # Approve USDC
            usdc_raw = usdc.functions.balanceOf(wallet).call()
            tx = usdc.functions.approve(SWAP_ROUTER, usdc_raw).build_transaction({"from": wallet, "gas": 60000})
            send_tx(w3, account, tx)
            
            # Swap USDC -> WETH
            if do_swap(w3, account, wallet, USDC, WETH, usdc_raw, f"BUY_ETH_AT_{current_price:.0f}_PROFIT"):
                # Unwrap
                weth_bal = weth.functions.balanceOf(wallet).call()
                if weth_bal > 0:
                    tx = weth.functions.withdraw(weth_bal).build_transaction({"from": wallet, "gas": 50000})
                    send_tx(w3, account, tx)
                
                new_eth = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
                profit_eth = new_eth - eth_bal
                profit_usd = profit_eth * current_price
                print(f"  PROFIT: {profit_eth:.6f} ETH (${profit_usd:.4f})")
                bought_back = True
                break

        # Cut loss if ETH rose 0.5%+ (we're short, losing money)
        elif change_pct >= 0.5:
            print(f"\n  ETH rose {change_pct:.2f}% -> cutting loss, buying back")
            usdc_raw = usdc.functions.balanceOf(wallet).call()
            tx = usdc.functions.approve(SWAP_ROUTER, usdc_raw).build_transaction({"from": wallet, "gas": 60000})
            send_tx(w3, account, tx)
            
            if do_swap(w3, account, wallet, USDC, WETH, usdc_raw, f"BUY_ETH_AT_{current_price:.0f}_STOPLOSS"):
                weth_bal = weth.functions.balanceOf(wallet).call()
                if weth_bal > 0:
                    tx = weth.functions.withdraw(weth_bal).build_transaction({"from": wallet, "gas": 50000})
                    send_tx(w3, account, tx)
                
                new_eth = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
                loss_eth = new_eth - eth_bal
                loss_usd = loss_eth * current_price
                print(f"  LOSS: {loss_eth:.6f} ETH (${loss_usd:.4f})")
                bought_back = True
                break

        cycles += 1
        await asyncio.sleep(60)

    if not bought_back:
        print("\n  Time limit reached. Buying back at market...")
        usdc_raw = usdc.functions.balanceOf(wallet).call()
        if usdc_raw > 0:
            tx = usdc.functions.approve(SWAP_ROUTER, usdc_raw).build_transaction({"from": wallet, "gas": 60000})
            send_tx(w3, account, tx)
            do_swap(w3, account, wallet, USDC, WETH, usdc_raw, f"BUY_ETH_TIMEOUT")
            weth_bal = weth.functions.balanceOf(wallet).call()
            if weth_bal > 0:
                tx = weth.functions.withdraw(weth_bal).build_transaction({"from": wallet, "gas": 50000})
                send_tx(w3, account, tx)

    # Final
    final_eth = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
    final_price = await get_eth_price()
    print(f"\n{'='*50}")
    print(f"Final balance: {final_eth:.6f} ETH (${final_eth * final_price:.2f})")
    print(f"Started with:  {eth_bal:.6f} ETH (${eth_bal * entry_price:.2f})")
    pnl = final_eth - eth_bal
    print(f"P&L: {pnl:+.6f} ETH (${pnl * final_price:+.4f})")
    print(f"Trades: {len(trade_log)}")
    save_log()


if __name__ == "__main__":
    asyncio.run(run())
