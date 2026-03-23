"""
full_balance.py — Comprehensive balance report for the AEGIS bot wallet: ETH, WETH, USDC
with USD estimates and current nonce (Base mainnet).
"""
from web3 import Web3
import json

w3 = Web3(Web3.HTTPProvider('https://mainnet.base.org'))
wallet = '0x86c2C9b1Fc8D9662dA6AFB44541eb5964b5dc424'

# Balances
eth_bal = w3.from_wei(w3.eth.get_balance(wallet), 'ether')

USDC = Web3.to_checksum_address('0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913')
WETH = Web3.to_checksum_address('0x4200000000000000000000000000000000000006')
ERC20_ABI = [{"constant":True,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"}]

usdc_contract = w3.eth.contract(address=USDC, abi=ERC20_ABI)
weth_contract = w3.eth.contract(address=WETH, abi=ERC20_ABI)

usdc_bal = usdc_contract.functions.balanceOf(wallet).call() / 1e6
weth_bal = float(w3.from_wei(weth_contract.functions.balanceOf(wallet).call(), 'ether'))

print(f"ETH:  {eth_bal} ({float(eth_bal) * 2050:.2f} USD est)")
print(f"WETH: {weth_bal}")
print(f"USDC: {usdc_bal}")
print(f"Total approx: ${float(eth_bal) * 2050 + weth_bal * 2050 + usdc_bal:.2f}")

# Get ETH price from Uniswap V3 WETH/USDC 0.05% pool
# token0=WETH, token1=USDC on this pool
POOL = Web3.to_checksum_address('0xd0b53D9277642d899DF5C87A3966A349A798F224')
POOL_ABI = [{"inputs":[],"name":"slot0","outputs":[{"name":"sqrtPriceX96","type":"uint160"},{"name":"tick","type":"int24"},{"name":"observationIndex","type":"uint16"},{"name":"observationCardinality","type":"uint16"},{"name":"observationCardinalityNext","type":"uint16"},{"name":"feeProtocol","type":"uint8"},{"name":"unlocked","type":"bool"}],"type":"function"}]
pool = w3.eth.contract(address=POOL, abi=POOL_ABI)
try:
    slot0 = pool.functions.slot0().call()
    sqrtPriceX96 = slot0[0]
    # Price = (sqrtPriceX96 / 2^96)^2
    # This gives token1/token0 = USDC/WETH but need to adjust for decimals
    # USDC has 6 decimals, WETH has 18 decimals
    price = (sqrtPriceX96 / (2**96))**2 * (10**12)  # 10^(18-6)
    print(f"\nLive ETH/USD (Uniswap pool): ${price:.2f}")
    print(f"Actual portfolio value: ${float(eth_bal) * price + weth_bal * price + usdc_bal:.2f}")
except Exception as e:
    print(f"Pool price error: {e}")

# Gas price
gas_price = w3.eth.gas_price
print(f"\nGas price: {w3.from_wei(gas_price, 'gwei'):.4f} gwei")
print(f"Swap cost estimate: ~${float(w3.from_wei(gas_price * 200000, 'ether')) * 2050:.4f}")
