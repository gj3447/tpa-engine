"""SCIP symbol-grammar tokenizer tests on known symbol strings.

These need only protobuf-free pure-python parsing (``decode_descriptors`` /
``parse_symbol``), so they run without scip-python or an index file.
"""

from __future__ import annotations

import pytest

from codegraph.scip_backend import decode_descriptors, parse_symbol

STRIP = ("src",)


def test_module_init():
    info = decode_descriptors("`src.ooptdd.backends.base`/__init__:", STRIP)
    assert info["kind"] == "module"
    assert info["qualified_name"] == "ooptdd.backends.base"
    assert info["name"] == "base"


def test_class_type_suffix():
    info = decode_descriptors("`src.ooptdd.backends.base`/QueryResult#", STRIP)
    assert info["kind"] == "class"
    assert info["qualified_name"] == "ooptdd.backends.base.QueryResult"
    assert info["name"] == "QueryResult"


def test_method_suffix_is_method_true():
    info = decode_descriptors("`src.ooptdd.backends.base`/Backend#ship().", STRIP)
    assert info["kind"] == "function"
    assert info["qualified_name"] == "ooptdd.backends.base.Backend.ship"
    assert info["name"] == "ship"
    assert info["is_method"] is True


def test_module_level_function_not_method():
    info = decode_descriptors("`src.ooptdd.gate`/evaluate().", STRIP)
    assert info["kind"] == "function"
    assert info["qualified_name"] == "ooptdd.gate.evaluate"
    assert info["is_method"] is False


def test_term_field_in_class():
    info = decode_descriptors("`src.ooptdd.backends.base`/QueryResult#events.", STRIP)
    assert info["kind"] == "term"
    assert info["qualified_name"] == "ooptdd.backends.base.QueryResult.events"


def test_strip_prefix_optional():
    # without strip, the 'src.' stays in the module path
    info = decode_descriptors("`ooptdd.gate`/evaluate().", ())
    assert info["qualified_name"] == "ooptdd.gate.evaluate"


@pytest.mark.parametrize("sym", [
    "local 42",
    "scip-python python python-stdlib 3.13 `os`/getcwd().",  # other package
    "not a scip symbol",
])
def test_parse_symbol_rejects_non_repo(sym):
    assert parse_symbol(sym, own_packages={"ooptdd"}, strip_prefixes=STRIP) is None


def test_parse_symbol_full_repo_method():
    sym = "scip-python python ooptdd 0.2.0 `src.ooptdd.backends.base`/Backend#ship()."
    info = parse_symbol(sym, own_packages={"ooptdd"}, strip_prefixes=STRIP)
    assert info["qualified_name"] == "ooptdd.backends.base.Backend.ship"
    assert info["kind"] == "function"


def test_parse_symbol_no_package_filter_models_all():
    sym = "scip-python python anything 1 `pkg.mod`/foo()."
    info = parse_symbol(sym, own_packages=None, strip_prefixes=())
    assert info["qualified_name"] == "pkg.mod.foo"
