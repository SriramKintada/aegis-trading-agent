"""
check_balance.py — Quick ETH balance check for the AEGIS bot wallet on Base mainnet.
"""
from web3 import Web3
w3 = Web3(Web3.HTTPProvider('https://mainnet.base.org'))
print(f'Connected: {w3.is_connected()}')
balance = w3.eth.get_balance('0x86c2C9b1Fc8D9662dA6AFB44541eb5964b5dc424')
eth_balance = w3.from_wei(balance, 'ether')
print(f'Bot wallet balance: {eth_balance} ETH')
usd = float(eth_balance) * 2100
print(f'Approx USD: ${usd:.2f}')
