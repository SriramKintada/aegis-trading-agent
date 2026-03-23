"""Monitor ETH price and buy back USDC->ETH when profitable."""
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

USDC_ABI = json.loads('[{"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]')
WETH_ABI = json.loads('[{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"wad","type":"uint256"}],"name":"withdraw","outputs":[],"type":"function"}]')
SWAP_ABI = json.loads('[{"inputs":[{"components":[{"name":"tokenIn","type":"address"},{"name":"tokenOut","type":"address"},{"name":"fee","type":"uint24"},{"name":"recipient","type":"address"},{"name":"amountIn","type":"uint256"},{"name":"amountOutMinimum","type":"uint256"},{"name":"sqrtPriceLimitX96","type":"uint160"}],"name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}]')

usdc_c = w3.eth.contract(address=USDC, abi=USDC_ABI)
weth_c = w3.eth.contract(address=WETH, abi=WETH_ABI)
router = w3.eth.contract(address=SWAP_ROUTER, abi=SWAP_ABI)

ENTRY_PRICE = 2083.0  # price we sold at
trades = []

def gas():
    b = w3.eth.get_block("latest")
    bf = b.get("baseFeePerGas", 1000000)
    mp = w3.to_wei(0.001, "gwei")
    return {"maxFeePerGas": bf*2+mp, "maxPriorityFeePerGas": mp, "chainId": 8453}

def do_tx(built):
    built.update(gas())
    built["nonce"] = w3.eth.get_transaction_count(wallet)
    if "gas" not in built:
        built["gas"] = w3.eth.estimate_gas(built)
    signed = account.sign_transaction(built)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    r = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    hx = r["transactionHash"].hex()
    ok = r["status"] == 1
    status = "OK" if ok else "FAIL"
    print(f"  TX {status}: https://basescan.org/tx/{hx}", flush=True)
    trades.append({"time": datetime.now(timezone.utc).isoformat(), "tx": hx, "ok": ok})
    with open(Path(__file__).parent / "active_trades.json", "w") as f:
        json.dump(trades, f, indent=2)
    return r if ok else None

async def get_price():
    async with aiohttp.ClientSession() as s:
        async with s.get("https://min-api.cryptocompare.com/data/price", params={"fsym":"ETH","tsyms":"USD"}) as r:
            return float((await r.json()).get("USD", 0))

async def main():
    usdc_bal = usdc_c.functions.balanceOf(wallet).call()
    if usdc_bal == 0:
        print("No USDC to buy back. Nothing to do.", flush=True)
        return

    print(f"USDC balance: {usdc_bal/1e6:.4f}", flush=True)
    print(f"Entry price: ${ENTRY_PRICE:.2f}", flush=True)
    print(f"Buy back at: ${ENTRY_PRICE * 0.997:.2f} (-0.3% = PROFIT)", flush=True)
    print(f"Stop loss at: ${ENTRY_PRICE * 1.005:.2f} (+0.5% = CUT LOSS)", flush=True)
    print(f"Monitoring every 30s...", flush=True)
    print(flush=True)

    start = time.time()

    while time.time() - start < 14400:  # 4 hours max
        try:
            p = await get_price()
            chg = (p - ENTRY_PRICE) / ENTRY_PRICE * 100
            mins = int((time.time() - start) / 60)
            print(f"  [{mins}m] ${p:.2f} ({chg:+.2f}%) | Target: ${ENTRY_PRICE*0.997:.2f} | Stop: ${ENTRY_PRICE*1.005:.2f}", flush=True)

            if chg <= -0.3:
                print(f"\n>>> PROFIT TRIGGER! ETH at ${p:.2f} ({chg:.2f}%)", flush=True)
                print(f">>> Buying back USDC -> WETH...", flush=True)

                # Approve USDC
                t = usdc_c.functions.approve(SWAP_ROUTER, usdc_bal).build_transaction({"from": wallet, "gas": 60000})
                do_tx(t)
                time.sleep(2)

                # Swap USDC -> WETH
                params = (USDC, WETH, 500, wallet, usdc_bal, 0, 0)
                t = router.functions.exactInputSingle(params).build_transaction({"from": wallet, "gas": 200000})
                do_tx(t)
                time.sleep(2)

                # Unwrap WETH -> ETH
                wb = weth_c.functions.balanceOf(wallet).call()
                if wb > 0:
                    t = weth_c.functions.withdraw(wb).build_transaction({"from": wallet, "gas": 50000})
                    do_tx(t)

                final_eth = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
                print(f">>> Final ETH: {final_eth:.6f} (${final_eth * p:.2f})", flush=True)
                return

            elif chg >= 0.5:
                print(f"\n>>> STOP LOSS! ETH at ${p:.2f} ({chg:+.2f}%)", flush=True)
                print(f">>> Buying back to cut loss...", flush=True)

                t = usdc_c.functions.approve(SWAP_ROUTER, usdc_bal).build_transaction({"from": wallet, "gas": 60000})
                do_tx(t)
                time.sleep(2)

                params = (USDC, WETH, 500, wallet, usdc_bal, 0, 0)
                t = router.functions.exactInputSingle(params).build_transaction({"from": wallet, "gas": 200000})
                do_tx(t)
                time.sleep(2)

                wb = weth_c.functions.balanceOf(wallet).call()
                if wb > 0:
                    t = weth_c.functions.withdraw(wb).build_transaction({"from": wallet, "gas": 50000})
                    do_tx(t)

                final_eth = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
                print(f">>> Final ETH: {final_eth:.6f} (${final_eth * p:.2f})", flush=True)
                return

        except Exception as e:
            print(f"  Error: {e}", flush=True)

        await asyncio.sleep(30)

    print("Time limit reached", flush=True)

asyncio.run(main())
