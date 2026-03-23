"""
check_all.py — Quick balance check for ETH, WETH, and USDC on the AEGIS bot wallet (Base mainnet).
"""
from web3 import Web3
import json

w3 = Web3(Web3.HTTPProvider("https://mainnet.base.org"))
wallet = "0x86c2C9b1Fc8D9662dA6AFB44541eb5964b5dc424"

WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

abi = json.loads('[{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]')

weth_c = w3.eth.contract(address=WETH, abi=abi)
usdc_c = w3.eth.contract(address=USDC, abi=abi)

eth = float(w3.from_wei(w3.eth.get_balance(wallet), "ether"))
weth = float(w3.from_wei(weth_c.functions.balanceOf(wallet).call(), "ether"))
usdc = usdc_c.functions.balanceOf(wallet).call() / 1e6
nonce = w3.eth.get_transaction_count(wallet)

print(f"ETH:  {eth:.6f} (${eth*2080:.2f})")
print(f"WETH: {weth:.6f} (${weth*2080:.2f})")
print(f"USDC: {usdc:.6f}")
print(f"Total: ${(eth+weth)*2080 + usdc:.2f}")
print(f"Nonce: {nonce}")
