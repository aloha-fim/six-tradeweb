"""Yield-curve fitting (Svensson) and curve-based bond pricing -- pure Python.

The Svensson form is linear in its four betas once the two taus are fixed, so we
solve the betas by ordinary least squares (normal equations + Gaussian
elimination) and grid-search the two taus. No numpy/scipy dependency -- the app
stays pure-Python.

    y(t) = b0 + b1*e^(-t/t1) + b2*(t/t1)*e^(-t/t1) + b3*(t/t2)*e^(-t/t2)
"""
from __future__ import annotations

import math

_TAU1_GRID = (0.5, 1.0, 2.0, 3.0, 5.0, 7.0)
_TAU2_GRID = (5.0, 8.0, 10.0, 15.0, 20.0, 30.0)


def _basis(t: float, t1: float, t2: float) -> list[float]:
    e1 = math.exp(-t / t1)
    e2 = math.exp(-t / t2)
    return [1.0, e1, (t / t1) * e1, (t / t2) * e2]


def _solve(A: list[list[float]], b: list[float]) -> list[float]:
    """Solve the n x n system A x = b by Gaussian elimination with partial pivot."""
    n = len(b)
    M = [A[i][:] + [b[i]] for i in range(n)]
    for c in range(n):
        p = max(range(c, n), key=lambda r: abs(M[r][c]))
        M[c], M[p] = M[p], M[c]
        if abs(M[c][c]) < 1e-12:
            M[c][c] = 1e-12
        for r in range(n):
            if r != c:
                f = M[r][c] / M[c][c]
                for k in range(c, n + 1):
                    M[r][k] -= f * M[c][k]
    return [M[i][n] / M[i][i] for i in range(n)]


def _fit_betas(mats: list[float], ys: list[float], t1: float, t2: float) -> list[float]:
    XtX = [[0.0] * 4 for _ in range(4)]
    Xty = [0.0] * 4
    for t, y in zip(mats, ys):
        x = _basis(t, t1, t2)
        for i in range(4):
            Xty[i] += x[i] * y
            for j in range(4):
                XtX[i][j] += x[i] * x[j]
    return _solve(XtX, Xty)


def _sse(mats, ys, betas, t1, t2) -> float:
    s = 0.0
    for t, y in zip(mats, ys):
        pred = sum(b * xi for b, xi in zip(betas, _basis(t, t1, t2)))
        s += (pred - y) ** 2
    return s


def fit_svensson(maturities, yields) -> dict:
    mats = [max(0.01, float(t)) for t in maturities]
    ys = [float(y) for y in yields]
    if len(mats) < 4:                       # too few points: flat curve at the mean
        m = sum(ys) / len(ys) if ys else 0.0
        return {"beta0": m, "beta1": 0.0, "beta2": 0.0, "beta3": 0.0,
                "tau1": 2.0, "tau2": 10.0, "sse": 0.0, "n": len(ys),
                "t_min": round(min(mats), 2) if mats else 0.05,
                "t_max": round(max(mats), 2) if mats else 30.0}
    best = None
    for t1 in _TAU1_GRID:
        for t2 in _TAU2_GRID:
            if t2 <= t1:
                continue
            betas = _fit_betas(mats, ys, t1, t2)
            sse = _sse(mats, ys, betas, t1, t2)
            if best is None or sse < best[0]:
                best = (sse, betas, t1, t2)
    sse, betas, t1, t2 = best
    return {"beta0": betas[0], "beta1": betas[1], "beta2": betas[2], "beta3": betas[3],
            "tau1": t1, "tau2": t2, "sse": round(sse, 6), "n": len(ys),
            "t_min": round(min(mats), 2), "t_max": round(max(mats), 2)}


def curve_yield(params: dict, t: float) -> float:
    # flat-forward extrapolation outside the fitted maturity span: a Svensson curve
    # is unconstrained beyond its data and will blow up, so clamp to [t_min, t_max].
    t = max(0.01, float(t))
    lo, hi = params.get("t_min", 0.05), params.get("t_max", 30.0)
    t = min(max(t, lo), hi)
    x = _basis(t, params["tau1"], params["tau2"])
    return (params["beta0"] * x[0] + params["beta1"] * x[1]
            + params["beta2"] * x[2] + params["beta3"] * x[3])


def bond_price_from_yield(face: float, coupon_pct: float, ytm_pct: float,
                          years: float, freq: int = 1) -> float:
    """Present value of an annual (or freq-per-year) coupon bond at a flat ytm."""
    y = ytm_pct / 100.0 / freq
    n = max(1, int(round(years * freq)))
    cpn = coupon_pct / 100.0 * face / freq
    price = sum(cpn / ((1 + y) ** t) for t in range(1, n + 1))
    price += face / ((1 + y) ** n)
    return price
