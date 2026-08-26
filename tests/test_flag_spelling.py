"""A ``--kebab-case`` flag reaches the ``snake_case`` parameter it obviously means.

Every CLI in the wild spells a multi-word option with hyphens, and Python spells the parameter it
binds to with underscores. liquifai accepted only the underscore form, so ``--custom-node URL``
parsed into an override key ``custom-node``, matched the parameter ``custom_node`` never, and did
nothing — no error, no warning, because a well-formed ``--key value`` token is not "dropped", it is
simply applied to a config key nothing reads.

Normalising is safe rather than a guess: **a hyphen can never appear in a Python identifier**, so a
hyphenated override key could not have been addressing a parameter under any spelling. The polarity
suffixes (``--key-`` / ``--key+``) are read BEFORE normalisation, so the trailing hyphen that means
"false" is never mistaken for a word separator.
"""

import pytest

from liquifai.overrides import parse_override_args


class TestKebabReachesTheParameter:
    def test_dash_space_form(self) -> None:
        overrides, _, dropped = parse_override_args(["--custom-node", "URL"])
        assert overrides == {"custom_node": "URL"}
        assert dropped == []

    def test_dash_equals_form(self) -> None:
        overrides, _, _ = parse_override_args(["--comfy-dir=/tmp/x"])
        assert overrides == {"comfy_dir": "/tmp/x"}

    def test_bare_key_value_form(self) -> None:
        overrides, _, _ = parse_override_args(["default-nodes=false"])
        assert overrides == {"default_nodes": False}

    def test_implicit_true_flag(self) -> None:
        overrides, _, _ = parse_override_args(["--only-taidal-nodes"])
        assert overrides == {"only_taidal_nodes": True}

    def test_the_underscore_spelling_still_works(self) -> None:
        """Both spellings land on the same key — this is an alias, not a replacement."""
        assert parse_override_args(["--custom_node", "URL"])[0] == {"custom_node": "URL"}
        assert parse_override_args(["--custom-node", "URL"])[0] == {"custom_node": "URL"}

    def test_add_and_delete_forms_normalise_too(self) -> None:
        overrides, deletions, _ = parse_override_args(["+custom-node=URL", "~default-nodes"])
        assert overrides == {"custom_node": "URL"}
        assert deletions == ["default_nodes"]

    def test_a_dotted_key_normalises_every_segment(self) -> None:
        overrides, _, _ = parse_override_args(["--my-opt.learning-rate", "0.1"])
        assert overrides == {"my_opt.learning_rate": 0.1}


class TestPolarityIsReadBeforeNormalising:
    """``--key-`` means FALSE. The trailing hyphen must not be read as a word separator."""

    def test_trailing_dash_is_false_not_a_separator(self) -> None:
        assert parse_override_args(["--default-nodes-"])[0] == {"default_nodes": False}

    def test_trailing_plus_is_true(self) -> None:
        assert parse_override_args(["--default-nodes+"])[0] == {"default_nodes": True}

    def test_underscore_spelling_of_polarity_is_unchanged(self) -> None:
        assert parse_override_args(["--default_nodes-"])[0] == {"default_nodes": False}


class TestValuesAreNeverTouched:
    """Only KEYS are normalised — a hyphen in a value is data."""

    @pytest.mark.parametrize(
        "argv",
        [
            ["--custom-node", "https://github.com/some-org/some-repo"],
            ["--custom-node=https://github.com/some-org/some-repo"],
            ["custom-node=https://github.com/some-org/some-repo"],
        ],
    )
    def test_a_hyphenated_value_survives(self, argv: list) -> None:
        assert parse_override_args(argv)[0] == {"custom_node": "https://github.com/some-org/some-repo"}

    def test_a_negative_number_is_a_value_not_a_flag(self) -> None:
        assert parse_override_args(["--noise-power-db", "-30"])[0] == {"noise_power_db": -30}
