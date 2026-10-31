"""A CLI value bound to a ``str`` parameter reaches the command exactly as typed.

Every ``--flag value`` is coerced through ``confluid.parse_value``, which ends in
``yaml.safe_load``. That is right for an untyped config override (``+trainer.lr=0.01``
should become a float) and wrong for a parameter the command already declares as
``str``: YAML folds a multi-line plain scalar to a single line, reads ``#…`` as a
comment, ``3:30`` as sexagesimal, ``012`` as octal and ``yes`` as True.

Reported against a real command:

    ds version-update NAME 0.0 --description $'h5 test data\ncreated with "python …"'

arrived as ``'h5 test data created with "python …"'`` — the newline silently gone.

So: when the active command annotates a parameter ``str``, its value is taken
verbatim. Everything else keeps coercing.
"""

import sys
from pathlib import Path
from typing import Any, Dict, List, Optional

import pytest
from confluid import configurable

from liquifai import LiquifyApp
from liquifai.context import set_context
from liquifai.overrides import parse_override_args

#: The parameter names the active command declares as ``str``.
_STR_PARAMS = frozenset({"description", "name"})


def _parse(args: List[str], verbatim: Any = _STR_PARAMS) -> Dict[str, Any]:
    overrides, _deletions, _dropped = parse_override_args(args, verbatim_keys=verbatim)
    return overrides


class TestStrParamsAreVerbatim:
    def test_embedded_newline_survives(self) -> None:
        """The reported bug: a multi-line description must keep its line break."""
        text = 'h5 test data for Raidar I\ncreated with "python SplitAndConvertToHDF5.py -s v=X"'
        assert _parse(["--description", text])["description"] == text

    @pytest.mark.parametrize(
        ("value", "would_have_become"),
        [
            ("#1 priority", "None (read as a YAML comment)"),
            ("3:30", "210 (YAML 1.1 sexagesimal)"),
            ("012", "10 (octal)"),
            ("yes", "True"),
            ("on", "True"),
            ("2026-08-27", "a datetime.date"),
            ("key: value", "a dict"),
            ("  padded  ", "'padded' (stripped)"),
        ],
    )
    def test_yaml_surprises_stay_strings(self, value: str, would_have_become: str) -> None:
        got = _parse(["--description", value])["description"]
        assert got == value, f"{value!r} was coerced; it used to become {would_have_become}"
        assert isinstance(got, str)

    def test_equals_form_is_verbatim_too(self) -> None:
        assert _parse(["--description=#1 priority"])["description"] == "#1 priority"

    def test_hyphen_spelling_reaches_the_underscore_parameter(self) -> None:
        got = parse_override_args(["--description", "yes"], verbatim_keys=frozenset({"description"}))[0]
        assert got["description"] == "yes"


class TestEverythingElseStillCoerces:
    def test_a_parameter_not_declared_str_still_coerces(self) -> None:
        """`--limit 5` must still arrive as an int, not "5"."""
        assert _parse(["--limit", "5"]) == {"limit": 5}

    def test_an_unknown_key_still_coerces(self) -> None:
        """A free-form config override names no parameter, so YAML typing is correct."""
        assert _parse(["--trainer_lr", "0.01"]) == {"trainer_lr": 0.01}

    def test_a_dotted_key_still_coerces(self) -> None:
        """A dotted key addresses a nested config object, not the command signature."""
        assert _parse(["--optimizer.lr", "0.01"]) == {"optimizer.lr": 0.01}

    def test_bool_and_polarity_forms_are_untouched(self) -> None:
        assert _parse(["--append"]) == {"append": True}
        assert _parse(["--append-"]) == {"append": False}

    def test_no_verbatim_keys_is_the_old_behaviour(self) -> None:
        """Callers that pass nothing keep coercing everything — the default is a no-op."""
        overrides, _d, _dr = parse_override_args(["--description", "yes"])
        assert overrides == {"description": True}


class TestSignatureDerivation:
    def test_str_annotated_params_are_collected(self) -> None:
        """The app derives the verbatim set from the ACTIVE command's annotations."""
        from liquifai.overrides import str_param_names

        def cmd(name: str, count: int, description: str = "", flag: bool = False) -> None: ...

        assert str_param_names(cmd) == {"name", "description"}

    def test_optional_str_counts_as_str(self) -> None:
        from liquifai.overrides import str_param_names

        def cmd(note: Optional[str] = None) -> None: ...

        assert str_param_names(cmd) == {"note"}

    def test_unintrospectable_callable_yields_nothing(self) -> None:
        from liquifai.overrides import str_param_names

        assert str_param_names(None) == set()


class TestAConfigKeyHoldingTextKeepsTheValueAsTyped:
    """A top-level key the loaded config holds as text takes a typed value as text.

    Reported: ``run render.yaml --version "1.3"`` — the shell drops the quotes, YAML reads ``1.3`` as a float, and the
    class taking ``version: str`` refused it (``1.10`` would have become ``1.1``, another version). The config's own
    value (``version: latest``) already says the key is text.
    """

    _DOCUMENT = {"version": "latest", "dataset": "", "exclude": "*.pickle.json", "limit": 20, "downloaded": False}

    def _parse(self, args: List[str]) -> Dict[str, Any]:
        from liquifai.overrides import text_config_keys

        overrides, _deletions, _dropped = parse_override_args(args, verbatim_keys=text_config_keys(self._DOCUMENT))
        return overrides

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["--version", "1.3"], {"version": "1.3"}),
            (["--version", "1.10"], {"version": "1.10"}),
            (["version=2.0"], {"version": "2.0"}),
            (["--dataset", "2024"], {"dataset": "2024"}),
            (["--version", "'1.3'"], {"version": "'1.3'"}),
            (["--version", "null"], {"version": "null"}),
            (["--exclude", "[a, b]"], {"exclude": "[a, b]"}),
        ],
    )
    def test_the_typed_text_arrives_unchanged(self, args: List[str], expected: Dict[str, Any]) -> None:
        assert self._parse(args) == expected

    @pytest.mark.parametrize(
        ("args", "expected"),
        [
            (["--limit", "30"], {"limit": 30}),
            (["--downloaded", "true"], {"downloaded": True}),
            (["+trainer.lr=0.01"], {"trainer.lr": 0.01}),
            (["--sets.0.version", "1.3"], {"sets.0.version": 1.3}),
            (["--samples", "5"], {"samples": 5}),
        ],
    )
    def test_a_key_not_held_as_text_still_reads_as_yaml(self, args: List[str], expected: Dict[str, Any]) -> None:
        assert self._parse(args) == expected

    def test_only_top_level_text_values_count(self) -> None:
        from liquifai.overrides import text_config_keys

        document = {"version": "latest", "limit": 20, "nested": {"name": "x"}, "items": ["a"], "none": None}
        assert text_config_keys(document) == {"version"}

    def test_a_document_that_is_no_mapping_holds_no_text_keys(self) -> None:
        from liquifai.overrides import text_config_keys

        assert text_config_keys(None) == set()
        assert text_config_keys(["version"]) == set()


@configurable
class _VersionedSource:
    """End-to-end fixture: a class that takes its version as text, fed by a top-level key."""

    def __init__(self, version: str = "latest", limit: int = 0) -> None:
        self.version, self.limit = version, limit


def _run_versioned(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, document: str, *flags: str) -> "_VersionedSource":
    app = LiquifyApp(name="versioned-app")
    seen: List[_VersionedSource] = []

    @app.script_command(flow_mode="auto")
    def go(source: Any) -> None:
        seen.append(source)

    config = tmp_path / "cfg.yaml"
    config.write_text(document)
    monkeypatch.setattr(sys, "argv", ["versioned-app", "go", str(config), *flags])
    set_context(None)
    app.run()
    (source,) = seen
    return source


_VERSIONED_DOCUMENT = "version: latest\nlimit: 20\nsource: !class:_VersionedSource {}\n"


class TestTheRealRun:
    def test_a_version_typed_like_a_number_reaches_the_class_as_text(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _run_versioned(tmp_path, monkeypatch, _VERSIONED_DOCUMENT, "--version", "1.10")
        assert source.version == "1.10"

    def test_a_number_key_still_reaches_the_class_as_a_number(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        source = _run_versioned(tmp_path, monkeypatch, _VERSIONED_DOCUMENT, "--limit", "30")
        assert source.limit == 30

    def test_a_key_the_document_does_not_hold_still_reads_as_yaml(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Without ``version:`` in the document the typed ``1.3`` stays a number, as before."""
        source = _run_versioned(tmp_path, monkeypatch, "source: !class:_VersionedSource {}\n", "--limit", "1.0")
        assert source.limit == 1.0
