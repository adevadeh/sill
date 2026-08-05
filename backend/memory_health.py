#!/usr/bin/env python3
"""
memory_health.py — health metrics for the Sill memory corpus.

Applies the intrinsic-dimension / distance-concentration diagnostics from the
attention spatial-index experiments (gitlab.com/ptaysom/psi, attention/) to the
memory embeddings themselves.

This is NOT a retrieval speedup. The corpus is far too small for approximate
nearest-neighbor indexing to beat exact brute force (that crossover is tens of
thousands of vectors). It is a *health monitor* that watches three things as
the corpus grows:

  intrinsic_dim   how many dimensions the memory cloud actually occupies
                  (TwoNN estimator). If this plateaus while n keeps climbing
                  -> REDUNDANCY: new memories are variations of what's stored.
  concentration   mean (farthest / nearest) distance ratio. Falling toward 1
                  -> DISCRIMINABILITY LOSS: recall can't separate relevant from
                  irrelevant, so top-k recall degrades.
  near_dupes      pairs with cosine >= DUP_THRESH. Actionable at ANY n:
                  concrete merge/supersede candidates.

Each run appends to memory_health_history.jsonl and reports DRIFT vs the
previous run — that is where the redundancy and concentration signals live,
since they are about change over time, not any single snapshot.

Usage:
    python memory_health.py            # reads DB conn from backend/.env

Reuses the same Postgres the MCP server uses (POSTGRES_* in backend/.env).
"""
import json
import os
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

try:
    import numpy as np
except ImportError as exc:
    raise ImportError(
        "memory_health.py needs numpy, which is an optional dependency (this "
        "is a diagnostic tool, not on the core memory-store path, so a plain "
        "install of sill-memory does not pull it in). Install it with:\n"
        "    pip install -e '.[diagnostics]'\n"
        "or, into an existing environment:\n"
        "    pip install numpy"
    ) from exc

import psycopg2

BACKEND_DIR = Path(__file__).resolve().parent
HISTORY = BACKEND_DIR / "memory_health_history.jsonl"

# thresholds (tune as the corpus grows)
DUP_THRESH = 0.95          # cosine >= this == near-duplicate
MIN_N_FOR_ID = 50          # below this, intrinsic-dim estimate is illustrative
REDUNDANCY_N_GROWTH = 0.25  # if n grew >25% ...
REDUNDANCY_ID_GROWTH = 0.05  # ... but intrinsic dim grew <5% -> flag
CONCENTRATION_DROP = 0.15   # relative drop in far/near that warns


# ---- estimators (same math as attention/intrinsic_dim_pruning.py) --------
def _normalize(X):
    return X / np.maximum(np.linalg.norm(X, axis=1, keepdims=True), 1e-12)

def twonn_dim(Xn):
    g = Xn @ Xn.T
    diag = np.diag(g)
    D2 = np.maximum(diag[:, None] + diag[None, :] - 2 * g, 0.0)
    np.fill_diagonal(D2, np.inf)
    r = np.sqrt(np.sort(D2, axis=1)[:, :2])
    mu = r[:, 1] / np.maximum(r[:, 0], 1e-12)
    mu = mu[np.isfinite(mu) & (mu > 1.0)]
    return float(len(mu) / np.sum(np.log(mu))) if len(mu) else float("nan")

def concentration(Xn):
    g = Xn @ Xn.T
    diag = np.diag(g)
    D = np.sqrt(np.maximum(diag[:, None] + diag[None, :] - 2 * g, 0.0))
    np.fill_diagonal(D, np.nan)
    near = np.nanmin(D, axis=1)
    far = np.nanmax(D, axis=1)
    return float(np.mean(far / np.maximum(near, 1e-12)))


# ---- data ----------------------------------------------------------------
def load_env(env_path):
    if env_path.exists():
        for line in env_path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                os.environ.setdefault(k.strip(), v.strip())

def load_memories():
    load_env(BACKEND_DIR / ".env")
    conn = psycopg2.connect(
        host=os.environ.get("POSTGRES_HOST", "localhost"),
        port=os.environ.get("POSTGRES_PORT", "5432"),
        dbname=os.environ.get("POSTGRES_DB", "sill"),
        user=os.environ.get("POSTGRES_USER", "sill"),
        password=os.environ.get("POSTGRES_PASSWORD", ""),
    )
    with conn, conn.cursor() as cur:
        cur.execute("SELECT id, type::text, left(content, 70), embedding::text "
                    "FROM memories WHERE status = 'active'")
        rows = cur.fetchall()
    conn.close()
    ids = [r[0] for r in rows]
    types = [r[1] for r in rows]
    snippets = [r[2] for r in rows]
    X = np.array([np.fromstring(r[3].strip("[]"), sep=",") for r in rows])
    return ids, types, snippets, X


# ---- report --------------------------------------------------------------
def main():
    ids, types, snippets, X = load_memories()
    n = len(ids)
    if n < 3:
        print(f"corpus too small (n={n}) — nothing to measure yet.")
        return
    Xn = _normalize(X)

    idim = twonn_dim(Xn)
    conc = concentration(Xn)
    cos = Xn @ Xn.T
    iu = np.triu_indices(n, k=1)
    cos_off = cos[iu]
    mean_cos = float(cos_off.mean())

    dup_pairs = [(i, j) for i, j in zip(*iu) if cos[i, j] >= DUP_THRESH]
    type_counts = {t: types.count(t) for t in sorted(set(types))}

    print(f"Sill memory health — {datetime.now(timezone.utc):%Y-%m-%d %H:%M UTC}")
    print(f"  n active memories : {n}   ({', '.join(f'{t}:{c}' for t,c in type_counts.items())})")
    id_note = "" if n >= MIN_N_FOR_ID else f"  [illustrative — need n>={MIN_N_FOR_ID} to trust]"
    print(f"  intrinsic dim     : {idim:.1f} / 768 ambient{id_note}")
    print(f"  concentration     : {conc:.2f}  (far/near; ->1 = recall losing separation)")
    print(f"  mean pairwise cos : {mean_cos:.3f}")
    print(f"  near-duplicates   : {len(dup_pairs)} pair(s) at cos>={DUP_THRESH}")
    for i, j in dup_pairs[:8]:
        print(f"      {cos[i,j]:.3f}  «{snippets[i]}»  ~  «{snippets[j]}»")

    # ---- drift vs last run ----
    prev = None
    if HISTORY.exists():
        lines = [l for l in HISTORY.read_text().splitlines() if l.strip()]
        if lines:
            prev = json.loads(lines[-1])

    print("\ndrift vs previous run:")
    if not prev:
        print("  (no prior run — this snapshot becomes the baseline)")
    else:
        dn = n - prev["n"]
        print(f"  n: {prev['n']} -> {n} ({dn:+d})")
        print(f"  intrinsic dim: {prev['intrinsic_dim']:.1f} -> {idim:.1f}")
        print(f"  concentration: {prev['concentration']:.2f} -> {conc:.2f}")
        # redundancy: corpus grew a lot but the manifold barely did
        if prev["n"] > 0 and dn > 0:
            n_growth = dn / prev["n"]
            id_growth = (idim - prev["intrinsic_dim"]) / max(prev["intrinsic_dim"], 1e-9)
            if n_growth > REDUNDANCY_N_GROWTH and id_growth < REDUNDANCY_ID_GROWTH:
                print(f"  ⚠ REDUNDANCY: n +{n_growth:.0%} but intrinsic dim +{id_growth:.0%} "
                      "— storing variations, not new ground.")
        # discriminability: far/near falling
        if prev["concentration"] > 0:
            drop = (prev["concentration"] - conc) / prev["concentration"]
            if drop > CONCENTRATION_DROP:
                print(f"  ⚠ DISCRIMINABILITY: far/near fell {drop:.0%} "
                      "— recall separation declining.")
        if len(dup_pairs) > prev.get("near_dupes", 0):
            print(f"  ⚠ near-duplicates rose {prev.get('near_dupes',0)} -> {len(dup_pairs)} "
                  "— review the pairs above to merge/supersede.")

    # ---- append snapshot ----
    with HISTORY.open("a") as f:
        f.write(json.dumps({
            "ts": time.time(),
            "n": n,
            "intrinsic_dim": round(idim, 3),
            "concentration": round(conc, 4),
            "mean_cos": round(mean_cos, 4),
            "near_dupes": len(dup_pairs),
        }) + "\n")
    print(f"\nappended snapshot to {HISTORY.name}")


if __name__ == "__main__":
    sys.exit(main())
