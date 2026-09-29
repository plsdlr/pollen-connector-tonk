#!/usr/bin/env python3
"""Run the garden from a plain checkout: `python3 pollen.py <command>`.

Nothing to install -- the package is standard library only. `pollen enable`
run through this file registers hooks that call it the same way, by absolute
path, so the checkout must stay where it is.
"""
import os
import sys

HERE = os.path.dirname(os.path.realpath(__file__))
sys.path.insert(0, os.path.join(HERE, "src"))
os.environ["TONK_POLLEN_LAUNCHER"] = os.path.join(HERE, "pollen.py")

from tonk_pollen.cli import main  # noqa: E402

sys.exit(main())
