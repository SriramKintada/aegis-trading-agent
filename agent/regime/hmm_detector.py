"""
AEGIS — HMM Regime Detector
Uses GaussianHMM from hmmlearn to classify market regime from OHLCV features.

Features:
  - Log returns (1-period)
  - Realized volatility (rolling 24-bar std of log returns)
  - Volume z-score (20-bar rolling)
  - Momentum (20-bar price change %)
  - Sentiment score (from FinBERT, optional)

States: BULL_TRENDING, BEAR_TRENDING, HIGH_VOL_CHOPPY, MEAN_REVERTING
"""

import logging
import pickle
from pathlib import Path
from typing import Optional

import numpy as np
import pandas as pd
from hmmlearn.hmm import GaussianHMM
from sklearn.preprocessing import StandardScaler

from regime.regime_types import Regime, RegimeResult

logger = logging.getLogger(__name__)

MODEL_DIR = Path(__file__).parent / "models"
MODEL_DIR.mkdir(exist_ok=True)


class HMMRegimeDetector:
    """
    4-state Gaussian Hidden Markov Model for market regime detection.

    Usage:
        detector = HMMRegimeDetector()
        detector.fit(historical_df)
        result = detector.predict(recent_df, sentiment_score=0.1)
    """

    N_STATES = 4
    N_ITER = 200
    RANDOM_STATE = 42

    def __init__(self, model_path: Optional[Path] = None):
        self.model: Optional[GaussianHMM] = None
        self.scaler = StandardScaler()
        self._state_to_regime: dict[int, Regime] = {}
        self._is_fitted = False
        self._model_path = model_path or MODEL_DIR / "hmm_model.pkl"

        # Try loading pre-trained model
        if self._model_path.exists():
            self._load()

    # ─── Feature Engineering ──────────────────────────────────────────────────

    def _build_features(
        self,
        df: pd.DataFrame,
        sentiment_score: Optional[float] = None,
    ) -> np.ndarray:
        """
        Construct feature matrix from OHLCV DataFrame.

        Args:
            df: DataFrame with columns [open, high, low, close, volume]
            sentiment_score: Optional scalar sentiment in [-1, 1]

        Returns:
            np.ndarray of shape (n_samples, n_features)
        """
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]

        # 1) Log returns
        df["log_ret"] = np.log(df["close"] / df["close"].shift(1))

        # 2) Realized volatility (rolling 24-bar)
        df["real_vol"] = df["log_ret"].rolling(24, min_periods=5).std()

        # 3) Volume z-score (rolling 20-bar)
        vol_mean = df["volume"].rolling(20, min_periods=5).mean()
        vol_std = df["volume"].rolling(20, min_periods=5).std().replace(0, 1e-9)
        df["vol_z"] = (df["volume"] - vol_mean) / vol_std

        # 4) Momentum (20-bar price change %)
        df["momentum_20"] = df["close"].pct_change(20)

        # 5) High-Low range ratio (volatility proxy)
        df["hl_ratio"] = (df["high"] - df["low"]) / df["close"]

        df = df.dropna()

        feature_cols = ["log_ret", "real_vol", "vol_z", "momentum_20", "hl_ratio"]
        X = df[feature_cols].values.astype(float)

        # Clip extreme outliers
        X = np.clip(X, -10, 10)

        # Append sentiment as constant feature across all rows if provided
        if sentiment_score is not None:
            sent_col = np.full((len(X), 1), float(np.clip(sentiment_score, -1, 1)))
            X = np.hstack([X, sent_col])

        return X

    # ─── State Labeling ───────────────────────────────────────────────────────

    def _label_states(self, df: pd.DataFrame, states: np.ndarray) -> dict[int, Regime]:
        """
        After fitting, map HMM state indices to economic regime labels.
        Uses mean log_return and realized_vol to identify regime semantics.
        """
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        df["log_ret"] = np.log(df["close"] / df["close"].shift(1))
        df["real_vol"] = df["log_ret"].rolling(24, min_periods=5).std()
        df = df.dropna()

        # Align lengths
        n = min(len(df), len(states))
        df = df.iloc[-n:]
        states = states[-n:]

        state_stats: dict[int, dict] = {}
        for s in range(self.N_STATES):
            mask = states == s
            if mask.sum() == 0:
                state_stats[s] = {"mean_ret": 0.0, "mean_vol": 0.0}
                continue
            state_stats[s] = {
                "mean_ret": df.loc[df.index[mask], "log_ret"].mean(),
                "mean_vol": df.loc[df.index[mask], "real_vol"].mean(),
            }

        # Sort by vol to find HIGH_VOL_CHOPPY (highest vol)
        sorted_by_vol = sorted(state_stats.items(), key=lambda x: x[1]["mean_vol"])
        high_vol_state = sorted_by_vol[-1][0]

        # Among the rest, sort by return
        remaining = [s for s in range(self.N_STATES) if s != high_vol_state]
        sorted_by_ret = sorted(remaining, key=lambda s: state_stats[s]["mean_ret"])

        bear_trending = sorted_by_ret[0]     # Lowest return
        mean_reverting = sorted_by_ret[1]    # Second lowest — near-zero return, lower vol
        bull_trending = sorted_by_ret[-1]    # Highest return

        mapping = {
            bull_trending: Regime.BULL_TRENDING,
            bear_trending: Regime.BEAR_TRENDING,
            high_vol_state: Regime.HIGH_VOL_CHOPPY,
            mean_reverting: Regime.MEAN_REVERTING,
        }

        logger.info(f"State → Regime mapping: {mapping}")
        return mapping

    # ─── Training ─────────────────────────────────────────────────────────────

    def fit(self, df: pd.DataFrame, sentiment_score: float = 0.0) -> "HMMRegimeDetector":
        """
        Fit the HMM on historical OHLCV data.

        Args:
            df: DataFrame with [open, high, low, close, volume] — minimum 100 rows
            sentiment_score: Optional constant sentiment for training period

        Returns:
            self (fluent interface)
        """
        if len(df) < 50:
            raise ValueError(f"Need at least 50 rows for training, got {len(df)}")

        X_raw = self._build_features(df, sentiment_score)
        X_scaled = self.scaler.fit_transform(X_raw)

        self.model = GaussianHMM(
            n_components=self.N_STATES,
            covariance_type="full",
            n_iter=self.N_ITER,
            random_state=self.RANDOM_STATE,
            verbose=False,
            tol=1e-4,
        )
        self.model.fit(X_scaled)

        # Label states
        states = self.model.predict(X_scaled)
        self._state_to_regime = self._label_states(df.iloc[-len(X_scaled):], states)
        self._is_fitted = True

        logger.info(
            f"HMM fitted on {len(X_scaled)} bars | "
            f"Score: {self.model.score(X_scaled):.4f}"
        )

        self._save()
        return self

    # ─── Inference ────────────────────────────────────────────────────────────

    def predict(
        self,
        df: pd.DataFrame,
        sentiment_score: float = 0.0,
    ) -> RegimeResult:
        """
        Predict current market regime.

        Args:
            df: Recent OHLCV DataFrame (at least 30 rows recommended)
            sentiment_score: Current FinBERT composite score in [-1, 1]

        Returns:
            RegimeResult with regime label + confidence
        """
        if not self._is_fitted:
            logger.warning("HMM not fitted — fitting on provided data now")
            self.fit(df, sentiment_score)

        X_raw = self._build_features(df, sentiment_score)
        if len(X_raw) == 0:
            logger.error("Feature engineering produced empty array — returning default regime")
            return RegimeResult(
                regime=Regime.HIGH_VOL_CHOPPY,
                confidence=0.25,
                probabilities={r: 0.25 for r in Regime},
            )

        X_scaled = self.scaler.transform(X_raw)

        # Get posterior probabilities for the last observation
        log_posteriors = self.model.predict_proba(X_scaled)
        last_posteriors = log_posteriors[-1]  # shape: (n_states,)

        # Map state index → regime
        predicted_state = int(np.argmax(last_posteriors))
        predicted_regime = self._state_to_regime.get(predicted_state, Regime.HIGH_VOL_CHOPPY)

        probs_by_regime: dict[Regime, float] = {}
        for state_idx, prob in enumerate(last_posteriors):
            regime = self._state_to_regime.get(state_idx, Regime.HIGH_VOL_CHOPPY)
            probs_by_regime[regime] = probs_by_regime.get(regime, 0.0) + float(prob)

        confidence = float(last_posteriors[predicted_state])

        logger.debug(
            f"Regime: {predicted_regime.label} | "
            f"Confidence: {confidence:.2%} | "
            f"Probs: { {r.label: f'{p:.2%}' for r, p in probs_by_regime.items()} }"
        )

        return RegimeResult(
            regime=predicted_regime,
            confidence=confidence,
            probabilities=probs_by_regime,
        )

    # ─── Persistence ──────────────────────────────────────────────────────────

    def _save(self):
        data = {
            "model": self.model,
            "scaler": self.scaler,
            "state_to_regime": self._state_to_regime,
        }
        with open(self._model_path, "wb") as f:
            pickle.dump(data, f)
        logger.info(f"HMM model saved to {self._model_path}")

    def _load(self):
        try:
            with open(self._model_path, "rb") as f:
                data = pickle.load(f)
            self.model = data["model"]
            self.scaler = data["scaler"]
            self._state_to_regime = data["state_to_regime"]
            self._is_fitted = True
            logger.info(f"HMM model loaded from {self._model_path}")
        except Exception as e:
            logger.warning(f"Could not load HMM model: {e}")
            self._is_fitted = False
