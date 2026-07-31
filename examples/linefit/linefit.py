"""linefit — a training-style liquifai CLI (think Lightning CLI, one file).

Where pypeek shows the *operations* half of liquifai (pure dict-returning
functions -> generated CLI + MCP tools), linefit shows the *script command*
half: verbs whose SIGNATURES declare the objects they need, a YAML file that
wires those objects with confluid tags, and CLI overrides that reach any knob.

* **Config promotion** — ``linefit fit experiment.yaml`` loads the config
  before the verb runs (the ``LightningCLI fit --config …`` move).
* **Dependency injection** — ``def fit(trainer: Trainer)``: liquifai builds
  the ``Trainer`` from its config block and injects it; the ``!class:`` model
  and data land inside it, the ``!lazy:`` optimizer stays deferred until
  ``fit()`` supplies the model's parameters (``flow_mode="auto"``).
* **Dotted overrides** — ``--Trainer.max_epochs 50``, ``--LinearModel.w 1.5``,
  or bare ``Trainer.optimizer.lr=0.1`` tweak the loaded config from the CLI
  (paths start at a class-name block; a bare ``--noise 0.1`` broadcasts).
* **print-config** — dumps the fully-merged configuration (file + overrides)
  as reloadable YAML: ``linefit print-config exp.yaml > frozen.yaml`` then
  ``linefit fit frozen.yaml`` reruns the exact experiment.

The "training" is honest but tiny: gradient descent recovering the slope and
intercept of a noisy synthetic line — pure Python, no ML dependencies, runs
offline in milliseconds. Try it (from the liquifai repo root)::

    pip install -e examples/linefit
    linefit fit linefit.yaml
    linefit fit linefit.yaml --Trainer.max_epochs 50 --LinearModel.w 1.5
    linefit print-config linefit.yaml

See README.md next to this file for the full walkthrough.
"""

import random
from typing import Any, List, Literal, Optional, Tuple

import confluid
from confluid import Lazy, LazyClass, NoBroadcast, flow

from liquifai import LiquifyApp
from liquifai.exceptions import LiquifaiError

app = LiquifyApp(name="linefit", description="Fit a least-squares line — a training-style liquifai demo.")


class LinefitError(LiquifaiError):
    """A user-facing linefit failure (bad input, unusable config).

    Subclassing ``LiquifaiError`` opts into liquifai's CLI failure contract:
    one clean ``Error: …`` line + exit 1 (full traceback under ``--debug``).
    """


# ---------------------------------------------------------------------------
# Components — plain @configurable classes; the YAML wires them together
# ---------------------------------------------------------------------------


@confluid.configurable
class SyntheticData:
    """Noisy records of the line y = slope*x + intercept.

    Args:
        n: Total number of points (split 80/10/10 into train/val/test).
        slope: Ground-truth slope the trainer should recover.
        intercept: Ground-truth intercept.
        noise: Standard deviation of the Gaussian noise on y.
        seed: RNG seed (a bare top-level ``seed:`` key broadcasts here).
    """

    def __init__(
        self, n: int = 128, slope: float = 2.0, intercept: float = -1.0, noise: float = 0.05, seed: int = 0
    ) -> None:
        self.n = n
        self.slope = slope
        self.intercept = intercept
        self.noise = noise
        self.seed = seed

    @property
    def points(self) -> List[Tuple[float, float]]:
        rng = random.Random(self.seed)
        xs = [i / max(self.n - 1, 1) for i in range(self.n)]
        return [(x, self.slope * x + self.intercept + rng.gauss(0.0, self.noise)) for x in xs]

    def split(self, name: Literal["train", "val", "test"]) -> List[Tuple[float, float]]:
        """The 80/10/10 partition backing the fit/validate/test verbs."""
        points = self.points
        n_train, n_val = int(self.n * 0.8), int(self.n * 0.1)
        if name == "train":
            return points[:n_train]
        if name == "val":
            return points[n_train : n_train + n_val]
        return points[n_train + n_val :]


@confluid.configurable
class LinearModel:
    """The model: y = w*x + b.

    Args:
        w: Initial (or, for ``predict``, the effective) slope parameter.
        b: Initial intercept parameter.
    """

    def __init__(self, w: float = 0.0, b: float = 0.0) -> None:
        self.w = w
        self.b = b
        self._params: Optional[List[float]] = None

    @property
    def params(self) -> List[float]:
        """The trainable parameter list — created on first use, mutated in place by the optimizer."""
        if self._params is None:
            self._params = [self.w, self.b]
        return self._params

    def predict(self, x: float) -> float:
        w, b = self.params
        return w * x + b

    def loss_and_grads(self, points: List[Tuple[float, float]]) -> Tuple[float, List[float]]:
        """Mean-squared error and its analytic gradient over ``points``."""
        n = len(points)
        loss = gw = gb = 0.0
        for x, y in points:
            err = self.predict(x) - y
            loss += err * err / n
            gw += 2.0 * err * x / n
            gb += 2.0 * err / n
        return loss, [gw, gb]


@confluid.configurable
class GradientDescent:
    """Plain gradient descent with optional momentum.

    Args:
        lr: Learning rate.
        momentum: Momentum coefficient (0 = vanilla gradient descent).
        model: The LIVE model whose parameters to update — only the RUN can
            supply it, which is why the YAML declares this component
            ``!lazy:`` and the trainer flows it with ``model=`` inside
            ``fit()``. ``NoBroadcast`` keeps a bare top-level ``model:`` key
            from pre-wiring the slot — it belongs to the run.
    """

    def __init__(self, lr: float = 0.05, momentum: float = 0.0, model: NoBroadcast[Any] = None) -> None:
        self.lr = lr
        self.momentum = momentum
        self.model = model
        self._velocity: Optional[List[float]] = None

    def step(self, grads: List[float]) -> None:
        if self.model is None:
            raise LinefitError("optimizer has no model — it must be flowed with model=...")
        if self._velocity is None:
            self._velocity = [0.0] * len(grads)
        params = self.model.params
        for i, g in enumerate(grads):
            self._velocity[i] = self.momentum * self._velocity[i] + g
            params[i] -= self.lr * self._velocity[i]


@confluid.configurable
class Trainer:
    """Runs the training loop over model + data + a deferred optimizer.

    Args:
        model: The model to fit (``!class:LinearModel()`` in YAML).
        data: The dataset (``!class:SyntheticData()``).
        optimizer: DEFERRED — annotated ``Lazy``, so ``flow_mode="auto"``
            leaves it unbuilt and ``fit()`` flows it with the model's params.
        max_epochs: Training length.
        tolerance: Early-stop when the loss improves less than this.
        log_every: Print progress every N epochs (0 = silent).
    """

    def __init__(
        self,
        model: Optional[LinearModel] = None,
        data: Optional[SyntheticData] = None,
        optimizer: Optional[Lazy[GradientDescent]] = None,
        max_epochs: int = 400,
        tolerance: float = 1.0e-9,
        log_every: int = 100,
    ) -> None:
        self.model = model
        self.data = data
        self.optimizer = optimizer
        self.max_epochs = max_epochs
        self.tolerance = tolerance
        self.log_every = log_every

    def _require(self) -> Tuple[LinearModel, SyntheticData]:
        if not isinstance(self.model, LinearModel) or not isinstance(self.data, SyntheticData):
            raise LinefitError("trainer needs a model and data — wire them in the config file")
        return self.model, self.data

    def fit(self) -> float:
        """Train on the train split; returns the final training loss."""
        model, data = self._require()
        # The canonical runtime injection: the !lazy: optimizer finally gets
        # the one argument only the run can supply — the live model.
        opt = flow(self.optimizer if self.optimizer is not None else LazyClass(GradientDescent), model=model)
        assert isinstance(opt, GradientDescent)
        train = data.split("train")
        previous = float("inf")
        loss = 0.0
        for epoch in range(self.max_epochs):
            loss, grads = model.loss_and_grads(train)
            opt.step(grads)
            if self.log_every and epoch % self.log_every == 0:
                print(f"epoch {epoch:4d}  train_loss={loss:.6f}")
            if abs(previous - loss) < self.tolerance:
                break
            previous = loss
        return loss

    def evaluate(self, split: Literal["val", "test"]) -> float:
        """Mean-squared error of the CURRENT model parameters on a held-out split."""
        model, data = self._require()
        loss, _ = model.loss_and_grads(data.split(split))
        return loss


# ---------------------------------------------------------------------------
# The verbs — signatures declare the dependency, liquifai injects it
# ---------------------------------------------------------------------------


@app.script_command(flow_mode="auto")
def fit(trainer: Trainer) -> None:
    """Train the model and report the recovered line."""
    loss = trainer.fit()
    assert trainer.model is not None and trainer.data is not None
    w, b = trainer.model.params
    print(f"fit: train_loss={loss:.6f}")
    print(f"fit: recovered w={w:.3f} b={b:.3f} (target w={trainer.data.slope} b={trainer.data.intercept})")


@app.script_command(flow_mode="auto")
def validate(trainer: Trainer) -> None:
    """Train, then report the loss on the validation split.

    (A real training app would restore a checkpoint here; the demo model
    retrains in milliseconds, so it just fits first.)
    """
    trainer.fit()
    print(f"validate: val_loss={trainer.evaluate('val'):.6f}")


@app.script_command(flow_mode="auto")
def test(trainer: Trainer) -> None:
    """Train, then report the loss on the held-out test split."""
    trainer.fit()
    print(f"test: test_loss={trainer.evaluate('test'):.6f}")


@app.script_command(flow_mode="auto", positionals=["xs"])
def predict(trainer: Trainer, xs: str = "0.0,0.5,1.0") -> None:
    """Predict y for comma-separated x values — with the model AS CONFIGURED.

    No training happens: the effective parameters come from the config file /
    CLI overrides (``linefit predict exp.yaml 0,1,2 --LinearModel.w 2``), the
    way a real app would predict from restored weights.

    Args:
        xs: Comma-separated x values (positional: ``linefit predict cfg.yaml 0,1,2``).
    """
    assert trainer.model is not None
    try:
        values = [float(token) for token in xs.split(",") if token.strip()]
    except ValueError as exc:
        raise LinefitError(f"--xs expects comma-separated numbers, got {xs!r}") from exc
    for x in values:
        print(f"predict: x={x:g} -> y={trainer.model.predict(x):.4f}")


@app.script_command(name="print-config")
def print_config(trainer: Trainer) -> None:
    """Dump the fully-merged configuration (file + CLI overrides) as YAML.

    The default ``flow_mode="manual"`` keeps nested stubs and the ``!lazy:``
    optimizer deferred, so the dump is the composed RECIPE — reloadable with
    ``linefit fit <dump>`` for an exact rerun. Dumping under the class-name
    key keeps the document a valid app config (the ``Trainer:`` block DI
    selects), not a bare object snapshot.
    """
    print(confluid.dump({"Trainer": trainer}))


def main() -> Any:
    """Console-script entry point."""
    return app.run()


if __name__ == "__main__":
    main()
