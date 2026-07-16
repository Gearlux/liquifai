# linefit — a training-style liquifai CLI

`linefit` is the *script command* showcase: a `fit` / `validate` / `test` /
`predict` CLI whose YAML instantiates the model, data, and optimizer, and
whose command signatures declare what gets injected. If you know PyTorch
Lightning's `LightningCLI`, this is that shape — one small file, no
boilerplate. ([`pypeek`](../pypeek/README.md) covers the other half of the
framework: pure operations, generated commands, MCP tools, completion caching.)

The "training" is honest but tiny: gradient descent recovering the slope and
intercept of a noisy synthetic line (`y = 2x - 1`). Pure Python, offline,
milliseconds.

## Install & run

```bash
pip install -e examples/linefit        # from the liquifai repo root
linefit fit linefit.yaml
```

```
epoch    0  train_loss=0.263224
epoch  100  train_loss=0.002049
...
fit: train_loss=0.001903
fit: recovered w=2.027 b=-1.007 (target w=2.0 b=-1.0)
```

(Without installing: `python examples/linefit/linefit.py fit linefit.yaml`
from this directory.)

## The verbs

| Verb | What it does |
|---|---|
| `fit <config>` | Train; report the recovered line |
| `validate <config>` | Train, then loss on the validation split |
| `test <config>` | Train, then loss on the held-out test split |
| `predict <config> <xs>` | Predict for comma-separated x values — with the model **as configured** (no training) |
| `print-config <config>` | Dump the fully-merged config (file + CLI overrides) as reloadable YAML |

Each verb is an `@app.script_command`: the first positional argument is
**promoted to a config path** (`linefit fit experiment.yaml` — the
`LightningCLI fit --config …` move), and the function signature declares the
dependency:

```python
@app.script_command(flow_mode="auto")
def fit(trainer: Trainer) -> None:
    loss = trainer.fit()
```

Liquifai builds the `Trainer` from its config block and injects it —
`flow_mode="auto"` deep-flows the nested `!class:` model and data while the
`Lazy`-annotated optimizer stays deferred, so `fit()` can flow it with the one
argument only the run can supply (the live model).

## The config file

```yaml
seed: 42                      # bare key: broadcasts into every accepting component

Trainer:                      # selected for `trainer: Trainer` by class name
  max_epochs: 400
  model: !class:LinearModel()       # built eagerly at load
  data: !class:SyntheticData()
  optimizer: !lazy:GradientDescent  # deferred until fit() supplies the model
    lr: 0.3

SyntheticData:                # class-name block: every SyntheticData instance
  n: 128
  slope: 2.0
  intercept: -1.0
```

## Overrides from the CLI

Any knob in the loaded config is reachable without editing the file; all
forms interoperate in one invocation:

```bash
linefit fit linefit.yaml --Trainer.max_epochs 50        # dotted flag (block path)
linefit fit linefit.yaml --LinearModel.w 1.5            # class-name path
linefit fit linefit.yaml Trainer.optimizer.lr=0.1       # bare key=value form
linefit fit linefit.yaml --noise 0.1                    # bare key: broadcasts
linefit predict linefit.yaml 0,1,2 --LinearModel.w 2 --LinearModel.b -1
```

A typo'd token is **warned about, never silently dropped**.

## The reproducibility loop

`print-config` dumps the merged configuration — file, includes, and CLI
overrides, with the `!lazy:` optimizer still deferred — as a config the app
can load again:

```bash
linefit print-config linefit.yaml --Trainer.max_epochs 800 > frozen.yaml
linefit fit frozen.yaml        # exact rerun, months later
```

## Failure contract

Bad input raises `LinefitError` (a `LiquifaiError`), so the CLI prints ONE
clean `Error: …` line and exits 1 — rerun with `--debug` for the traceback:

```bash
linefit predict linefit.yaml abc
# Error: --xs expects comma-separated numbers, got 'abc'
```

## Files

- [`linefit.py`](linefit.py) — the whole app (~200 lines with docs)
- [`linefit.yaml`](linefit.yaml) — the sample experiment config
- [`pyproject.toml`](pyproject.toml) — console script + `liquifai.apps` entry point
- [`run.py`](run.py) — the CI self-test (drives every verb as a subprocess)
