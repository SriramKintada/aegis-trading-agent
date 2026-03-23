"""Verify Aerodrome pool exists and we can read prices."""
from web3 import Web3
import json

w3 = Web3(Web3.HTTPProvider('https://mainnet.base.org'))
wallet = '0x86c2C9b1Fc8D9662dA6AFB44541eb5964b5dc424'

WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")

# Aerodrome WETH/USDC volatile pool
AERO_POOL = Web3.to_checksum_address("0xB4885Bc63399BF5518b994c1d0C153334Ee579D0")
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

pool = w3.eth.contract(address=AERO_POOL, abi=AERO_POOL_ABI)

print("Testing Aerodrome pool...")
try:
    t0 = pool.functions.token0().call()
    t1 = pool.functions.token1().call()
    stable = pool.functions.stable().call()
    print(f"  Token0: {t0}")
    print(f"  Token1: {t1}")
    print(f"  Stable: {stable}")
    print(f"  WETH match: {t0.lower() == WETH.lower() or t1.lower() == WETH.lower()}")
    print(f"  USDC match: {t0.lower() == USDC.lower() or t1.lower() == USDC.lower()}")
except Exception as e:
    print(f"  Pool query error: {e}")
    print("  Trying alternative pool addresses...")
    # Try other known Aerodrome WETH/USDC pools
    alt_pools = [
        "0xcDAC0d6c6C59727a65F871236188350531885C43",  # vAMM-WETH/USDC
        "0x6cDcb1C4A4D1C3C6d054b27AC5B77e89eAFb971d",  # sAMM-WETH/USDC
    ]
    for addr in alt_pools:
        try:
            alt = w3.eth.contract(address=Web3.to_checksum_address(addr), abi=AERO_POOL_ABI)
            t0 = alt.functions.token0().call()
            t1 = alt.functions.token1().call()
            print(f"  Pool {addr[:10]}...: token0={t0[:10]}, token1={t1[:10]}")
        except Exception as e2:
            print(f"  Pool {addr[:10]}... error: {e2}")

# Test getAmountOut
try:
    test_amount = w3.to_wei(0.001, "ether")
    out = pool.functions.getAmountOut(test_amount, WETH).call()
    eth_price_aero = (out / 1e6) / 0.001
    print(f"\n  Aero price (0.001 ETH -> USDC): ${eth_price_aero:.2f}")
    
    # Compare with Uniswap
    UNI_POOL = Web3.to_checksum_address("0xd0b53D9277642d899DF5C87A3966A349A798F224")
    UNI_POOL_ABI = [{"inputs":[],"name":"slot0","outputs":[
        {"name":"sqrtPriceX96","type":"uint160"},
        {"name":"tick","type":"int24"},
        {"name":"observationIndex","type":"uint16"},
        {"name":"observationCardinality","type":"uint16"},
        {"name":"observationCardinalityNext","type":"uint16"},
        {"name":"feeProtocol","type":"uint8"},
        {"name":"unlocked","type":"bool"}
    ],"type":"function"}]
    uni = w3.eth.contract(address=UNI_POOL, abi=UNI_POOL_ABI)
    slot0 = uni.functions.slot0().call()
    sqrtPriceX96 = slot0[0]
    uni_price = (sqrtPriceX96 / (2**96))**2 * (10**12)
    
    spread = abs(eth_price_aero - uni_price) / min(uni_price, eth_price_aero) * 100
    print(f"  Uni price:  ${uni_price:.2f}")
    print(f"  Aero price: ${eth_price_aero:.2f}")
    print(f"  Spread:     {spread:.4f}%")
    print(f"  Arb viable (>0.40%): {'YES 🔥' if spread > 0.40 else 'NO'}")
    
except Exception as e:
    print(f"\n  getAmountOut error: {e}")

# Also get reserves for depth check
try:
    r0, r1, ts = pool.functions.getReserves().call()
    print(f"\n  Reserves:")
    print(f"    Reserve0: {r0}")
    print(f"    Reserve1: {r1}")
    print(f"    Last update: {ts}")
except Exception as e:
    print(f"  Reserves error: {e}")
