"""CI self-test for the linefit example app (see linefit.py / README.md).

Drives ``python linefit.py <verb> ...`` as subprocesses — no installation, no
network — and asserts on the output. The ``examples/*/run.py`` glob in CI
executes this file; pypeek (network-dependent) deliberately ships no run.py.
"""

import subprocess
import sys
from pathlib import Path

APP_DIR = Path(__file__).parent
APP = APP_DIR / "linefit.py"


def run(*args: str, expect_rc: int = 0) -> str:
    """Run a linefit invocation; return its stdout (asserting the exit code)."""
    result = subprocess.run([sys.executable, str(APP), *args], cwd=APP_DIR, capture_output=True, text=True, timeout=120)
    label = "linefit " + " ".join(args)
    assert result.returncode == expect_rc, (
        f"{label!r} exited {result.returncode} (expected {expect_rc})\n"
        f"--- stdout ---\n{result.stdout}\n--- stderr ---\n{result.stderr}"
    )
    if expect_rc != 0:
        # The CLI failure contract: ONE clean error line, never a traceback.
        assert "Traceback" not in result.stderr, f"expected a clean error, got:\n{result.stderr}"
    print(f"ok: {label}")
    return result.stdout


def main() -> None:
    # 1. Plain fit: config promotion loads linefit.yaml, training converges.
    out = run("fit", "linefit.yaml")
    assert "recovered w=2.0" in out, f"training did not converge:\n{out}"

    # 2. Dotted flag overrides reach nested knobs (block path + class-name path).
    out = run("fit", "linefit.yaml", "--Trainer.max_epochs", "50", "--Trainer.log_every", "0")
    assert "epoch" not in out, "log_every=0 should silence per-epoch progress"

    # 3. The bare key=value deep-dotted form works too.
    out = run("fit", "linefit.yaml", "Trainer.optimizer.lr=0.05", "Trainer.max_epochs=1")
    assert "train_loss" in out

    # 4. print-config dumps the MERGED config (file + CLI overrides), reloadable.
    out = run("print-config", "linefit.yaml", "--Trainer.max_epochs", "7")
    assert "max_epochs: 7" in out, f"override missing from the dump:\n{out}"
    # `dump()` emits the plain format, so the deferred optimizer round-trips as
    # `_target_: GradientDescent` + `_partial_: true` rather than a `!lazy:` tag.
    assert "_target_: GradientDescent" in out, "the optimizer must survive in the recipe"
    assert "_partial_: true" in out, "the optimizer must survive DEFERRED in the recipe"
    frozen = APP_DIR / "frozen_ci.yaml"
    frozen.write_text(out[out.index("Trainer:") :])
    try:
        out = run("fit", frozen.name)  # the reproducibility loop: rerun the dump
        assert "train_loss" in out
    finally:
        frozen.unlink()

    # 5. validate / test evaluate on held-out splits.
    out = run("validate", "linefit.yaml", "--Trainer.log_every", "0")
    assert "val_loss=" in out
    out = run("test", "linefit.yaml", "--Trainer.log_every", "0")
    assert "test_loss=" in out

    # 6. predict: positional xs + overrides configuring the model directly.
    out = run("predict", "linefit.yaml", "0,1,2", "--LinearModel.w", "2.0", "--LinearModel.b", "-1.0")
    assert "x=1 -> y=1.0000" in out, f"configured model should predict y=2x-1:\n{out}"

    # 7. Failure contract: bad input -> ONE clean error line + exit 1.
    out = run("predict", "linefit.yaml", "abc", expect_rc=1)

    print("PASS: linefit self-test (7 scenarios)")


if __name__ == "__main__":
    main()
