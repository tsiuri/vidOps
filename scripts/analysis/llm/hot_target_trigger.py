"""
Hot target trigger evaluation system.

Supports configurable trigger rules per target:
- Min occurrence thresholds
- Keyword matching with thresholds
- OR/AND logic for multiple conditions
- "always" runs
- Cooldown tracking
"""

from __future__ import annotations

import time
from typing import Any, Dict, List, Set, Optional
from dataclasses import dataclass, field


@dataclass
class TriggerResult:
    """Result of evaluating a trigger."""
    triggered: bool
    reason: str = ""
    match_count: int = 0
    matched_tokens: List[str] = field(default_factory=list)
    matched_keywords: List[str] = field(default_factory=list)


class HotTargetTriggerEvaluator:
    """Evaluate hot target trigger rules against chunk analyses."""

    def __init__(self):
        # Track last trigger time per target for cooldown
        self._last_triggered: Dict[str, float] = {}

    def evaluate_target(
        self,
        target: Dict[str, Any],
        chunk_analyses: List[Dict[str, Any]],
        chunks: List[Dict[str, Any]],
    ) -> TriggerResult:
        """
        Evaluate whether a target should be triggered.

        Target trigger rule configuration:
        {
            "name": "conflict_detection",
            "category": "conflict",

            # Trigger conditions (evaluated with OR or AND logic)
            "trigger": {
                "logic": "OR",  // "OR" or "AND" (default: "OR")

                # Category/topic matching
                "match": ["conflict", "drama"],  // tokens to match in categories/topics
                "min_occurrences": 2,  // minimum matches needed (default: 1)

                # Keyword matching
                "keywords": ["disagreement", "fight", "argument"],
                "keyword_threshold": 1,  // min keywords that must appear (default: 1)

                # Always run flag
                "always": false,  // if true, always trigger (default: false)

                # Cooldown (don't re-trigger too frequently)
                "cooldown_seconds": 300,  // min seconds between triggers (default: 0)
            }
        }

        Args:
            target: Target configuration dict
            chunk_analyses: List of chunk analysis results
            chunks: List of chunk dicts with text

        Returns:
            TriggerResult with trigger decision and match details
        """
        name = target.get("name") or target.get("category") or "unknown"

        # Legacy format support: rules can be at top level or in "trigger" dict
        trigger_config = target.get("trigger", target)

        # Check cooldown first
        cooldown_sec = trigger_config.get("cooldown_seconds", 0)
        if cooldown_sec > 0:
            last_time = self._last_triggered.get(name, 0)
            elapsed = time.time() - last_time
            if elapsed < cooldown_sec:
                return TriggerResult(
                    triggered=False,
                    reason=f"cooldown ({elapsed:.0f}s < {cooldown_sec}s)"
                )

        # Always run?
        if trigger_config.get("always", False):
            self._mark_triggered(name)
            return TriggerResult(triggered=True, reason="always")

        # Determine logic mode
        logic = str(trigger_config.get("logic", "OR")).upper()
        if logic not in ("OR", "AND"):
            logic = "OR"

        # Evaluate individual conditions
        conditions_met = []

        # Condition 1: Category/topic matching
        token_result = self._evaluate_token_matching(
            target, chunk_analyses, trigger_config
        )
        if token_result.triggered:
            conditions_met.append("category/topic")

        # Condition 2: Keyword matching
        keyword_result = self._evaluate_keyword_matching(
            trigger_config, chunks
        )
        if keyword_result.triggered:
            conditions_met.append("keyword")

        # Apply logic
        if logic == "OR":
            triggered = len(conditions_met) > 0
        else:  # AND
            # For AND logic, all specified conditions must be met
            required_conditions = []
            if trigger_config.get("match") or trigger_config.get("category") or trigger_config.get("name"):
                required_conditions.append("category/topic")
            if trigger_config.get("keywords"):
                required_conditions.append("keyword")

            triggered = all(cond in conditions_met for cond in required_conditions) if required_conditions else False

        if triggered:
            self._mark_triggered(name)

        # Combine results
        return TriggerResult(
            triggered=triggered,
            reason=" AND ".join(conditions_met) if logic == "AND" else " OR ".join(conditions_met),
            match_count=token_result.match_count + keyword_result.match_count,
            matched_tokens=token_result.matched_tokens,
            matched_keywords=keyword_result.matched_keywords
        )

    def _evaluate_token_matching(
        self,
        target: Dict[str, Any],
        chunk_analyses: List[Dict[str, Any]],
        trigger_config: Dict[str, Any]
    ) -> TriggerResult:
        """Evaluate category/topic token matching."""
        # Build list of tokens to match
        tokens = []

        # Add category/name from target
        if trigger_config.get("category"):
            tokens.append(str(trigger_config["category"]).strip().lower())
        if trigger_config.get("name"):
            tokens.append(str(trigger_config["name"]).strip().lower())

        # Add explicit match list
        match_list = trigger_config.get("match", [])
        if isinstance(match_list, list):
            tokens.extend([str(t).strip().lower() for t in match_list if t])

        # Remove duplicates
        tokens = list(set(tok for tok in tokens if tok))

        if not tokens:
            return TriggerResult(triggered=False)

        # Count matches across all chunks
        matched_tokens = set()
        match_count = 0

        for analysis in chunk_analyses:
            cats = [str(c).strip().lower() for c in (analysis.get("categories") or []) if c]
            topics = [str(t).strip().lower() for t in (analysis.get("topics") or []) if t]

            for tok in tokens:
                if tok in cats or tok in topics:
                    matched_tokens.add(tok)
                    match_count += 1

        # Check min occurrences threshold
        min_occ = trigger_config.get("min_occurrences", 1)
        triggered = match_count >= min_occ

        return TriggerResult(
            triggered=triggered,
            match_count=match_count,
            matched_tokens=list(matched_tokens)
        )

    def _evaluate_keyword_matching(
        self,
        trigger_config: Dict[str, Any],
        chunks: List[Dict[str, Any]]
    ) -> TriggerResult:
        """Evaluate keyword matching in chunk text."""
        keywords = trigger_config.get("keywords", [])
        if not keywords or not isinstance(keywords, list):
            return TriggerResult(triggered=False)

        keywords = [str(k).strip().lower() for k in keywords if k]
        if not keywords:
            return TriggerResult(triggered=False)

        # Build chunk text lookup
        chunk_text = {
            c.get("chunk_id"): (c.get("text") or "").lower()
            for c in chunks
        }

        # Count keyword matches
        matched_keywords = set()
        match_count = 0

        for text in chunk_text.values():
            for keyword in keywords:
                if keyword in text:
                    matched_keywords.add(keyword)
                    match_count += 1

        # Check keyword threshold
        threshold = trigger_config.get("keyword_threshold", 1)
        triggered = len(matched_keywords) >= threshold

        return TriggerResult(
            triggered=triggered,
            match_count=match_count,
            matched_keywords=list(matched_keywords)
        )

    def _mark_triggered(self, target_name: str):
        """Record that a target was triggered (for cooldown tracking)."""
        self._last_triggered[target_name] = time.time()

    def evaluate_all_targets(
        self,
        targets: List[Dict[str, Any]],
        chunk_analyses: List[Dict[str, Any]],
        chunks: List[Dict[str, Any]]
    ) -> List[Dict[str, Any]]:
        """
        Evaluate all targets and return list of triggered targets.

        Returns:
            List of target dicts (enriched with trigger metadata) that were triggered
        """
        triggered_targets = []

        for target in targets:
            result = self.evaluate_target(target, chunk_analyses, chunks)

            if result.triggered:
                # Enrich target with trigger metadata
                target_copy = dict(target)
                target_copy["_trigger_result"] = {
                    "reason": result.reason,
                    "match_count": result.match_count,
                    "matched_tokens": result.matched_tokens,
                    "matched_keywords": result.matched_keywords
                }
                triggered_targets.append(target_copy)

        return triggered_targets

    def reset_cooldowns(self):
        """Reset all cooldown timers (useful for testing)."""
        self._last_triggered.clear()
