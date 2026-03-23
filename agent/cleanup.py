"""Unwrap any remaining WETH -> ETH after bot shutdown."""
from web3 import Web3
from eth_account import Account
from dotenv import load_dotenv
from pathlib import Path
import os, time

load_dotenv(Path(__file__).parent.parent / ".env")
w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
account = Account.from_key(os.getenv("PRIVATE_KEY"))
wallet = account.address

WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
WETH_ABI = [
    {"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},
    {"constant":False,"inputs":[{"name":"wad","type":"uint256"}],"name":"withdraw","outputs":[],"type":"function"},
]
weth_c = w3.eth.contract(address=WETH, abi=WETH_ABI)
weth_bal = weth_c.functions.balanceOf(wallet).call()
print(f"WETH balance: {w3.from_wei(weth_bal, 'ether')} ETH")

if weth_bal > 0:
    print("Unwrapping WETH -> ETH...")
    block = w3.eth.get_block("latest")
    base_fee = block.get("baseFeePerGas", 1000000)
    priority = w3.to_wei(0.001, "gwei")
    tx = weth_c.functions.withdraw(weth_bal).build_transaction({
        "from": wallet,
        "gas": 50000,
        "chainId": 8453,
        "nonce": w3.eth.get_transaction_count(wallet),
        "maxFeePerGas": base_fee * 2 + priority,
        "maxPriorityFeePerGas": priority,
    })
    signed = account.sign_transaction(tx)
    h = w3.eth.send_raw_transaction(signed.raw_transaction)
    r = w3.eth.wait_for_transaction_receipt(h, timeout=60)
    tx_hex = r["transactionHash"].hex()
    print(f"Unwrap TX: https://basescan.org/tx/{tx_hex} status={r['status']}")
else:
    print("No WETH to unwrap.")

time.sleep(2)
eth = w3.from_wei(w3.eth.get_balance(wallet), "ether")
print(f"Final ETH balance: {eth} (~${float(eth) * 2060:.2f})")
