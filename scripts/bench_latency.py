"""Bench de latencias locales de CLAUDIO (sin red salvo Ollama opcional).

Uso:
    .venv\\Scripts\\python scripts/bench_latency.py
"""
import time

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

CASES = [
    "poné RAMMA en discord",
    "Más de Rackynolds, Oro beso, Nostalgia",
    "Música de Racky KIRBY",
    "buscá el último partido de River y Boca",
    "qué hora es",
    "Saja B",
    "pausá la música",
    "La playlist de Villeros, con doble L",
    "hola claudio todo bien?",
    "poné eye of the tiger",
]


def bench(fn, cases, n=300):
    t0 = time.perf_counter()
    for _ in range(n):
        for c in cases:
            fn(c)
    dt = time.perf_counter() - t0
    total = n * len(cases)
    return dt, dt / total * 1000


def main():
    import core.brain as b

    for name, fn in [
        ("normalize_user_text", b.normalize_user_text),
        ("fast_path_intent", lambda t: b.fast_path_intent(t)),
        ("music_query_from_text", b.music_query_from_text),
        ("_has_known_music", lambda t: b._has_known_music(t)),
        ("_bare_artist", lambda t: b._bare_artist(t)),
    ]:
        dt, ms = bench(fn, CASES)
        print(f"{name}: {dt:.2f}s total, {ms:.3f} ms/llamada")

    # Tamaño de prompts (tokens ~= chars/4).
    print("\n--- tokens aprox por prompt ---")
    import inspect
    for label, src in [("_chat", "_chat"), ("_synthesize", "_synthesize"),
                       ("_understand", "_understand"), ("_plan", "_plan")]:
        try:
            n = len(inspect.getsource(getattr(b.CLAUDIOBrain, src)))
            print(f"prompt {label}: ~{n} chars codigo (~{n // 4} tok estaticos)")
        except Exception as e:
            print(label, "?", e)


if __name__ == "__main__":
    main()
