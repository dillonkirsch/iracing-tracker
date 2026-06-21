import pytest

from irtracker import recipes
from irtracker.semdiff import parse_ini

INI = "[Graphics]\nFieldOfView=90\t; fov\nmirrorQuality=2\n\n[Audio]\nvol=80\n"


def test_patch_ini_edits_adds_and_preserves():
    out = recipes.patch_ini_text(INI, {
        ("Graphics", "FieldOfView"): "100",   # change existing (has a comment)
        ("Graphics", "shadows"): "high",       # add to an existing section
        ("VR", "hmd"): "Quest",                # add a brand-new section
    })
    p = parse_ini(out)
    assert p["Graphics"]["FieldOfView"] == "100"
    assert "; fov" in out                                   # inline comment kept
    assert p["Graphics"]["shadows"] == "high"
    assert p["Graphics"]["mirrorQuality"] == "2"            # untouched
    assert p["Audio"]["vol"] == "80"                        # untouched
    assert p["VR"]["hmd"] == "Quest"


def test_recipe_build_parse_and_changes():
    r = recipes.build_recipe("VR", "app.ini", parse_ini(INI), ["Graphics"])
    assert r["kind"] == "irtracker-recipe" and len(r["values"]) == 2
    r2 = recipes.parse_recipe(recipes.recipe_json(r))
    assert r2["file"] == "app.ini"
    changes = recipes.recipe_changes(r2, parse_ini("[Graphics]\nFieldOfView=80\nmirrorQuality=2\n"))
    assert changes == [{"section": "Graphics", "key": "FieldOfView", "old": "80", "new": "90"}]


def test_parse_recipe_rejects_junk():
    with pytest.raises(ValueError):
        recipes.parse_recipe("not json at all")
    with pytest.raises(ValueError):
        recipes.parse_recipe('{"hello": 1}')  # valid json, not a recipe
