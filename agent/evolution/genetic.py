"""
AEGIS — Genetic Strategy Evolver
Evolves strategy parameters every 24h using a simple genetic algorithm.

Genes: strategy parameters (MA periods, RSI thresholds, stop multipliers, etc.)
Fitness: Sharpe ratio × win_rate × (1 - max_drawdown)
Selection: Top 50% survive
Crossover: Blend parents' genes
Mutation: 5-10% random perturbation
"""

import copy
import logging
import random
import time
from dataclasses import dataclass
from typing import Any, Optional

import numpy as np

from strategies.base_strategy import BaseStrategy

logger = logging.getLogger(__name__)


@dataclass
class Individual:
    """One strategy variant with its fitness score."""
    strategy_name: str
    genes: dict
    fitness: float = 0.0
    win_rate: float = 0.0
    sharpe: float = 0.0
    max_drawdown: float = 0.0
    trade_count: int = 0
    generation: int = 0

    def clone(self) -> "Individual":
        return Individual(
            strategy_name=self.strategy_name,
            genes=copy.deepcopy(self.genes),
            fitness=self.fitness,
            win_rate=self.win_rate,
            sharpe=self.sharpe,
            max_drawdown=self.max_drawdown,
            trade_count=self.trade_count,
            generation=self.generation,
        )


class GeneticEvolver:
    """
    Genetic algorithm for autonomous strategy parameter evolution.

    Population: Multiple gene sets per strategy
    Lifecycle:
      1. Evaluate fitness of current population
      2. Select survivors (top 50%)
      3. Crossover between pairs of survivors
      4. Mutate children
      5. Replace bottom performers with evolved children
    """

    POPULATION_SIZE = 12     # Strategy variants per population
    SURVIVAL_RATE = 0.5      # Top 50% survive
    MUTATION_RATE = 0.15     # 15% chance to mutate each gene
    MUTATION_SCALE = 0.10    # Mutate by ±10% of gene value
    MIN_TRADES_FOR_EVAL = 3  # Minimum trades before evaluating fitness
    ELITE_COUNT = 2          # Best N always survive unchanged
    TOURNAMENT_SIZE = 3      # Tournament selection pool size
    RECENT_TRADE_WINDOW = 20 # Evaluate fitness on last N trades, not all-time
    LOSER_MUTATION_BOOST = 2.0  # Extra mutation for losing strategies

    def __init__(self, strategies: list[BaseStrategy], rng_seed: int = 42):
        self.strategies = {s.name: s for s in strategies}
        self.rng = random.Random(rng_seed)
        np.random.seed(rng_seed)

        # Population: strategy_name → list of Individuals
        self._populations: dict[str, list[Individual]] = {}
        self._generation: int = 0
        self._last_evolution: float = 0.0
        self._trade_history: dict[str, list[dict]] = {}   # strategy_name → trades

        self._initialize_populations()

    def _initialize_populations(self):
        """Create initial population from each strategy's default genes."""
        for name, strategy in self.strategies.items():
            if not hasattr(strategy, "genes"):
                continue
            base_genes = strategy.genes
            population = []
            for i in range(self.POPULATION_SIZE):
                if i == 0:
                    # First individual: exact default genes
                    genes = copy.deepcopy(base_genes)
                else:
                    # Mutate defaults to create variety
                    genes = self._mutate_genes(copy.deepcopy(base_genes), force=True)
                population.append(Individual(
                    strategy_name=name,
                    genes=genes,
                    generation=0,
                ))
            self._populations[name] = population
            logger.info(f"Initialized population of {len(population)} for {name}")

    # ─── Fitness Evaluation ───────────────────────────────────────────────────

    def record_trade(
        self,
        strategy_name: str,
        gene_fingerprint: str,   # hash of genes used for this trade
        pnl_pct: float,
        won: bool,
    ):
        """
        Record a trade outcome for fitness tracking.
        """
        if strategy_name not in self._trade_history:
            self._trade_history[strategy_name] = []
        self._trade_history[strategy_name].append({
            "gene_fp": gene_fingerprint,
            "pnl_pct": pnl_pct,
            "won": won,
            "timestamp": time.time(),
        })

    def evaluate_fitness(
        self,
        sharpe: float,
        win_rate: float,
        max_drawdown: float,
    ) -> float:
        """
        Compute fitness score.
        F = Sharpe × win_rate × (1 - max_drawdown)
        """
        # Clamp inputs
        sharpe = max(sharpe, -2.0)
        win_rate = float(np.clip(win_rate, 0.0, 1.0))
        max_drawdown = float(np.clip(max_drawdown, 0.0, 1.0))

        # Sharpe can be negative — shift to allow positive fitness for decent Sharpes
        sharpe_normalized = (sharpe + 2.0) / 4.0  # Maps [-2, 2] → [0, 1]
        fitness = sharpe_normalized * win_rate * (1 - max_drawdown)
        return float(fitness)

    def update_individual_fitness(
        self,
        strategy_name: str,
        individual_idx: int,
        sharpe: float,
        win_rate: float,
        max_drawdown: float,
        trade_count: int,
    ):
        """Update fitness for a specific individual."""
        if strategy_name not in self._populations:
            return
        pop = self._populations[strategy_name]
        if individual_idx >= len(pop):
            return
        ind = pop[individual_idx]
        ind.sharpe = sharpe
        ind.win_rate = win_rate
        ind.max_drawdown = max_drawdown
        ind.trade_count = trade_count
        ind.fitness = self.evaluate_fitness(sharpe, win_rate, max_drawdown)

    # ─── Selection ────────────────────────────────────────────────────────────

    def _select_survivors(self, population: list[Individual]) -> list[Individual]:
        """
        Tournament + elitist selection.
        Top ELITE_COUNT always survive; rest via tournament selection.
        """
        evaluated = [i for i in population if i.trade_count >= self.MIN_TRADES_FOR_EVAL]
        unevaluated = [i for i in population if i.trade_count < self.MIN_TRADES_FOR_EVAL]

        # Sort by fitness — elites always survive
        evaluated.sort(key=lambda x: x.fitness, reverse=True)

        n_survivors = max(self.ELITE_COUNT, int(len(population) * self.SURVIVAL_RATE))
        survivors = evaluated[:self.ELITE_COUNT]  # Elites guaranteed

        # Tournament selection for remaining slots
        remaining_slots = n_survivors - len(survivors)
        candidates = evaluated[self.ELITE_COUNT:]
        for _ in range(remaining_slots):
            if len(candidates) >= self.TOURNAMENT_SIZE:
                tournament = self.rng.sample(candidates, self.TOURNAMENT_SIZE)
                winner = max(tournament, key=lambda x: x.fitness)
                survivors.append(winner)
            elif candidates:
                survivors.append(candidates.pop(0))

        # Always include some unevaluated (new blood)
        n_new = min(len(unevaluated), max(1, int(n_survivors * 0.2)))
        survivors += unevaluated[:n_new]

        return survivors

    # ─── Crossover ────────────────────────────────────────────────────────────

    def _crossover(self, parent_a: Individual, parent_b: Individual) -> Individual:
        """
        Blend crossover: child gene = α × a_gene + (1-α) × b_gene
        α is random per gene.
        """
        child_genes = {}
        for key in parent_a.genes:
            if key not in parent_b.genes:
                child_genes[key] = parent_a.genes[key]
                continue
            a_val = parent_a.genes[key]
            b_val = parent_b.genes[key]
            if isinstance(a_val, (int, float)):
                alpha = self.rng.random()
                blended = alpha * a_val + (1 - alpha) * b_val
                if isinstance(a_val, int):
                    blended = max(1, int(round(blended)))
                child_genes[key] = blended
            else:
                # Non-numeric: inherit from random parent
                child_genes[key] = a_val if self.rng.random() > 0.5 else b_val

        generation = max(parent_a.generation, parent_b.generation) + 1
        return Individual(
            strategy_name=parent_a.strategy_name,
            genes=child_genes,
            generation=generation,
        )

    # ─── Mutation ─────────────────────────────────────────────────────────────

    def _mutate_genes(self, genes: dict, force: bool = False, scale_boost: float = 1.0) -> dict:
        """
        Mutate genes by ±MUTATION_SCALE × gene_value for each gene
        with probability MUTATION_RATE.

        scale_boost: multiplier for mutation scale (>1 = more aggressive)
        """
        mutated = copy.deepcopy(genes)
        effective_scale = self.MUTATION_SCALE * scale_boost
        for key, val in mutated.items():
            if not isinstance(val, (int, float)):
                continue
            if force or self.rng.random() < self.MUTATION_RATE:
                noise = self.rng.gauss(0, effective_scale)
                new_val = val * (1 + noise)
                if isinstance(val, int):
                    new_val = max(1, int(round(new_val)))
                mutated[key] = new_val
        return mutated

    # ─── Evolution Cycle ──────────────────────────────────────────────────────

    def evolve(self) -> dict[str, Individual]:
        """
        Run one full evolution cycle across all strategies.
        Returns the current best individual per strategy.

        Should be called every 24h.
        """
        self._generation += 1
        self._last_evolution = time.time()
        best_per_strategy: dict[str, Individual] = {}

        for strategy_name, population in self._populations.items():
            logger.info(
                f"Evolving {strategy_name} | gen={self._generation} | "
                f"pop_size={len(population)}"
            )

            survivors = self._select_survivors(population)
            if not survivors:
                logger.warning(f"No survivors for {strategy_name} — reinitializing")
                self._initialize_populations()
                continue

            # Log best individual
            evaluated = [i for i in survivors if i.trade_count >= self.MIN_TRADES_FOR_EVAL]
            if evaluated:
                best = max(evaluated, key=lambda x: x.fitness)
                best_per_strategy[strategy_name] = best
                logger.info(
                    f"  Best: fitness={best.fitness:.4f} | "
                    f"sharpe={best.sharpe:.3f} | "
                    f"win_rate={best.win_rate:.2%} | "
                    f"gen={best.generation}"
                )
            else:
                best_per_strategy[strategy_name] = survivors[0]

            # Fill population back to POPULATION_SIZE with children + mutations
            new_population = survivors[: self.ELITE_COUNT]  # Elites unchanged

            # Identify losers for more aggressive mutation
            median_fitness = 0.0
            if evaluated:
                fitnesses = sorted([i.fitness for i in evaluated])
                median_fitness = fitnesses[len(fitnesses) // 2]

            while len(new_population) < self.POPULATION_SIZE:
                if len(survivors) >= 2:
                    p_a, p_b = self.rng.sample(survivors, 2)
                    child = self._crossover(p_a, p_b)
                else:
                    child = survivors[0].clone()

                # More aggressive mutation for children of losing parents
                is_from_loser = (p_a.fitness < median_fitness if len(survivors) >= 2 else False)
                if is_from_loser:
                    # Double mutation: mutate twice with boosted scale
                    child.genes = self._mutate_genes(child.genes, scale_boost=self.LOSER_MUTATION_BOOST)
                    child.genes = self._mutate_genes(child.genes)
                else:
                    child.genes = self._mutate_genes(child.genes)
                new_population.append(child)

            self._populations[strategy_name] = new_population
            logger.info(
                f"  New population: {len(new_population)} individuals | "
                f"{len(survivors)} survivors + {len(new_population) - self.ELITE_COUNT} children"
            )

        # Apply best genes to live strategies
        self._apply_best_genes(best_per_strategy)

        return best_per_strategy

    def _apply_best_genes(self, best: dict[str, Individual]):
        """Apply the best evolved genes to the live strategy instances."""
        for strategy_name, individual in best.items():
            if strategy_name not in self.strategies:
                continue
            strategy = self.strategies[strategy_name]
            if hasattr(strategy, "genes"):
                try:
                    strategy.genes = individual.genes
                    logger.info(f"Applied evolved genes to {strategy_name}")
                except Exception as e:
                    logger.warning(f"Could not apply genes to {strategy_name}: {e}")

    def get_best_individual(self, strategy_name: str) -> Optional[Individual]:
        """Return the current best individual for a strategy."""
        pop = self._populations.get(strategy_name, [])
        evaluated = [i for i in pop if i.trade_count >= self.MIN_TRADES_FOR_EVAL]
        if not evaluated:
            return pop[0] if pop else None
        return max(evaluated, key=lambda x: x.fitness)

    def should_evolve(self, interval_hours: float = 24.0) -> bool:
        """Return True if evolution is due."""
        if self._last_evolution == 0:
            return True
        elapsed_hours = (time.time() - self._last_evolution) / 3600
        return elapsed_hours >= interval_hours

    def evolution_summary(self) -> dict:
        """Return a summary of current population fitness."""
        summary = {}
        for name, pop in self._populations.items():
            evaluated = [i for i in pop if i.trade_count >= self.MIN_TRADES_FOR_EVAL]
            if evaluated:
                best = max(evaluated, key=lambda x: x.fitness)
                summary[name] = {
                    "generation": self._generation,
                    "population_size": len(pop),
                    "evaluated": len(evaluated),
                    "best_fitness": best.fitness,
                    "best_sharpe": best.sharpe,
                    "best_win_rate": best.win_rate,
                }
            else:
                summary[name] = {
                    "generation": self._generation,
                    "population_size": len(pop),
                    "evaluated": 0,
                    "best_fitness": 0.0,
                }
        return summary
