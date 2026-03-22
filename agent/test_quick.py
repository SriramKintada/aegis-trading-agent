"""Quick smoke test for all AEGIS modules."""
import numpy as np
import pandas as pd

# Generate synthetic OHLCV
n = 200
rng = np.random.default_rng(42)
price = 3500.0
prices = [price]
for _ in range(n - 1):
    price *= (1 + rng.normal(0.0001, 0.008))
    prices.append(price)
closes = np.array(prices)
df = pd.DataFrame({
    "open": np.roll(closes, 1),
    "high": closes * 1.003,
    "low": closes * 0.997,
    "close": closes,
    "volume": rng.exponential(1000, n) * 100,
}, index=pd.date_range(end=pd.Timestamp.now(), periods=n, freq="1h"))
df.iloc[0, 0] = df.iloc[0, 3]

print("=== AEGIS Smoke Test ===\n")

# 1. HMM Regime Detection
from regime.hmm_detector import HMMRegimeDetector
detector = HMMRegimeDetector()
detector.fit(df)
result = detector.predict(df.iloc[-50:])
print(f"[HMM] Regime: {result.regime.label} (confidence: {result.confidence:.2%})")
print(f"[HMM] State mapping: {detector._state_to_regime}")

# 2. Momentum Strategy
from strategies.momentum import MomentumStrategy
mom = MomentumStrategy(fast_period=20, slow_period=50)
sig1 = mom.generate_signal(df, result)
print(f"[Momentum] Signal: {sig1.direction.value} (confidence: {sig1.confidence:.2%})")

# 3. Mean Reversion Strategy
from strategies.mean_reversion import MeanReversionStrategy
mr = MeanReversionStrategy(bb_period=20, bb_std=2.0)
sig2 = mr.generate_signal(df, result)
print(f"[MeanRev] Signal: {sig2.direction.value} (confidence: {sig2.confidence:.2%})")

# 4. Sentiment Pulse Strategy
from strategies.sentiment_pulse import SentimentPulseStrategy
sp = SentimentPulseStrategy(shift_threshold=0.3)
sig3 = sp.generate_signal(df, result, sentiment_score=0.5)
print(f"[Sentiment] Signal: {sig3.direction.value} (confidence: {sig3.confidence:.2%})")

# 5. Position Sizing
from risk.position_sizer import PositionSizer
sizer = PositionSizer()
size = sizer.quick_size(portfolio_value=100.0, regime=result.regime, confidence=0.7)
print(f"[Sizing] Position for $100 portfolio @ 70% confidence: ${size:.2f}")

# 6. Stop Manager
from risk.stop_manager import StopManager
stops = StopManager(atr_period=14)
from strategies.base_strategy import SignalDirection
stop_state = stops.create_stop(
    entry_price=3500.0,
    direction=SignalDirection.LONG,
    regime=result.regime,
    df=df,
)
print(f"[Stops] Entry: ${stop_state.entry_price:.2f} | Stop: ${stop_state.current_stop:.2f} | TP: ${stop_state.take_profit:.2f}")

# 7. Genetic Evolution
from evolution.genetic import GeneticEvolver
strategies = [mom, mr, sp]
evolver = GeneticEvolver(strategies)
print(f"[Evolution] Population size: {evolver.POPULATION_SIZE}")

# 8. Gas Optimizer
from execution.gas_optimizer import GasOptimizer
print(f"[Gas] Module loaded OK")

# 9. Bond Credit
from identity.bond_credit import BondCreditReporter
print(f"[Bond] Module loaded OK")

print("\n=== ALL TESTS PASSED ===")
