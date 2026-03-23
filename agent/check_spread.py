from web3 import Web3
import time

w3 = Web3(Web3.HTTPProvider('https://mainnet.base.org'))

WETH = Web3.to_checksum_address('0x4200000000000000000000000000000000000006')
USDC = Web3.to_checksum_address('0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913')

# Native USDC Aerodrome volatile pool
AERO_POOL = Web3.to_checksum_address('0xcDAC0d6c6C59727a65F871236188350531885C43')
ABI = [
    {'inputs':[{'name':'amountIn','type':'uint256'},{'name':'tokenIn','type':'address'}],'name':'getAmountOut','outputs':[{'name':'','type':'uint256'}],'type':'function'},
    {'inputs':[],'name':'token0','outputs':[{'name':'','type':'address'}],'type':'function'},
    {'inputs':[],'name':'token1','outputs':[{'name':'','type':'address'}],'type':'function'},
]

pool = w3.eth.contract(address=AERO_POOL, abi=ABI)
t0 = pool.functions.token0().call()
t1 = pool.functions.token1().call()
print(f'Token0: {t0}')
print(f'Token1: {t1}')

time.sleep(1)

# Price quote: 0.001 ETH -> USDC
test = w3.to_wei(0.001, 'ether')
out = pool.functions.getAmountOut(test, WETH).call()
aero_price = (out / 1e6) / 0.001
print(f'Aero price (native USDC): ${aero_price:.2f}')

time.sleep(1)

# Uniswap price
UNI_POOL = Web3.to_checksum_address('0xd0b53D9277642d899DF5C87A3966A349A798F224')
UNI_ABI = [{'inputs':[],'name':'slot0','outputs':[
    {'name':'sqrtPriceX96','type':'uint160'},{'name':'tick','type':'int24'},
    {'name':'observationIndex','type':'uint16'},{'name':'observationCardinality','type':'uint16'},
    {'name':'observationCardinalityNext','type':'uint16'},{'name':'feeProtocol','type':'uint8'},
    {'name':'unlocked','type':'bool'}],'type':'function'}]
uni = w3.eth.contract(address=UNI_POOL, abi=UNI_ABI)
slot0 = uni.functions.slot0().call()
uni_price = (slot0[0] / (2**96))**2 * (10**12)
print(f'Uni price: ${uni_price:.2f}')

spread = abs(aero_price - uni_price) / min(uni_price, aero_price) * 100
print(f'Spread: {spread:.4f}%')
viable = "YES" if spread > 0.40 else "NO"
print(f'Arb viable (need >0.40%): {viable}')
