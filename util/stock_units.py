"""Convert share prices to the raw normalized token units used by positions."""

from decimal import Decimal
import math


def _address(value):
    return value.lower() if value.startswith("0x") else value


def token_per_share(pool, asset_id, chain):
    """Use the exact mint's public conversion; never substitute another generation."""
    versions = pool.get("stock_token_versions") or []
    if versions:
        matches = [v for v in versions
                   if v["chain"].lower() == chain.lower()
                   and _address(v["address"]) == _address(asset_id)]
        if len(matches) != 1 or matches[0]["status"] not in ("active", "legacy"):
            raise ValueError("No activated conversion for the selected stock token")
        version = matches[0]
        if version["model"] == "evm_rebasing":
            value = 1  # balanceOf already reports underlying share units.
        elif version["model"] in ("legacy_fixed", "solana_scaled_ui"):
            # /pool/{id} supplies a live conversion for scaled UI tokens.
            value = version["token_per_share"]
        else:
            raise ValueError("Unsupported stock token conversion model")
    else:
        value = pool.get("token_per_share_exact") or pool.get("token_per_share", 1)
    result = Decimal(str(value))
    if (not result.is_finite() or result <= 0
            or not math.isfinite(float(result)) or float(result) <= 0):
        raise ValueError("token_per_share must be finite and positive")
    return result
