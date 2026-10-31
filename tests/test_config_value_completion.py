"""A config key's VALUE on TAB, from the provider the class it feeds registers.

A package registers a provider for one parameter of one of its classes under the ``liquifai.values`` entry-point
group — the name is the parameter's dotted path (``<module>.<Class>.<parameter>``), the value the provider
(``<module>:<function>``). With a config on the line, ``name=<TAB>``, ``--name <TAB>`` and ``--name=<TAB>`` offer
what the provider listed for the ``!class:`` block that key feeds — by its name (broadcast), through ``!ref:``, or by
its dotted path. The provider gets the block's other settings as text — the line's, else the file's, else the class's
defaults — and runs only at refresh time, in the app's process; TAB reads its cache and starts the refresh when the
cache is missing or old. A key no provider answers for completes as before.
"""

from __future__ import annotations

import textwrap
from pathlib import Path
from typing import Any, Dict, List, Mapping, Optional, Tuple

import pytest
from confluid import configurable

from liquifai import LiquifyApp, _fast_complete
from liquifai.completion import cache, config_values
from liquifai.completion import tree as tree_mod
from liquifai.completion.engine import complete_from_tree
from liquifai.completion.shells import bash_word_tail

#: The class path a ``!class:`` tag in these configs names: this module, found by its source.
HERE = __name__


@configurable
class Catalog:
    """A catalog read by name.

    Args:
        name: The catalog's name.
        kind: Its kind.
        local: Only the catalogs on this machine.
        count: How many items to read.
    """

    def __init__(self, name: str = "", kind: str = "big", local: bool = False, count: int = 0) -> None:
        self.name = name
        self.kind = kind
        self.local = local
        self.count = count


@configurable
class Runner:
    """Run over catalogs.

    Args:
        catalogs: The catalogs read.
        source: One more catalog.
    """

    def __init__(self, catalogs: Optional[List[Catalog]] = None, source: Optional[Catalog] = None) -> None:
        self.catalogs = catalogs
        self.source = source


#: What each provider was asked, in order — the settings it was handed.
ASKED: List[Dict[str, str]] = []


def catalog_names(settings: Mapping[str, str]) -> List[str]:
    """The provider of ``Catalog.name``: names that say which settings it was given."""
    ASKED.append(dict(settings))
    names = [f"{settings['kind']}-one", f"{settings['kind']}-two", "with space"]
    return names + (["local-only"] if settings.get("local") == "true" else [])


def failing_kinds(settings: Mapping[str, str]) -> List[str]:
    """The provider of ``Catalog.kind``: it cannot answer (a server down)."""
    raise ConnectionError("the server is down")


@pytest.fixture(autouse=True)
def providers(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    """An installed distribution registering the two providers, and an isolated completion cache."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("LIQUIFAI_NO_LAZY_COMPLETE", "1")
    monkeypatch.delenv("LIQUIFAI_COMPLETION_LIST", raising=False)
    site = tmp_path / "site"
    info = site / "probe_values-0.0.0.dist-info"
    info.mkdir(parents=True)
    (info / "METADATA").write_text("Metadata-Version: 2.1\nName: probe-values\nVersion: 0.0.0\n")
    (info / "entry_points.txt").write_text(
        f"[{config_values.GROUP}]\n"
        f"{HERE}.Catalog.name = {HERE}:catalog_names\n"
        f"{HERE}.Catalog.kind = {HERE}:failing_kinds\n"
    )
    monkeypatch.syspath_prepend(str(site))
    ASKED.clear()
    return site


def _app() -> LiquifyApp:
    app = LiquifyApp(name="valuedemo")

    @app.script_command("run")
    def run(runnable: Any = None, dry_run: bool = False) -> None:
        """Run the runnable.

        Args:
            runnable: The object bound under runnable.
            dry_run: Run nothing.
        """

    return app


def _config(folder: Path, body: str) -> Path:
    path = folder / "run.yaml"
    path.write_text(textwrap.dedent(body))
    return path


@pytest.fixture
def config(tmp_path: Path) -> Path:
    """A config whose top-level ``name`` and ``kind`` are broadcast into a Catalog inside a list."""
    return _config(
        tmp_path,
        f"""\
        name: ""
        kind: small
        runnable: !class:{HERE}.Runner
          catalogs:
            - !class:{HERE}.Catalog {{}}
        """,
    )


class Spawner:
    """The lazy-refresh callback TAB is given: records each refresh it asks for."""

    def __init__(self) -> None:
        self.asked: List[Tuple[str, Dict[str, str]]] = []

    def __call__(self, key: str, inputs: Dict[str, str]) -> None:
        self.asked.append((key, dict(inputs)))


def _tab(line: str, spawner: Optional[Spawner] = None, force: bool = False) -> List[str]:
    words = line.split(" ")
    return complete_from_tree(
        tree_mod.serialize_app(_app()), words, len(words) - 1, lazy_refresh=spawner, force_refresh=force
    )


def _tab_after_refresh(line: str) -> List[str]:
    """TAB once (asking for the refresh), run the refresh it asked for as the detached helper would, TAB again."""
    spawner = Spawner()
    _tab(line, spawner)
    for key, inputs in spawner.asked:
        tree_mod.refresh_one(_app(), key, inputs)
    return _tab(line)


# --- the three spellings ----------------------------------------------------------------------------------------------


class TestTheValueOfAConfigKey:
    def test_nothing_is_offered_before_the_refresh_which_the_tab_starts(self, config: Path) -> None:
        spawner = Spawner()
        assert _tab(f"valuedemo run {config} name=", spawner) == []
        key = f"{config_values.KEY_PREFIX}{HERE}.Catalog.name"
        assert spawner.asked == [(key, {"kind": "small", "local": "false", "count": "0"})]
        assert ASKED == []  # TAB never runs a provider

    def test_a_bare_key_completes_its_values_kept_as_key_value(self, config: Path) -> None:
        assert _tab_after_refresh(f"valuedemo run {config} name=") == [
            "name=small-one",
            "name=small-two",
            "name=with space",
        ]
        assert _tab(f"valuedemo run {config} name=small-t") == ["name=small-two"]

    def test_the_flag_spelling_completes_the_word_after_it(self, config: Path) -> None:
        assert _tab_after_refresh(f"valuedemo run {config} --name ") == ["small-one", "small-two", "with space"]
        assert _tab(f"valuedemo run {config} --name w") == ["with space"]

    def test_the_flag_equals_spelling_keeps_its_flag(self, config: Path) -> None:
        assert _tab_after_refresh(f"valuedemo run {config} --name=small-o") == ["--name=small-one"]

    def test_a_cached_list_is_read_without_a_new_refresh_until_it_is_old(self, config: Path) -> None:
        _tab_after_refresh(f"valuedemo run {config} name=")
        spawner = Spawner()
        assert _tab(f"valuedemo run {config} name=", spawner) != []
        assert spawner.asked == []
        assert _tab(f"valuedemo run {config} name=", spawner, force=True) != []  # a second TAB asks again
        assert len(spawner.asked) == 1


# --- the settings the provider gets -----------------------------------------------------------------------------------


class TestTheSettingsTheProviderGets:
    def test_the_line_wins_over_the_file(self, config: Path) -> None:
        assert _tab_after_refresh(f"valuedemo run {config} kind=huge name=")[:1] == ["name=huge-one"]
        assert _tab_after_refresh(f"valuedemo run {config} --kind tiny --name ")[:1] == ["tiny-one"]

    def test_a_class_default_stands_in_for_a_setting_nobody_writes(self, tmp_path: Path) -> None:
        config = _config(tmp_path, f"runnable: !class:{HERE}.Runner\n  source: !class:{HERE}.Catalog {{}}\n")
        assert _tab_after_refresh(f"valuedemo run {config} name=")[:1] == ["name=big-one"]

    def test_a_switch_typed_alone_is_true(self, config: Path) -> None:
        assert _tab_after_refresh(f"valuedemo run {config} --local name=")[-1] == "name=local-only"
        assert _tab_after_refresh(f"valuedemo run {config} local=yes name=")[-1] == "name=local-only"
        assert "name=local-only" not in _tab_after_refresh(f"valuedemo run {config} --local no name=")

    def test_a_setting_the_block_writes_is_read_from_the_block(self, tmp_path: Path) -> None:
        config = _config(
            tmp_path, f"runnable: !class:{HERE}.Runner\n  source: !class:{HERE}.Catalog {{kind: tiny, count: 3}}\n"
        )
        assert _tab_after_refresh(f"valuedemo run {config} name=")[:1] == ["name=tiny-one"]
        assert ASKED[-1] == {"kind": "tiny", "local": "false", "count": "3"}

    def test_a_setting_fed_by_a_reference_is_read_from_the_key_it_names(self, tmp_path: Path) -> None:
        config = _config(
            tmp_path,
            f"""\
            flavour: sweet
            runnable: !class:{HERE}.Runner
              source: !class:{HERE}.Catalog
                kind: !ref:flavour
            """,
        )
        assert _tab_after_refresh(f"valuedemo run {config} name=")[:1] == ["name=sweet-one"]
        assert _tab_after_refresh(f"valuedemo run {config} flavour=sour name=")[:1] == ["name=sour-one"]


# --- which key addresses the block ------------------------------------------------------------------------------------


class TestWhichKeyAddressesTheBlock:
    def test_a_key_feeding_the_parameter_through_a_reference_completes_its_values(self, tmp_path: Path) -> None:
        config = _config(
            tmp_path,
            f"""\
            title: ""
            runnable: !class:{HERE}.Runner
              source: !class:{HERE}.Catalog
                name: !ref:title
            """,
        )
        assert _tab_after_refresh(f"valuedemo run {config} title=")[:1] == ["title=big-one"]

    def test_a_dotted_key_names_the_block_by_its_path(self, tmp_path: Path) -> None:
        config = _config(tmp_path, f"runnable: !class:{HERE}.Runner\n  source: !class:{HERE}.Catalog {{}}\n")
        assert _tab_after_refresh(f"valuedemo run {config} runnable.source.name=")[:1] == [
            "runnable.source.name=big-one"
        ]
        assert _tab_after_refresh(f"valuedemo run {config} runnable.other.name=") == []

    def test_a_key_without_a_provider_completes_as_before(self, config: Path) -> None:
        assert _tab_after_refresh(f"valuedemo run {config} count=") == []
        assert _tab_after_refresh(f"valuedemo run {config} --count ") == []

    def test_a_provider_that_fails_offers_nothing_and_writes_no_cache(self, config: Path) -> None:
        assert _tab_after_refresh(f"valuedemo run {config} kind=") == []
        key = f"{config_values.KEY_PREFIX}{HERE}.Catalog.kind"
        assert tree_mod.refresh_one(_app(), key, {}) is None

    def test_a_refresh_for_a_parameter_no_package_registers_writes_nothing(self) -> None:
        assert tree_mod.refresh_one(_app(), f"{config_values.KEY_PREFIX}{HERE}.Catalog.count", {}) is None

    def test_without_a_config_on_the_line_a_key_completes_nothing(self) -> None:
        assert _tab("valuedemo run name=", Spawner()) == []


# --- the shells -------------------------------------------------------------------------------------------------------


class TestTheShells:
    def test_bash_is_handed_the_part_after_the_last_equals_sign(self) -> None:
        """bash splits a word at ``=`` (``$COMP_WORDBREAKS``) and replaces only what follows it."""
        assert bash_word_tail(["name=a", "name=b c"], "name=") == ["a", "b c"]
        assert bash_word_tail(["--name=a"], "--name=x") == ["a"]
        assert bash_word_tail(["alpha", "beta"], "al") == ["alpha", "beta"]  # no equals sign: as they are

    def test_liquifai_complete_prints_the_tail_to_bash_and_the_whole_word_to_zsh(
        self, config: Path, monkeypatch: pytest.MonkeyPatch, capsys: Any
    ) -> None:
        app = _app()
        tree_mod.write_cache(app)
        _tab_after_refresh(f"valuedemo run {config} name=")
        monkeypatch.setattr("sys.argv", ["liquifai-complete", "valuedemo"])
        line = f"valuedemo run {config} name=small-"
        monkeypatch.setenv("COMP_LINE", line)
        monkeypatch.setenv("COMP_POINT", str(len(line)))
        _fast_complete.main()
        assert capsys.readouterr().out.splitlines() == ["small-one", "small-two"]
        monkeypatch.delenv("COMP_LINE")
        monkeypatch.setenv("COMP_WORDS", "\n".join(line.split(" ")))
        monkeypatch.setenv("COMP_CWORD", "3")
        _fast_complete.main()
        assert capsys.readouterr().out.splitlines() == ["name=small-one", "name=small-two"]

    def test_the_cache_is_one_list_per_settings(self, config: Path) -> None:
        _tab_after_refresh(f"valuedemo run {config} name=")
        _tab_after_refresh(f"valuedemo run {config} kind=huge name=")
        key = f"{config_values.KEY_PREFIX}{HERE}.Catalog.name"
        small = {"kind": "small", "local": "false", "count": "0"}
        assert cache.read_dependent_value_cache("valuedemo", key, small) == ["small-one", "small-two", "with space"]
        assert cache.read_dependent_value_cache("valuedemo", key, {**small, "kind": "huge"}) == [
            "huge-one",
            "huge-two",
            "with space",
        ]
