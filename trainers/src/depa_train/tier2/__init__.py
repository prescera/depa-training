"""Tier 2: graph-only models in the ``depa-graph/v1`` format.

torch.export's own save/load format is not used for untrusted artifacts: its loader retries a failed
weights-only tensor load with ``weights_only=False`` (arbitrary unpickling), and deserializes symbolic
shapes with ``sympy.sympify`` (which evaluates strings). Instead a graph is a
directory with:

* ``graph.json`` - core-ATen op graph (JSON): inputs, parameter/buffer placeholders, call nodes whose
  targets are op names checked against an allowlist, literal arguments, outputs;
* ``weights.safetensors`` - parameters, buffers and constants keyed by their module path.

Loading parses JSON and safetensors only; nodes are rebuilt into a ``torch.fx`` graph whose call
targets are resolved exclusively through the allowlist. ``export.export_graph`` (run by the TDC on
its own machine) produces the format from any ``nn.Module`` via ``torch.export``.
"""

GRAPH_FORMAT = "depa-graph/v1"
