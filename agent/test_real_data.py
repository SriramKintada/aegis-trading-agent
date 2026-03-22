"""Test AEGIS against real ETH/USD market data."""
import asyncio
import aiohttp
import pandas as pd
import numpy as np

from regime.hmm_detector import HMMRegimeDetector
from regime.regime_types import Regime
from strategies.momentum import MomentumStrategy
from strategies.mean_reversion import MeanReversionStrategy
from strategies.sentiment_pulse import SentimentPulseStrategy
from risk.position_sizer import PositionSizer
from risk.stop_manager import StopManager
from strategies.base_strategy import SignalDirection


async def fetch_real_ohlcv(limit=500):
    """Fetch real ETH/USD hourly data from CryptoCompare."""
    url = "https://min-api.cryptocompare.com/data/v2/histohour"
    params = {"fsym": "ETH", "tsym": "USD", "limit": limit}
    async with aiohttp.ClientSession() as session:
        async with session.get(url, params=params) as resp:
            data = await resp.json()
            candles = data["Data"]["Data"]
            df = pd.DataFrame(candles)
            df = df.rename(columns={"time": "timestamp", "volumefrom": "volume"})
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
            df = df.set_index("timestamp")
            df = df[["open", "high", "low", "close", "volume"]].dropna()
            return df


async def main():
    print("=== AEGIS Real Data Test ===\n")
    
    # Fetch real data
    print("Fetching 500 hours of ETH/USD data...")
    df = await fetch_real_ohlcv(500)
    print(f"Got {len(df)} bars: {df.index[0]} to {df.index[-1]}")
    print(f"Price range: ${df['close'].min():.2f} - ${df['close'].max():.2f}")
    print(f"Current price: ${df['close'].iloc[-1]:.2f}\n")

    # Train HMM on full history
    detector = HMMRegimeDetector()
    detector.fit(df)
    
    # Predict current regime
    recent = df.iloc[-50:]
    regime = detector.predict(recent)
    print(f"Current Regime: {regime.regime.label} (confidence: {regime.confidence:.2%})")
    print(f"State mapping: {detector._state_to_regime}\n")

    # Test all strategies on current data
    strategies = [
        MomentumStrategy(fast_period=20, slow_period=50),
        MeanReversionStrategy(bb_period=20, bb_std=2.0),
        SentimentPulseStrategy(shift_threshold=0.3),
    ]
    
    sizer = PositionSizer()
    stops = StopManager(atr_period=14)
    
    for strat in strategies:
        signal = strat.generate_signal(df, regime)
        print(f"[{strat.name}]")
        print(f"  Direction: {signal.direction.value}")
        print(f"  Confidence: {signal.confidence:.2%}")
        if signal.is_actionable:
            print(f"  Entry: ${signal.entry_price:.2f}")
            print(f"  Stop: ${signal.stop_loss:.2f}")
            print(f"  Target: ${signal.take_profit:.2f}")
            print(f"  R:R = {signal.risk_reward:.2f}")
            
            # Size it
            size = sizer.quick_size(100.0, regime.regime, signal.confidence)
            print(f"  Size ($100 portfolio): ${size:.2f}")
            
            # Stop levels
            stop_state = stops.create_stop(
                signal.entry_price, signal.direction, regime.regime, df
            )
            print(f"  ATR Stop: ${stop_state.current_stop:.2f}")
            print(f"  ATR TP: ${stop_state.take_profit:.2f}")
        print()

    # Backtest: walk through last 200 bars
    print("=== Quick Backtest (last 200 bars) ===\n")
    portfolio = 100.0
    trades = []
    wins = 0
    losses = 0
    
    for i in range(100, len(df) - 1):
        window = df.iloc[max(0, i-200):i+1]
        if len(window) < 60:
            continue
            
        # Detect regime
        try:
            r = detector.predict(window.iloc[-50:])
        except Exception:
            continue
        
        # Get signals
        for strat in strategies:
            try:
                sig = strat.generate_signal(window, r)
            except Exception:
                continue
                
            if not sig.is_actionable or sig.confidence < 0.5:
                continue
            
            # Check next bar for outcome
            next_bar = df.iloc[i + 1]
            entry = sig.entry_price
            
            if sig.direction == SignalDirection.LONG:
                pnl_pct = (next_bar["close"] - entry) / entry
            elif sig.direction == SignalDirection.SHORT:
                pnl_pct = (entry - next_bar["close"]) / entry
            else:
                continue
            
            # Apply stop/TP
            if sig.stop_loss > 0:
                max_loss = abs(entry - sig.stop_loss) / entry
                pnl_pct = max(pnl_pct, -max_loss)
            if sig.take_profit > 0:
                max_gain = abs(sig.take_profit - entry) / entry
                pnl_pct = min(pnl_pct, max_gain)
            
            size = sizer.quick_size(portfolio, r.regime, sig.confidence)
            pnl_usd = size * pnl_pct
            portfolio += pnl_usd
            
            won = pnl_usd > 0
            if won:
                wins += 1
            else:
                losses += 1
            
            trades.append({
                "bar": i,
                "strategy": strat.name,
                "direction": sig.direction.value,
                "confidence": sig.confidence,
                "regime": r.regime.label,
                "pnl_pct": pnl_pct,
                "pnl_usd": pnl_usd,
                "portfolio": portfolio,
            })

    total = wins + losses
    print(f"Trades: {total}")
    if total > 0:
        print(f"Wins: {wins} | Losses: {losses} | Win Rate: {wins/total:.1%}")
        print(f"Starting: $100.00 | Final: ${portfolio:.2f}")
        print(f"Total Return: {(portfolio - 100) / 100:.2%}")
        
        # Per-strategy breakdown
        trade_df = pd.DataFrame(trades)
        print("\nPer-strategy breakdown:")
        for name, group in trade_df.groupby("strategy"):
            w = (group["pnl_usd"] > 0).sum()
            l = (group["pnl_usd"] <= 0).sum()
            total_pnl = group["pnl_usd"].sum()
            print(f"  {name}: {len(group)} trades | Win: {w}/{len(group)} | PnL: ${total_pnl:.2f}")
        
        # Per-regime breakdown
        print("\nPer-regime breakdown:")
        for regime_name, group in trade_df.groupby("regime"):
            total_pnl = group["pnl_usd"].sum()
            print(f"  {regime_name}: {len(group)} trades | PnL: ${total_pnl:.2f}")
    else:
        print("No trades generated — strategies are too conservative or market is flat")
        print("This is OK — means no false signals. Adjust thresholds if needed.")
    
    print(f"\nFinal Portfolio: ${portfolio:.2f}")


if __name__ == "__main__":
    asyncio.run(main())
