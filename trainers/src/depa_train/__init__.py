"""depa-train: the DEPA Training v2 training runtime.

Untrusted training input is reduced to something that can be checked automatically:

* tier 1 - declarative recipes executed by vetted, versioned plugins;
* tier 2 - graph-only models (ATen op graphs + safetensors weights) under an op allowlist;
* tier 3 - an approved custom container that honours the same interface and output contract.
"""

__version__ = "2.0.0"
RECIPE_SCHEMA_ID = "depa-training/recipe/v1"
