"""
AEGIS — FinBERT Sentiment Scorer
Uses HuggingFace ProsusAI/finbert for financial text sentiment.
Falls back to keyword-based scoring if model unavailable.
"""

import logging
import time
from collections import deque
from typing import Optional

import numpy as np

logger = logging.getLogger(__name__)

# Keyword fallback: words that indicate sentiment direction
BULLISH_KEYWORDS = {
    "surge", "rally", "bull", "bullish", "pump", "gain", "moon", "breakout",
    "record", "high", "all-time", "adoption", "launch", "partnership", "buy",
    "upgrade", "positive", "growth", "rise", "rising", "up", "recovery",
    "institutional", "approve", "approval", "etf", "inflow", "buying",
}
BEARISH_KEYWORDS = {
    "crash", "dump", "bear", "bearish", "drop", "fall", "falling", "down",
    "plunge", "low", "hack", "exploit", "ban", "restrict", "regulation",
    "sell", "selling", "fear", "panic", "loss", "warning", "lawsuit",
    "lawsuit", "investigation", "outflow", "liquidation", "liquidate",
}


def _keyword_score(text: str) -> float:
    """
    Simple keyword-based sentiment score in [-1, 1].
    Positive = bullish, negative = bearish.
    """
    words = set(text.lower().split())
    bull_hits = len(words & BULLISH_KEYWORDS)
    bear_hits = len(words & BEARISH_KEYWORDS)
    total = bull_hits + bear_hits
    if total == 0:
        return 0.0
    return (bull_hits - bear_hits) / total


class FinBERTScorer:
    """
    Financial sentiment scorer using ProsusAI/finbert.

    Falls back to keyword scoring if:
      - transformers not installed
      - model download fails
      - Device memory insufficient

    Maintains a rolling weighted sentiment score for AEGIS.
    """

    MODEL_NAME = "ProsusAI/finbert"

    def __init__(
        self,
        window_size: int = 20,         # Rolling window for composite score
        decay_factor: float = 0.85,    # Recency weighting (recent = higher weight)
        device: str = "cpu",           # "cpu" or "cuda" or "mps"
        use_fp16: bool = False,
    ):
        self.window_size = window_size
        self.decay_factor = decay_factor
        self.device = device
        self.use_fp16 = use_fp16

        self._pipeline = None
        self._finbert_available = False
        self._score_history: deque = deque(maxlen=window_size)  # (timestamp, score, weight)

        self._try_load_finbert()

    def _try_load_finbert(self):
        """Attempt to load FinBERT pipeline. Silently fall back on failure."""
        try:
            from transformers import pipeline, AutoTokenizer, AutoModelForSequenceClassification
            import torch

            logger.info(f"Loading FinBERT model ({self.MODEL_NAME})...")
            tokenizer = AutoTokenizer.from_pretrained(self.MODEL_NAME)
            model = AutoModelForSequenceClassification.from_pretrained(self.MODEL_NAME)

            if self.use_fp16 and self.device != "cpu":
                model = model.half()

            self._pipeline = pipeline(
                "text-classification",
                model=model,
                tokenizer=tokenizer,
                device=self.device if self.device != "cpu" else -1,
                top_k=None,      # Return all labels with scores
                truncation=True,
                max_length=512,
            )
            self._finbert_available = True
            logger.info("FinBERT loaded successfully")

        except ImportError:
            logger.warning("transformers/torch not installed — using keyword fallback")
        except Exception as e:
            logger.warning(f"FinBERT load failed ({e}) — using keyword fallback")

    def _finbert_score(self, text: str) -> float:
        """
        Score a single text with FinBERT.
        Returns scalar in [-1, 1]: positive = bullish, negative = bearish.
        """
        try:
            results = self._pipeline(text[:512])  # Truncate to model max
            # results: [[{"label": "positive", "score": 0.9}, ...]]
            if isinstance(results[0], list):
                results = results[0]

            label_scores = {r["label"].lower(): r["score"] for r in results}
            positive = label_scores.get("positive", 0.0)
            negative = label_scores.get("negative", 0.0)
            neutral = label_scores.get("neutral", 0.0)

            # Composite: positive - negative, scaled by confidence
            # Neutral acts as dampener
            score = (positive - negative) * (1 - 0.5 * neutral)
            return float(np.clip(score, -1.0, 1.0))

        except Exception as e:
            logger.warning(f"FinBERT inference failed: {e}")
            return _keyword_score(text)

    def score_headline(self, text: str) -> float:
        """
        Score a single headline. Returns scalar in [-1, 1].
        """
        if self._finbert_available and self._pipeline is not None:
            return self._finbert_score(text)
        return _keyword_score(text)

    def score_batch(self, texts: list[str]) -> list[float]:
        """
        Score multiple headlines efficiently.
        Returns list of scores in [-1, 1].
        """
        if not texts:
            return []

        if self._finbert_available and self._pipeline is not None:
            try:
                truncated = [t[:512] for t in texts]
                results = self._pipeline(truncated)
                scores = []
                for result in results:
                    if isinstance(result, list):
                        label_scores = {r["label"].lower(): r["score"] for r in result}
                    else:
                        label_scores = {result["label"].lower(): result["score"]}

                    positive = label_scores.get("positive", 0.0)
                    negative = label_scores.get("negative", 0.0)
                    neutral = label_scores.get("neutral", 0.0)
                    score = (positive - negative) * (1 - 0.5 * neutral)
                    scores.append(float(np.clip(score, -1.0, 1.0)))
                return scores
            except Exception as e:
                logger.warning(f"Batch FinBERT failed: {e} — falling back to keyword")

        return [_keyword_score(t) for t in texts]

    def update_with_headlines(
        self,
        headlines: list[str],
        timestamp: Optional[float] = None,
        source_weight: float = 1.0,
    ):
        """
        Score a batch of headlines and add to rolling history.

        Args:
            headlines: List of news headline strings
            timestamp: Unix timestamp (defaults to now)
            source_weight: Weight modifier for this source (1.0 = standard)
        """
        if not headlines:
            return

        ts = timestamp or time.time()
        scores = self.score_batch(headlines)

        if scores:
            # Aggregate the batch into a single reading (mean)
            batch_score = float(np.mean(scores))
            self._score_history.append((ts, batch_score, source_weight))
            logger.debug(
                f"Sentiment updated: {len(headlines)} headlines | "
                f"batch_score={batch_score:.3f} | composite={self.composite_score:.3f}"
            )

    @property
    def composite_score(self) -> float:
        """
        Compute rolling weighted composite sentiment score.
        More recent readings have higher weight (exponential decay).
        Returns scalar in [-1, 1].
        """
        if not self._score_history:
            return 0.0

        history = list(self._score_history)
        now = time.time()

        weighted_sum = 0.0
        weight_total = 0.0

        for i, (ts, score, src_weight) in enumerate(history):
            # Recency weight: more recent = higher
            recency_idx = i / max(len(history) - 1, 1)  # [0, 1]
            recency_weight = self.decay_factor ** (1 - recency_idx)  # Flip: newest = decay^0 = 1

            # Time decay (if old data)
            age_hours = (now - ts) / 3600
            time_decay = max(0.1, 1.0 - age_hours / 24)  # Linearly decays over 24h

            w = recency_weight * time_decay * src_weight
            weighted_sum += score * w
            weight_total += w

        if weight_total == 0:
            return 0.0

        return float(np.clip(weighted_sum / weight_total, -1.0, 1.0))

    @property
    def sentiment_label(self) -> str:
        score = self.composite_score
        if score > 0.2:
            return "BULLISH"
        elif score < -0.2:
            return "BEARISH"
        return "NEUTRAL"

    def clear_history(self):
        self._score_history.clear()
