# Constrained tool execution

The gateway ships a **bounded tool runner**, not a hardened arbitrary-code sandbox.

## Guarantees

- Only allowlisted tools registered in the process tool registry may execute.
- Agent definitions further restrict which tool names are permitted per run.
- Typed JSON-ish argument payloads are size-limited.
- Prompt-injection style payloads containing `eval` / `exec` / `subprocess` / shell paths are rejected.
- Per-tool timeouts and output size limits apply.
- Recursive tool-call depth is capped.
- Tools marked `requires_confirmation` need an explicit confirmation flag on the run.
- Tool credentials are never returned to the model.
- Outputs are screened by guardrails before re-entering the transcript.
- Built-in tools (`current_time`, `echo`, `calculator`) perform no filesystem, network, or subprocess access.

## Non-guarantees

- Not a general-purpose code sandbox.
- Not a multi-tenant isolation boundary for untrusted third-party tool plugins.
- Not a substitute for OS-level containers/seccomp for arbitrary tooling.

Independent security review is required before exposing additional tools in production.
