"""
AEGIS Mainnet Trader â€” Base Chain
Executes real trades on Base mainnet via Uniswap V3 SwapRouter02.

Safety:
  - Max 0.003 ETH used for trades (rest reserved for gas)
  - Each swap uses 0.001 ETH (~$2)
  - All TxIDs logged to mainnet_trades.json
  - Dry-run mode if DRY_RUN=true in env
"""

import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

from dotenv import load_dotenv
from web3 import Web3
from eth_account import Account

# Load env from parent dir
load_dotenv(Path(__file__).parent.parent / ".env")

# â”€â”€ Config â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

RPC_URL = os.getenv("RPC_URL", "https://mainnet.base.org")
PRIVATE_KEY = os.getenv("PRIVATE_KEY", "")
DRY_RUN = os.getenv("DRY_RUN", "false").lower() == "true"
CHAIN_ID = 8453

# Base Mainnet Addresses
WETH = Web3.to_checksum_address("0x4200000000000000000000000000000000000006")
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
SWAP_ROUTER = Web3.to_checksum_address("0x2626664c2603336E57B271c5C0b26F421741e481")

# Safety limits
MAX_TRADE_ETH = 0.001       # Max ETH per single swap
MAX_TOTAL_ETH = 0.003       # Max total ETH to use for all trades
MIN_GAS_RESERVE = 0.001     # Keep this much ETH for gas

# ABIs
WETH_ABI = json.loads('[{"constant":false,"inputs":[],"name":"deposit","outputs":[],"payable":true,"stateMutability":"payable","type":"function"},{"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":false,"inputs":[{"name":"wad","type":"uint256"}],"name":"withdraw","outputs":[],"type":"function"}]')

USDC_ABI = json.loads('[{"constant":false,"inputs":[{"name":"spender","type":"address"},{"name":"amount","type":"uint256"}],"name":"approve","outputs":[{"name":"","type":"bool"}],"type":"function"},{"constant":true,"inputs":[{"name":"account","type":"address"}],"name":"balanceOf","outputs":[{"name":"","type":"uint256"}],"type":"function"},{"constant":true,"inputs":[],"name":"decimals","outputs":[{"name":"","type":"uint8"}],"type":"function"}]')

SWAP_ROUTER_ABI = json.loads('[{"inputs":[{"components":[{"name":"tokenIn","type":"address"},{"name":"tokenOut","type":"address"},{"name":"fee","type":"uint24"},{"name":"recipient","type":"address"},{"name":"amountIn","type":"uint256"},{"name":"amountOutMinimum","type":"uint256"},{"name":"sqrtPriceLimitX96","type":"uint160"}],"name":"params","type":"tuple"}],"name":"exactInputSingle","outputs":[{"name":"amountOut","type":"uint256"}],"stateMutability":"payable","type":"function"}]')

# â”€â”€ State â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€â”€

trade_log = []
total_eth_used = 0.0


def log_trade(action, tx_hash, details=""):
    """Log a trade with timestamp and basescan link."""
    entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "action": action,
        "tx_hash": tx_hash,
        "basescan": f"https://basescan.org/tx/{tx_hash}",
        "details": details,
    }
    trade_log.append(entry)
    print(f"  TX: {tx_hash}")
    print(f"  Basescan: https://basescan.org/tx/{tx_hash}")
    # Save after every trade
    save_trades()


def save_trades():
    """Save trade log to JSON."""
    path = Path(__file__).parent / "mainnet_trades.json"
    with open(path, "w") as f:
        json.dump(trade_log, f, indent=2)


def get_gas_params(w3):
    """Get EIP-1559 gas parameters for Base."""
    latest = w3.eth.get_block("latest")
    base_fee = latest.get("baseFeePerGas", 1000000)  # ~0.001 gwei on Base
    max_priority = w3.to_wei(0.001, "gwei")  # tiny tip
    max_fee = base_fee * 2 + max_priority
    return {
        "maxFeePerGas": max_fee,
        "maxPriorityFeePerGas": max_priority,
    }


def send_tx(w3, account, tx_dict):
    """Sign, send, and wait for a transaction. Returns receipt or None."""
    try:
        # Add gas params
        gas_params = get_gas_params(w3)
        tx_dict.update(gas_params)
        tx_dict["chainId"] = CHAIN_ID
        tx_dict["nonce"] = w3.eth.get_transaction_count(account.address)

        # Estimate gas if not set
        if "gas" not in tx_dict:
            try:
                tx_dict["gas"] = w3.eth.estimate_gas(tx_dict)
            except Exception as e:
                print(f"  Gas estimation failed: {e}")
                tx_dict["gas"] = 150000  # safe fallback

        if DRY_RUN:
            print(f"  [DRY RUN] Would send tx: {tx_dict.get('to', 'N/A')}")
            return None

        signed = account.sign_transaction(tx_dict)
        tx_hash = w3.eth.send_raw_transaction(signed.raw_transaction)
        print(f"  Sent! Waiting for confirmation...")
        receipt = w3.eth.wait_for_transaction_receipt(tx_hash, timeout=60)

        if receipt["status"] == 1:
            print(f"  Confirmed in block {receipt['blockNumber']}")
        else:
            print(f"  FAILED! Status: {receipt['status']}")

        return receipt
    except Exception as e:
        print(f"  ERROR: {e}")
        return None


def check_balances(w3, wallet, weth_contract, usdc_contract):
    """Print all balances."""
    eth = w3.from_wei(w3.eth.get_balance(wallet), "ether")
    weth = w3.from_wei(weth_contract.functions.balanceOf(wallet).call(), "ether")
    usdc_raw = usdc_contract.functions.balanceOf(wallet).call()
    usdc = usdc_raw / 1e6  # USDC has 6 decimals
    print(f"\n  Balances:")
    print(f"    ETH:  {eth:.6f} (~${float(eth) * 2100:.2f})")
    print(f"    WETH: {weth:.6f} (~${float(weth) * 2100:.2f})")
    print(f"    USDC: {usdc:.6f}")
    return float(eth), float(weth), usdc


def main():
    global total_eth_used

    print("=" * 60)
    print("  AEGIS Mainnet Trader â€” Base Chain")
    print("=" * 60)

    if not PRIVATE_KEY:
        print("ERROR: No PRIVATE_KEY in .env")
        sys.exit(1)

    # Connect
    w3 = Web3(Web3.HTTPProvider(RPC_URL))
    if not w3.is_connected():
        print("ERROR: Cannot connect to Base RPC")
        sys.exit(1)
    print(f"Connected to Base (chain {w3.eth.chain_id})")

    account = Account.from_key(PRIVATE_KEY)
    wallet = account.address
    print(f"Wallet: {wallet}")

    if DRY_RUN:
        print("*** DRY RUN MODE â€” no real transactions ***")

    # Contracts
    weth_contract = w3.eth.contract(address=WETH, abi=WETH_ABI)
    usdc_contract = w3.eth.contract(address=USDC, abi=USDC_ABI)
    swap_router = w3.eth.contract(address=SWAP_ROUTER, abi=SWAP_ROUTER_ABI)

    # Check starting balances
    print("\n--- Starting Balances ---")
    eth_bal, weth_bal, usdc_bal = check_balances(w3, wallet, weth_contract, usdc_contract)

    available = eth_bal - MIN_GAS_RESERVE
    if available < MAX_TRADE_ETH:
        print(f"\nERROR: Not enough ETH. Have {eth_bal:.6f}, need {MAX_TRADE_ETH + MIN_GAS_RESERVE:.6f}")
        sys.exit(1)

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # TRADE 1: Wrap ETH â†’ WETH
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    wrap_amount = w3.to_wei(MAX_TRADE_ETH, "ether")
    print(f"\n--- Trade 1: Wrap {MAX_TRADE_ETH} ETH â†’ WETH ---")

    tx = weth_contract.functions.deposit().build_transaction({
        "from": wallet,
        "value": wrap_amount,
        "gas": 50000,
    })
    receipt = send_tx(w3, account, tx)
    if receipt and receipt["status"] == 1:
        log_trade("WRAP_ETH_TO_WETH", receipt["transactionHash"].hex(),
                  f"Wrapped {MAX_TRADE_ETH} ETH to WETH")
        total_eth_used += MAX_TRADE_ETH
    elif not DRY_RUN:
        print("  Wrap failed! Aborting.")
        save_trades()
        return

    time.sleep(2)

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # TRADE 2: Approve WETH for SwapRouter
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    print(f"\n--- Trade 2: Approve WETH for Uniswap Router ---")
    approve_amount = w3.to_wei(1, "ether")  # approve 1 WETH (more than we need)

    tx = weth_contract.functions.approve(SWAP_ROUTER, approve_amount).build_transaction({
        "from": wallet,
        "gas": 60000,
    })
    receipt = send_tx(w3, account, tx)
    if receipt and receipt["status"] == 1:
        log_trade("APPROVE_WETH", receipt["transactionHash"].hex(),
                  f"Approved WETH spending for SwapRouter02")
    else:
        print("  Approve failed! Aborting.")
        save_trades()
        return

    time.sleep(2)

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # TRADE 3: Swap WETH â†’ USDC on Uniswap
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    swap_amount = w3.to_wei(MAX_TRADE_ETH, "ether")
    # Min out: 0 for now (we accept any amount â€” small trade, slippage doesn't matter much)
    # In production you'd calculate from oracle price
    min_out = 0

    print(f"\n--- Trade 3: Swap {MAX_TRADE_ETH} WETH â†’ USDC ---")

    swap_params = (
        WETH,           # tokenIn
        USDC,           # tokenOut
        500,            # fee tier (0.05%)
        wallet,         # recipient
        swap_amount,    # amountIn
        min_out,        # amountOutMinimum
        0,              # sqrtPriceLimitX96 (0 = no limit)
    )

    tx = swap_router.functions.exactInputSingle(swap_params).build_transaction({
        "from": wallet,
        "gas": 200000,
    })
    receipt = send_tx(w3, account, tx)
    if receipt and receipt["status"] == 1:
        log_trade("SWAP_WETH_TO_USDC", receipt["transactionHash"].hex(),
                  f"Swapped {MAX_TRADE_ETH} WETH to USDC via Uniswap V3")
    else:
        print("  Swap failed!")

    time.sleep(2)

    # Check balances after swap
    print("\n--- Post-Swap Balances ---")
    eth_bal, weth_bal, usdc_bal = check_balances(w3, wallet, weth_contract, usdc_contract)

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # TRADE 4: Approve USDC for SwapRouter (for reverse swap)
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    if usdc_bal > 0:
        print(f"\n--- Trade 4: Approve USDC for Uniswap Router ---")
        usdc_approve = int(usdc_bal * 1e6)  # USDC has 6 decimals

        tx = usdc_contract.functions.approve(SWAP_ROUTER, usdc_approve).build_transaction({
            "from": wallet,
            "gas": 60000,
        })
        receipt = send_tx(w3, account, tx)
        if receipt and receipt["status"] == 1:
            log_trade("APPROVE_USDC", receipt["transactionHash"].hex(),
                      f"Approved USDC spending for SwapRouter02")

        time.sleep(2)

        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        # TRADE 5: Swap USDC â†’ WETH (round trip)
        # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
        usdc_swap_amount = int(usdc_bal * 1e6)  # All USDC back to WETH
        print(f"\n--- Trade 5: Swap {usdc_bal:.2f} USDC â†’ WETH ---")

        swap_params = (
            USDC,               # tokenIn
            WETH,               # tokenOut
            500,                # fee tier
            wallet,             # recipient
            usdc_swap_amount,   # amountIn
            0,                  # amountOutMinimum
            0,                  # sqrtPriceLimitX96
        )

        tx = swap_router.functions.exactInputSingle(swap_params).build_transaction({
            "from": wallet,
            "gas": 200000,
        })
        receipt = send_tx(w3, account, tx)
        if receipt and receipt["status"] == 1:
            log_trade("SWAP_USDC_TO_WETH", receipt["transactionHash"].hex(),
                      f"Swapped {usdc_bal:.2f} USDC back to WETH via Uniswap V3")

        time.sleep(2)

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # TRADE 6: Unwrap WETH â†’ ETH (get ETH back)
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    _, weth_bal_final, _ = check_balances(w3, wallet, weth_contract, usdc_contract)
    if weth_bal_final > 0:
        unwrap_amount = w3.to_wei(weth_bal_final, "ether")
        print(f"\n--- Trade 6: Unwrap {weth_bal_final:.6f} WETH â†’ ETH ---")

        tx = weth_contract.functions.withdraw(unwrap_amount).build_transaction({
            "from": wallet,
            "gas": 50000,
        })
        receipt = send_tx(w3, account, tx)
        if receipt and receipt["status"] == 1:
            log_trade("UNWRAP_WETH_TO_ETH", receipt["transactionHash"].hex(),
                      f"Unwrapped {weth_bal_final:.6f} WETH back to ETH")

    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    # Final Summary
    # â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•â•
    print("\n" + "=" * 60)
    print("  TRADE SUMMARY")
    print("=" * 60)

    print(f"\n  Total trades: {len(trade_log)}")
    print(f"  Total ETH used for trades: {total_eth_used:.6f}")
    print(f"\n  --- Final Balances ---")
    check_balances(w3, wallet, weth_contract, usdc_contract)

    print(f"\n  All TxIDs:")
    for t in trade_log:
        print(f"    [{t['action']}] {t['basescan']}")

    print(f"\n  Trades saved to: mainnet_trades.json")
    save_trades()
    print("\n  Done!")


if __name__ == "__main__":
    main()

