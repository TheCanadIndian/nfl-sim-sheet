"""
Settings adopted by the self-tuning job (learn.py), applied on top of the code defaults.

    import learned; learned.apply("nfl")      # in project.py, before anything is fit
    import learned; learned.apply("nhl")      # in hockey/project.py

model_params.json holds {"nfl": {"boxscore.priors.K_TGT": 25, ...}, "nhl": {"model.K_FIN_XG": 50, ...}}.
Delete an entry (or the file) to go back to the default in the code.
"""

import importlib
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))
PARAMS = os.path.join(HERE, "model_params.json")


def load():
    return json.load(open(PARAMS)) if os.path.exists(PARAMS) else {}


def save(params):
    with open(PARAMS, "w") as f:
        json.dump(params, f, indent=2, sort_keys=True)


def apply(sport):
    """Set every learned value for `sport` on its module. Returns what was applied."""
    out = {}
    for key, val in load().get(sport, {}).items():
        mod, attr = key.rsplit(".", 1)
        m = importlib.import_module(mod)
        if hasattr(m, attr):
            setattr(m, attr, val)
            out[key] = val
    return out
