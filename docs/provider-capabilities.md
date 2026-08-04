# Provider capability matrix

Status values are based on **runtime-verified** adapter behaviour against live HTTP
(fictional providers A/B) or unit/contract tests—not interface presence alone.

| Capability | Echo | OpenAI adapter | Anthropic adapter | Azure OpenAI | Google | Bedrock | Provider A (fictional) | Provider B (fictional) |
|---|---|---|---|---|---|---|---|---|
| Chat completion | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED |
| Streaming (SSE/chunks) | IMPLEMENTED | IMPLEMENTED | PARTIAL | PARTIAL | PARTIAL | PARTIAL | IMPLEMENTED | PARTIAL |
| Embeddings | NOT IMPLEMENTED | IMPLEMENTED | NOT IMPLEMENTED | PARTIAL | PARTIAL | NOT IMPLEMENTED | IMPLEMENTED | NOT IMPLEMENTED |
| Tool / function calling | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | IMPLEMENTED | PARTIAL |
| Structured output | NOT IMPLEMENTED | PARTIAL | PARTIAL | PARTIAL | PARTIAL | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED |
| Multimodal input | NOT IMPLEMENTED | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | NOT IMPLEMENTED | NOT IMPLEMENTED |
| Moderation | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | PARTIAL (safety refusal scenario) | PARTIAL |
| Batch / async provider jobs | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED |
| Token usage reporting | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED |
| Native idempotency | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED | NOT IMPLEMENTED |
| Timeout behaviour | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED | IMPLEMENTED (scenario) | IMPLEMENTED (scenario) |
| Rate-limit headers / 429 | NOT IMPLEMENTED | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | IMPLEMENTED | IMPLEMENTED |
| Retry-After support | NOT IMPLEMENTED | PARTIAL | PARTIAL | PARTIAL | PARTIAL | PARTIAL | IMPLEMENTED | IMPLEMENTED |

## Notes

- Gateway-level retries, circuit breakers, and fallback are IMPLEMENTED in application services and verified in unit/resilience tests; Compose e2e covers live chat/stream/embeddings against A/B.
- Agent orchestration and tool execution are **bounded** (step/timeout limits) and should be treated as production-inspired, not unbounded autonomous agents.
- Provider A speaks OpenAI-compatible `/v1/chat/completions` + embeddings; Provider B speaks Anthropic-style `/v1/messages` with intentional schema/auth differences.
