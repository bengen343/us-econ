"""Turn a TimesFM point forecast + prediction interval into Kalshi-shaped
probabilities.

Two outputs per 5k strike K on the grid:

  p_at_least  P(published >= K)        -- one Kalshi KXJOBLESSCLAIMS contract
  p_bucket    P(K <= published < K+5k) -- the "lands in the 195-200k bucket"
                                          question; equals p_at_least(K)
                                          minus p_at_least(K+5k)

Two details that matter and are easy to get wrong:

1. **The interval is asymmetric.** AI.FORECAST's TimesFM bounds sit further
   above the point forecast than below it (mean sigma_hi ~10.2k vs sigma_lo
   ~7.9k on our history). Fitting one symmetric normal throws that away, so we
   fit a two-piece normal by **quantile matching**: treat the point forecast as
   the median and each bound as its stated quantile, giving a different sigma
   on each side.

   Treating the point forecast as the *mode* instead (rescaling the two halves
   into a proper density) is the other obvious choice and it is measurably
   wrong here: it puts only ~46% of the mass below the point forecast, which
   tilts every probability upward. Over 119 origins its PIT fails a uniformity
   test (KS p=0.004, mean PIT 0.419) while quantile matching passes
   (KS p=0.215). Walk-forward bias and sigma-scale corrections on top were both
   tested and made calibration worse, so this stays parameter-free.

2. **The headline is rounded to the nearest 1,000.** DOL publishes 194,000,
   never 194,237, and Kalshi settles on that published figure. So
   P(published >= 195,000) is P(underlying >= 194,500), not P(>= 195,000).
   Skipping this continuity correction biases every strike by half a rounding
   unit -- 10% of a bucket.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.stats import norm


@dataclass(frozen=True)
class SplitNormal:
    """Two-piece normal: median at `mu`, sd `sigma_lo` below, `sigma_hi` above.

    The CDF is continuous (both branches give 0.5 at `mu`); only the density
    steps there, which is harmless for the bucket integrals we take.
    """

    mu: float
    sigma_lo: float
    sigma_hi: float

    @classmethod
    def from_interval(
        cls, point: float, lower: float, upper: float, confidence_level: float
    ) -> SplitNormal:
        """Fit from AI.FORECAST's (forecast_value, lower_bound, upper_bound)."""
        if not lower < point < upper:
            raise ValueError(
                f"interval must bracket the point forecast; got "
                f"lower={lower}, point={point}, upper={upper}"
            )
        if not 0.0 < confidence_level < 1.0:
            raise ValueError(f"confidence_level must be in (0,1); got {confidence_level}")
        z = norm.ppf(0.5 + confidence_level / 2.0)
        return cls(mu=float(point),
                   sigma_lo=float((point - lower) / z),
                   sigma_hi=float((upper - point) / z))

    def cdf(self, x: np.ndarray | float) -> np.ndarray:
        """P(X <= x) for the continuous underlying value."""
        x = np.asarray(x, dtype=float)
        sigma = np.where(x < self.mu, self.sigma_lo, self.sigma_hi)
        return np.clip(norm.cdf((x - self.mu) / sigma), 0.0, 1.0)

    def p_at_least(self, strike: np.ndarray | float, rounding_unit: float) -> np.ndarray:
        """P(published figure >= strike), with the rounding continuity correction."""
        return 1.0 - self.cdf(np.asarray(strike, dtype=float) - rounding_unit / 2.0)


def strike_grid(point: float, bucket_width: float, n_each_side: int) -> np.ndarray:
    """5k strikes bracketing the point forecast, aligned to the bucket grid.

    Aligning to multiples of `bucket_width` (rather than centring on the point
    forecast) is what makes these comparable to Kalshi's listed strikes, which
    are always round multiples of 5,000.
    """
    centre = np.floor(point / bucket_width) * bucket_width
    offsets = np.arange(-n_each_side, n_each_side + 1) * bucket_width
    return centre + offsets


def bucket_table(
    dist: SplitNormal,
    *,
    bucket_width: float,
    rounding_unit: float,
    n_each_side: int,
) -> list[dict]:
    """One row per strike: the exceedance probability and the bucket probability.

    `p_bucket` is the differenced exceedance curve, so the buckets over the full
    real line sum to 1 by construction; across this finite grid they sum to
    slightly less, and the shortfall is the tail mass outside it.
    """
    strikes = strike_grid(dist.mu, bucket_width, n_each_side)
    at_least = dist.p_at_least(strikes, rounding_unit)
    # P(next strike up) for the top row comes from one extra strike beyond the grid.
    at_least_next = dist.p_at_least(strikes + bucket_width, rounding_unit)

    rows = []
    for k, pa, pan in zip(strikes, at_least, at_least_next, strict=True):
        rows.append({
            "bucket_low": float(k),
            "bucket_high": float(k + bucket_width),
            "p_at_least": float(pa),
            "p_bucket": float(pa - pan),
        })
    return rows
