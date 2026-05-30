"""Verify the Platform Core / product layer manifest stays consistent
with the actual `execution/ops_platform/` module set.

This is a boundary-clarity test, not a runtime test. If a new module
appears in `ops_platform/` without being assigned a layer, this test
fails — forcing the author to declare its layer.
"""

from pathlib import Path

from execution.ops_platform.__layers__ import (
    all_known_modules,
    layer_of,
)


def test_every_ops_platform_module_has_a_declared_layer():
    """Any .py file in ops_platform/ should be assigned to a layer
    (or explicitly excluded below)."""

    ops_dir = Path(__file__).resolve().parents[1] / "execution" / "ops_platform"
    found: set[str] = set()
    for p in ops_dir.glob("*.py"):
        name = p.stem
        if name.startswith("_"):
            continue
        found.add(name)

    declared = all_known_modules()
    missing = found - declared
    assert not missing, (
        "These modules in execution/ops_platform/ are not assigned to a layer "
        "in execution/ops_platform/__layers__.py. Add them to PLATFORM_CORE / "
        "OPS_PRODUCT / ARCHITECT_PRODUCT / LIBRARY_PRODUCT / SHARED_UTIL.\n"
        f"  {sorted(missing)}"
    )


def test_no_phantom_modules_in_manifest():
    """Manifest must not reference modules that don't exist on disk."""

    ops_dir = Path(__file__).resolve().parents[1] / "execution" / "ops_platform"
    on_disk = {p.stem for p in ops_dir.glob("*.py") if not p.stem.startswith("_")}

    declared = all_known_modules()
    phantom = declared - on_disk
    assert not phantom, (
        "These modules are declared in __layers__.py but don't exist:\n"
        f"  {sorted(phantom)}"
    )


def test_known_modules_route_to_expected_layers():
    """Spot-check a few well-known assignments."""
    assert layer_of("event_fabric") == "platform_core"
    assert layer_of("transactional_outbox") == "platform_core"
    assert layer_of("incidents") == "ops_product"
    assert layer_of("builder") == "architect_product"
    assert layer_of("marketplace") == "library_product"
