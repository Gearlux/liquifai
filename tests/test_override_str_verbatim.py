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

from typing import Any, Dict, List, Optional

import pytest

from liquifai.overrides import parse_override_args

#: The parameter names the active command declares as ``str``.
_STR_PARAMS = frozenset({"description", "name"})


def _parse(args: List[str], verbatim: Any = _STR_PARAMS) -> Dict[str, Any]:
    overrides, _deletions, _dropped = parse_override_args(args, verbatim_keys=verbatim)
    return overrides


class TestStrParamsAreVerbatim:
    def test_embedded_newline_survives(self) -> None:
        """The reported bug: a multi-line description must keep its line break."""
        text = 'h5 test data for Helios I\ncreated with "python SplitAndConvertToHDF5.py -s v=X"'
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
