"""Which model, and how much effort, a task deserves (ADR-025).

EOS advises and the harness executes: nothing in this package calls a
provider. It classifies a task, scores its complexity, and picks the cheapest
registered model that meets the level's requirement, at an effort that model
accepts. Every surface -- `eos route`, the brief's ROUTE line, the MCP tool,
the optional subagent hook -- calls `route()` and renders what it returns.
"""
