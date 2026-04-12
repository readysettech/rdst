"""Static RDS pricing data for common instance classes."""

from typing import Any, Dict, Optional

# On-demand pricing (us-east-1, single-AZ, PostgreSQL/MySQL).
# Source: AWS pricing page. Updated periodically.
RDS_PRICING: Dict[str, Dict[str, Any]] = {
    # T3 (burstable)
    "db.t3.micro": {"vcpu": 2, "memory_gb": 1, "price_usd_hr": 0.017},
    "db.t3.small": {"vcpu": 2, "memory_gb": 2, "price_usd_hr": 0.034},
    "db.t3.medium": {"vcpu": 2, "memory_gb": 4, "price_usd_hr": 0.068},
    "db.t3.large": {"vcpu": 2, "memory_gb": 8, "price_usd_hr": 0.136},
    "db.t3.xlarge": {"vcpu": 4, "memory_gb": 16, "price_usd_hr": 0.272},
    "db.t3.2xlarge": {"vcpu": 8, "memory_gb": 32, "price_usd_hr": 0.544},
    # T4g (burstable, Graviton)
    "db.t4g.micro": {"vcpu": 2, "memory_gb": 1, "price_usd_hr": 0.016},
    "db.t4g.small": {"vcpu": 2, "memory_gb": 2, "price_usd_hr": 0.032},
    "db.t4g.medium": {"vcpu": 2, "memory_gb": 4, "price_usd_hr": 0.065},
    "db.t4g.large": {"vcpu": 2, "memory_gb": 8, "price_usd_hr": 0.129},
    "db.t4g.xlarge": {"vcpu": 4, "memory_gb": 16, "price_usd_hr": 0.258},
    "db.t4g.2xlarge": {"vcpu": 8, "memory_gb": 32, "price_usd_hr": 0.516},
    # M5 (general purpose)
    "db.m5.large": {"vcpu": 2, "memory_gb": 8, "price_usd_hr": 0.171},
    "db.m5.xlarge": {"vcpu": 4, "memory_gb": 16, "price_usd_hr": 0.342},
    "db.m5.2xlarge": {"vcpu": 8, "memory_gb": 32, "price_usd_hr": 0.684},
    "db.m5.4xlarge": {"vcpu": 16, "memory_gb": 64, "price_usd_hr": 1.368},
    # M6g (general purpose, Graviton)
    "db.m6g.large": {"vcpu": 2, "memory_gb": 8, "price_usd_hr": 0.155},
    "db.m6g.xlarge": {"vcpu": 4, "memory_gb": 16, "price_usd_hr": 0.311},
    "db.m6g.2xlarge": {"vcpu": 8, "memory_gb": 32, "price_usd_hr": 0.621},
    "db.m6g.4xlarge": {"vcpu": 16, "memory_gb": 64, "price_usd_hr": 1.242},
    # M7g (general purpose, Graviton3)
    "db.m7g.large": {"vcpu": 2, "memory_gb": 8, "price_usd_hr": 0.163},
    "db.m7g.xlarge": {"vcpu": 4, "memory_gb": 16, "price_usd_hr": 0.326},
    "db.m7g.2xlarge": {"vcpu": 8, "memory_gb": 32, "price_usd_hr": 0.653},
    # R5 (memory optimized)
    "db.r5.large": {"vcpu": 2, "memory_gb": 16, "price_usd_hr": 0.240},
    "db.r5.xlarge": {"vcpu": 4, "memory_gb": 32, "price_usd_hr": 0.480},
    "db.r5.2xlarge": {"vcpu": 8, "memory_gb": 64, "price_usd_hr": 0.960},
    "db.r5.4xlarge": {"vcpu": 16, "memory_gb": 128, "price_usd_hr": 1.920},
    # R6g (memory optimized, Graviton)
    "db.r6g.large": {"vcpu": 2, "memory_gb": 16, "price_usd_hr": 0.218},
    "db.r6g.xlarge": {"vcpu": 4, "memory_gb": 32, "price_usd_hr": 0.435},
    "db.r6g.2xlarge": {"vcpu": 8, "memory_gb": 64, "price_usd_hr": 0.870},
    "db.r6g.4xlarge": {"vcpu": 16, "memory_gb": 128, "price_usd_hr": 1.740},
    # R6i (memory optimized, Intel)
    "db.r6i.large": {"vcpu": 2, "memory_gb": 16, "price_usd_hr": 0.240},
    "db.r6i.xlarge": {"vcpu": 4, "memory_gb": 32, "price_usd_hr": 0.480},
    "db.r6i.2xlarge": {"vcpu": 8, "memory_gb": 64, "price_usd_hr": 0.960},
    # R7g (memory optimized, Graviton3)
    "db.r7g.large": {"vcpu": 2, "memory_gb": 16, "price_usd_hr": 0.229},
    "db.r7g.xlarge": {"vcpu": 4, "memory_gb": 32, "price_usd_hr": 0.457},
    "db.r7g.2xlarge": {"vcpu": 8, "memory_gb": 64, "price_usd_hr": 0.914},
}

# Average hours per month
_HOURS_PER_MONTH = 730


def monthly_cost(instance_class: str) -> Optional[float]:
    """Get estimated monthly cost for an instance class."""
    pricing = RDS_PRICING.get(instance_class)
    if pricing:
        return round(pricing["price_usd_hr"] * _HOURS_PER_MONTH, 2)
    return None


def get_instance_info(instance_class: str) -> Optional[Dict[str, Any]]:
    """Get vCPU, memory, and pricing info for an instance class."""
    return RDS_PRICING.get(instance_class)


def estimate_class_from_shared_buffers(shared_buffers_mb: float) -> Optional[str]:
    """Estimate instance class from shared_buffers size.

    Heuristic: shared_buffers is typically 25% of instance memory,
    so estimated_memory = shared_buffers * 4.
    """
    if shared_buffers_mb <= 0:
        return None

    estimated_memory_gb = (shared_buffers_mb * 4) / 1024

    # Find closest instance class by memory
    best_match = None
    best_diff = float("inf")

    for cls, info in RDS_PRICING.items():
        diff = abs(info["memory_gb"] - estimated_memory_gb)
        if diff < best_diff:
            best_diff = diff
            best_match = cls

    return best_match


def suggest_downsize(instance_class: str) -> Optional[Dict[str, Any]]:
    """Suggest a smaller instance class in the same family.

    Returns dict with instance_class and monthly_cost, or None.
    """
    current = RDS_PRICING.get(instance_class)
    if not current:
        return None

    # Parse family and size: db.r6g.xlarge -> (db.r6g, xlarge)
    parts = instance_class.rsplit(".", 1)
    if len(parts) != 2:
        return None
    family = parts[0]

    # Size ordering (smallest to largest)
    size_order = ["micro", "small", "medium", "large", "xlarge", "2xlarge", "4xlarge", "8xlarge", "12xlarge", "16xlarge"]

    current_size = parts[1]
    if current_size not in size_order:
        return None

    current_idx = size_order.index(current_size)
    if current_idx == 0:
        return None  # Already smallest

    # Try one size down
    smaller_size = size_order[current_idx - 1]
    smaller_class = f"{family}.{smaller_size}"
    smaller_info = RDS_PRICING.get(smaller_class)
    if smaller_info:
        return {
            "instance_class": smaller_class,
            "monthly_cost": round(smaller_info["price_usd_hr"] * _HOURS_PER_MONTH, 2),
        }

    return None
