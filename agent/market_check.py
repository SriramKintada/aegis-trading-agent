"""Check current market conditions and generate a signal."""
import asyncio
import aiohttp
import pandas as pd
import numpy as np
from regime.hmm_detector import HMMRegimeDetector

async def check():
    url = "https://min-api.cryptocompare.com/data/v2/histohour"
    params = {"fsym": "ETH", "tsym": "USD", "limit": 200}
    async with aiohttp.ClientSession() as s:
        async with s.get(url, params=params) as r:
            data = await r.json()
            candles = data["Data"]["Data"]
            df = pd.DataFrame(candles)
            df = df.rename(columns={"time": "timestamp", "volumefrom": "volume"})
            df["timestamp"] = pd.to_datetime(df["timestamp"], unit="s")
            df = df.set_index("timestamp")
            df = df[["open", "high", "low", "close", "volume"]].dropna()

            price = df["close"].iloc[-1]
            price_1h = df["close"].iloc[-2]
            price_4h = df["close"].iloc[-5]
            price_24h = df["close"].iloc[-25]

            print(f"ETH Price NOW: ${price:.2f}")
            print(f"1h change:  {(price - price_1h) / price_1h * 100:+.2f}%")
            print(f"4h change:  {(price - price_4h) / price_4h * 100:+.2f}%")
            print(f"24h change: {(price - price_24h) / price_24h * 100:+.2f}%")
            print()

            # Regime
            det = HMMRegimeDetector()
            det.fit(df)
            regime = det.predict(df.iloc[-50:])
            print(f"Regime: {regime.regime.label} ({regime.confidence:.0%})")

            # RSI
            delta = df["close"].diff()
            gain = delta.clip(lower=0).ewm(com=13).mean()
            loss = (-delta.clip(upper=0)).ewm(com=13).mean()
            rs = gain / loss.replace(0, 1e-9)
            rsi = 100 - (100 / (1 + rs))
            print(f"RSI(14): {rsi.iloc[-1]:.1f}")

            # Trend
            sma20 = df["close"].rolling(20).mean().iloc[-1]
            sma50 = df["close"].rolling(50).mean().iloc[-1]
            ema5 = df["close"].ewm(span=5).mean().iloc[-1]
            ema15 = df["close"].ewm(span=15).mean().iloc[-1]
            print(f"SMA20: ${sma20:.2f} | SMA50: ${sma50:.2f}")
            print(f"EMA5: ${ema5:.2f} | EMA15: ${ema15:.2f}")

            above_sma20 = "ABOVE" if price > sma20 else "BELOW"
            trend = "BULLISH" if sma20 > sma50 else "BEARISH"
            fast_trend = "UP" if ema5 > ema15 else "DOWN"
            print(f"Price vs SMA20: {above_sma20}")
            print(f"SMA20 vs SMA50: {trend}")
            print(f"Fast trend (EMA5 vs EMA15): {fast_trend}")
            print()

            # BB
            bb_mid = sma20
            bb_std = df["close"].rolling(20).std().iloc[-1]
            bb_upper = bb_mid + 2 * bb_std
            bb_lower = bb_mid - 2 * bb_std
            bb_pos = (price - bb_lower) / (bb_upper - bb_lower)
            print(f"Bollinger: lower=${bb_lower:.2f} mid=${bb_mid:.2f} upper=${bb_upper:.2f}")
            print(f"BB position: {bb_pos:.2f} (0=lower band, 1=upper band)")
            print()

            # Volume
            vol_avg = df["volume"].rolling(20).mean().iloc[-1]
            vol_now = df["volume"].iloc[-1]
            vol_ratio = vol_now / vol_avg if vol_avg > 0 else 0
            print(f"Volume ratio (vs 20-bar avg): {vol_ratio:.2f}x")
            print()

            # SIGNAL RECOMMENDATION
            print("=" * 50)
            print("SIGNAL ASSESSMENT")
            print("=" * 50)
            
            signals = []
            if rsi.iloc[-1] < 30:
                signals.append("RSI oversold -> BUY signal")
            elif rsi.iloc[-1] > 70:
                signals.append("RSI overbought -> SELL signal")
            
            if fast_trend == "UP" and trend == "BULLISH":
                signals.append("Strong uptrend -> BUY bias")
            elif fast_trend == "DOWN" and trend == "BEARISH":
                signals.append("Strong downtrend -> SELL bias")
            
            if bb_pos < 0.1:
                signals.append("Near lower BB -> potential BUY")
            elif bb_pos > 0.9:
                signals.append("Near upper BB -> potential SELL")
            
            if vol_ratio > 2.0:
                signals.append(f"High volume ({vol_ratio:.1f}x) -> confirms direction")
            
            if signals:
                for s in signals:
                    print(f"  {s}")
            else:
                print("  No strong signals - market is neutral")
            
            print()
            
            # What should we do with $8?
            print("=" * 50)
            print("RECOMMENDATION FOR $8.82 PORTFOLIO")
            print("=" * 50)
            
            if regime.regime.label in ("BULL_TRENDING",) and fast_trend == "UP":
                print("HOLD ETH - uptrend, let it ride")
                print("Execute proof-of-concept trades for hackathon (small amounts)")
            elif regime.regime.label in ("BEAR_TRENDING",) and fast_trend == "DOWN":
                print("SWAP to USDC - protect from downside")
                print("Swap back when trend reverses for small profit")
            else:
                print("NEUTRAL - execute round-trip trades for hackathon proof")
                print("Keep most in ETH, trade small amounts for TxIDs")

asyncio.run(check())
