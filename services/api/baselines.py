"""
Historical baselines for drift detection in LabLink AI.

Uses Welford's online algorithm for numerically stable incremental
calculation of mean and standard deviation.

Welford's Algorithm:
    For each new value x:
        n += 1
        delta = x - mean
        mean += delta / n
        delta2 = x - mean
        M2 += delta * delta2

    Variance = M2 / (n - 1)  # sample variance
    Std = sqrt(Variance)

This allows us to update statistics incrementally without storing
all historical values, which is essential for long-running production.
"""

import math
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional, Tuple
from sqlalchemy.orm import Session
from sqlalchemy import and_

from database import Baseline, SessionLocal


def get_baselines(
    org_id: str,
    instrument: str,
    db: Optional[Session] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Get current baselines for an org/instrument combination.

    Args:
        org_id: Organization ID
        instrument: Instrument type (e.g., "agilent_chemstation")
        db: Optional database session

    Returns:
        Dict mapping field names to baseline stats:
        {
            "field_name": {
                "mean": float,
                "std": float,
                "n_samples": int,
                "last_updated": datetime
            }
        }
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        baselines = (
            db.query(Baseline)
            .filter(
                Baseline.org_id == org_id,
                Baseline.instrument == instrument,
            )
            .all()
        )

        result = {}
        for b in baselines:
            result[b.field_name] = {
                "mean": b.mean,
                "std": b.std,
                "n_samples": b.n_samples,
                "last_updated": b.last_updated,
            }

        return result

    finally:
        if close_db:
            db.close()


def get_all_baselines(
    org_id: str,
    db: Optional[Session] = None,
) -> Dict[str, Dict[str, Dict[str, Any]]]:
    """
    Get all baselines for an organization, grouped by instrument.

    Args:
        org_id: Organization ID
        db: Optional database session

    Returns:
        Dict mapping instrument -> field_name -> baseline stats
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        baselines = (
            db.query(Baseline)
            .filter(Baseline.org_id == org_id)
            .order_by(Baseline.instrument, Baseline.field_name)
            .all()
        )

        result: Dict[str, Dict[str, Dict[str, Any]]] = {}
        for b in baselines:
            if b.instrument not in result:
                result[b.instrument] = {}

            result[b.instrument][b.field_name] = {
                "mean": b.mean,
                "std": b.std,
                "n_samples": b.n_samples,
                "last_updated": b.last_updated.isoformat() if b.last_updated else None,
                "created_at": b.created_at.isoformat() if b.created_at else None,
            }

        return result

    finally:
        if close_db:
            db.close()


def _welford_update(
    existing_mean: float,
    existing_m2: float,
    existing_n: int,
    new_values: List[float],
) -> Tuple[float, float, float, int]:
    """
    Apply Welford's online algorithm to update running statistics.

    Args:
        existing_mean: Current mean
        existing_m2: Current M2 (sum of squared differences from mean)
        existing_n: Current sample count
        new_values: New values to incorporate

    Returns:
        Tuple of (new_mean, new_m2, new_std, new_n)
    """
    mean = existing_mean
    m2 = existing_m2
    n = existing_n

    for x in new_values:
        if x is None or (isinstance(x, float) and (math.isnan(x) or math.isinf(x))):
            continue

        n += 1
        delta = x - mean
        mean += delta / n
        delta2 = x - mean
        m2 += delta * delta2

    # Calculate sample standard deviation
    if n > 1:
        variance = m2 / (n - 1)
        std = math.sqrt(variance) if variance > 0 else 0.0
    else:
        std = 0.0

    return mean, m2, std, n


def update_baselines(
    org_id: str,
    instrument: str,
    new_stats: Dict[str, Dict[str, Any]],
    db: Optional[Session] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Update baselines with new data using Welford's algorithm.

    This performs incremental updates to mean/std without needing
    to store all historical values.

    Args:
        org_id: Organization ID
        instrument: Instrument type
        new_stats: Dict of field_name -> {"values": [...], "mean": x, "std": y, "n": z}
                   If "values" is provided, uses those for update.
                   Otherwise uses aggregate stats for batch update.
        db: Optional database session

    Returns:
        Dict of updated baselines
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        updated = {}

        for field_name, stats in new_stats.items():
            # Get or create baseline
            baseline = (
                db.query(Baseline)
                .filter(
                    Baseline.org_id == org_id,
                    Baseline.instrument == instrument,
                    Baseline.field_name == field_name,
                )
                .first()
            )

            if baseline is None:
                # Create new baseline
                baseline = Baseline(
                    org_id=org_id,
                    instrument=instrument,
                    field_name=field_name,
                    mean=0.0,
                    std=0.0,
                    m2=0.0,
                    n_samples=0,
                )
                db.add(baseline)

            # Get new values
            values = stats.get("values", [])

            if values:
                # Use individual values for precise update
                new_mean, new_m2, new_std, new_n = _welford_update(
                    existing_mean=baseline.mean,
                    existing_m2=baseline.m2,
                    existing_n=baseline.n_samples,
                    new_values=[v for v in values if v is not None],
                )
            else:
                # Use batch update with aggregate stats
                # This is less precise but works when we only have summary stats
                batch_mean = stats.get("mean")
                batch_std = stats.get("std", 0)
                batch_n = stats.get("n", 0)

                if batch_mean is None or batch_n == 0:
                    continue

                # Combine two sample sets (parallel Welford's)
                new_mean, new_m2, new_std, new_n = _combine_stats(
                    mean1=baseline.mean,
                    m2_1=baseline.m2,
                    n1=baseline.n_samples,
                    mean2=batch_mean,
                    std2=batch_std,
                    n2=batch_n,
                )

            # Update baseline
            baseline.mean = new_mean
            baseline.m2 = new_m2
            baseline.std = new_std
            baseline.n_samples = new_n
            baseline.last_updated = datetime.now(timezone.utc)

            updated[field_name] = {
                "mean": new_mean,
                "std": new_std,
                "n_samples": new_n,
            }

        db.commit()
        return updated

    finally:
        if close_db:
            db.close()


def _combine_stats(
    mean1: float,
    m2_1: float,
    n1: int,
    mean2: float,
    std2: float,
    n2: int,
) -> Tuple[float, float, float, int]:
    """
    Combine two sets of running statistics (parallel Welford's algorithm).

    This allows merging statistics from two independent computations,
    which is useful for batch updates.

    Args:
        mean1, m2_1, n1: First set of statistics
        mean2, std2, n2: Second set (given as mean, std, n)

    Returns:
        Tuple of (combined_mean, combined_m2, combined_std, combined_n)
    """
    if n1 == 0:
        # First set is empty, use second set
        m2_2 = (std2 ** 2) * (n2 - 1) if n2 > 1 else 0.0
        return mean2, m2_2, std2, n2

    if n2 == 0:
        # Second set is empty, return first
        std1 = math.sqrt(m2_1 / (n1 - 1)) if n1 > 1 else 0.0
        return mean1, m2_1, std1, n1

    # Convert std2 to m2_2
    m2_2 = (std2 ** 2) * (n2 - 1) if n2 > 1 else 0.0

    # Combined count
    n = n1 + n2

    # Combined mean (weighted average)
    delta = mean2 - mean1
    combined_mean = mean1 + delta * n2 / n

    # Combined M2 (parallel algorithm)
    # M2_combined = M2_1 + M2_2 + delta^2 * n1 * n2 / n
    combined_m2 = m2_1 + m2_2 + (delta ** 2) * n1 * n2 / n

    # Combined std
    if n > 1:
        variance = combined_m2 / (n - 1)
        combined_std = math.sqrt(variance) if variance > 0 else 0.0
    else:
        combined_std = 0.0

    return combined_mean, combined_m2, combined_std, n


def reset_baselines(
    org_id: str,
    instrument: str,
    field_names: Optional[List[str]] = None,
    db: Optional[Session] = None,
) -> int:
    """
    Reset baselines for an instrument (e.g., after recalibration).

    Args:
        org_id: Organization ID
        instrument: Instrument type
        field_names: Optional list of specific fields to reset.
                     If None, resets all fields for the instrument.
        db: Optional database session

    Returns:
        Number of baselines deleted
    """
    close_db = False
    if db is None:
        db = SessionLocal()
        close_db = True

    try:
        query = db.query(Baseline).filter(
            Baseline.org_id == org_id,
            Baseline.instrument == instrument,
        )

        if field_names:
            query = query.filter(Baseline.field_name.in_(field_names))

        count = query.delete(synchronize_session=False)
        db.commit()

        return count

    finally:
        if close_db:
            db.close()


def get_baselines_for_qc(
    org_id: str,
    instrument: str,
    db: Optional[Session] = None,
) -> Dict[str, Dict[str, Any]]:
    """
    Get baselines in the format expected by qc_summary().

    Args:
        org_id: Organization ID
        instrument: Instrument type
        db: Optional database session

    Returns:
        Dict in qc_summary's historical_baselines format:
        {
            "field_name": {
                "mean": float,
                "std": float,
                "n_samples": int
            }
        }
    """
    baselines = get_baselines(org_id, instrument, db)

    # Format for QC module
    return {
        field: {
            "mean": data["mean"],
            "std": data["std"],
            "n_samples": data["n_samples"],
        }
        for field, data in baselines.items()
        if data["n_samples"] > 0  # Only include baselines with data
    }
