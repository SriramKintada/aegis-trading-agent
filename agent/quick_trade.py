"""Quick trade: sell ETH->USDC now, monitor, buy back on dip."""
import json, os, sys, time, asyncio, aiohttp
from datetime import datetime, timezone
from pathlib import Path
from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

load_dotenv(Path(__file__).parent.parent / ".env")
w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
account = Account.from_key(os.getenv("PRIVATE_KEY"))
wallet = account.address

WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
SWAP_ROUTER = Web3.to_checksum_address("0x2626664c2603336E57B271c5C0b26F421741e481")

WETH_ABI = json.loads('[{"constant":false,"inputs":[],"name":"deposit","outputs":[],"payable":true,"stateMutability":"payable","type":"function"},{"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"wad","type":"uint256"}],"name":"withdraw","outputs":[],"type":"function"}]')
USDC_ABI = json.loads('[{"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]')
SWAP_ROUTER_ABI = json.loads('[{"inputs":[{"components":[{"name":"tokenIn","type":"address"},{"name":"tokenOut","type":"address"},{"name":"fee","type":"uint24"},{"name":"recipient","type":"address"},{"name":"amountIn","type":"uint256"},{"name":"amountOutMinimum","type":"uint256"},{"name":"sqrtPriceLimitX96","type":"uint160"}],"name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}]')

weth_c = w3.eth.contract(address=WETH, abi=WETH_ABI)
usdc_c = w3.eth.contract(address=USDC, abi=USDC_ABI)
router = w3.eth.contract(address=SWAP_ROUTER, abi=SWAP_ROUTER_ABI)
trades = []

def gas():
    b = w3.eth.get_block("latest")
    bf = b.get("baseFeePerGas", 1000000)
    mp = w3.to_wei(0.001, "gwei")
    return {"maxFeePerGas": bf*2+mp, "maxPriorityFeePerGas": mp, "chainId": 8453}

def tx(built):
    built.update(gas())
    built["nonce"] = w3.eth.get_transaction_count(wallet)
    if "gas" not in built:
        built["gas"] = w3.eth.estimate_gas(built)
    signed = account.sign_transaction(built)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    r = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    hx = r["transactionHash"].hex()
    ok = r["status"] == 1
    print(f"  {'OK' if ok else 'FAIL'}: https://basescan.org/tx/{hx}")
    trades.append({"time": datetime.now(timezone.utc).isoformat(), "tx": hx, "ok": ok})
    return r if ok else None

async def price():
    async with aiohttp.ClientSession() as s:
        async with s.get("https://min-api.cryptocompare.com/data/price", params={"fsym":"ETH","tsyms":"USD"}) as r:
            return float((await r.json()).get("USD", 0))

async def main():
    # Check WETH balance from earlier wrap
    weth_bal = weth_c.functions.balanceOf(wallet).call()
    usdc_bal = usdc_c.functions.balanceOf(wallet).call()
    eth_bal = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
    
    print(f"ETH: {eth_bal:.6f} | WETH: {w3.from_wei(weth_bal, 'ether'):.6f} | USDC: {usdc_bal/1e6:.4f}")
    
    if weth_bal == 0 and usdc_bal == 0:
        print("No WETH or USDC to trade. Need to wrap ETH first.")
        print("Wrapping 0.001 ETH...")
        t = weth_c.functions.deposit().build_transaction({"from": wallet, "value": w3.to_wei(0.001, "ether"), "gas": 50000})
        if not tx(t):
            return
        time.sleep(2)
        weth_bal = weth_c.functions.balanceOf(wallet).call()
    
    entry_price = await price()
    print(f"ETH: ${entry_price:.2f}")
    
    if weth_bal > 0:
        # Approve + Sell WETH -> USDC
        print(f"Approving WETH...")
        t = weth_c.functions.approve(SWAP_ROUTER, weth_bal * 2).build_transaction({"from": wallet, "gas": 60000})
        tx(t)
        time.sleep(2)
        
        print(f"Selling WETH -> USDC at ${entry_price:.2f}...")
        params = (WETH, USDC, 500, wallet, weth_bal, 0, 0)
        t = router.functions.exactInputSingle(params).build_transaction({"from": wallet, "gas": 200000})
        if not tx(t):
            print("Swap failed")
            return
        
        usdc_bal = usdc_c.functions.balanceOf(wallet).call()
        print(f"Got: {usdc_bal/1e6:.4f} USDC")
    
    elif usdc_bal > 0:
        print(f"Already holding {usdc_bal/1e6:.4f} USDC")
    else:
        print("Nothing to trade")
        return
    
    # Monitor
    print(f"\nMonitoring... buy back at -0.3% (${entry_price*0.997:.2f}), stop at +0.5% (${entry_price*1.005:.2f})")
    
    start = time.time()
    max_time = 4 * 3600  # 4 hours
    
    while time.time() - start < max_time:
        p = await price()
        chg = (p - entry_price) / entry_price * 100
        elapsed = int(time.time() - start)
        mins = elapsed // 60
        print(f"  [{mins}m] ${p:.2f} ({chg:+.2f}%)", end="\r")
        
        if chg <= -0.3:
            print(f"\n  DIP! ETH at ${p:.2f} ({chg:.2f}%) -> BUYING BACK")
            usdc_now = usdc_c.functions.balanceOf(wallet).call()
            t = usdc_c.functions.approve(SWAP_ROUTER, usdc_now).build_transaction({"from": wallet, "gas": 60000})
            tx(t)
            time.sleep(1)
            params = (USDC, WETH, 500, wallet, usdc_now, 0, 0)
            t = router.functions.exactInputSingle(params).build_transaction({"from": wallet, "gas": 200000})
            tx(t)
            # Unwrap
            wb = weth_c.functions.balanceOf(wallet).call()
            if wb > 0:
                t = weth_c.functions.withdraw(wb).build_transaction({"from": wallet, "gas": 50000})
                tx(t)
            break
        
        elif chg >= 0.5:
            print(f"\n  STOP! ETH at ${p:.2f} ({chg:+.2f}%) -> buying back (stop loss)")
            usdc_now = usdc_c.functions.balanceOf(wallet).call()
            t = usdc_c.functions.approve(SWAP_ROUTER, usdc_now).build_transaction({"from": wallet, "gas": 60000})
            tx(t)
            time.sleep(1)
            params = (USDC, WETH, 500, wallet, usdc_now, 0, 0)
            t = router.functions.exactInputSingle(params).build_transaction({"from": wallet, "gas": 200000})
            tx(t)
            wb = weth_c.functions.balanceOf(wallet).call()
            if wb > 0:
                t = weth_c.functions.withdraw(wb).build_transaction({"from": wallet, "gas": 50000})
                tx(t)
            break
        
        await asyncio.sleep(30)
    
    # Final
    final = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
    fp = await price()
    print(f"\nFinal: {final:.6f} ETH (${final*fp:.2f})")
    print(f"Trades: {len(trades)}")
    with open(Path(__file__).parent / "active_trades.json", "w") as f:
        json.dump(trades, f, indent=2)

asyncio.run(main())
