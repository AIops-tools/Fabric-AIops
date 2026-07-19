# Agent guardrails — running fabric-aiops with a smaller / local model

If you drive these tools with a local model (Llama, Qwen, Mistral … via Goose,
Ollama, LM Studio, or any OpenAI-compatible runtime), you will get noticeably
better results with a short system prompt. This page gives you one, and — more
importantly — tells you which guardrails you **no longer need to write**, because
the tool now enforces them itself.

The distinction matters. A guardrail in a prompt is a request. A guardrail in the
harness is a guarantee. Anything below that we could move into the harness, we did.

## What the tool now enforces — do not waste prompt budget on these

| You might be tempted to prompt | Why you don't need to |
|---|---|
| "Work read-only, never modify anything" | Set `FABRIC_READ_ONLY=1`. Write tools are then **not registered at all** — they never appear in the tool list, so the model cannot call one even if it tries. The `@governed_tool` harness independently refuses writes, so the CLI is covered too. |
| "Don't invent a value when a field is missing" | A field the controller did not return comes back as `null`, never as `""`. Absent and empty are distinguishable in the payload. |
| "Tell me if the output was cut off" | Anything with a `limit` returns `{"devices": [...], "returned": N, "limit": L, "truncated": true/false}`. Truncation is measured against the full result, not guessed from a length coincidence. |
| "Preserve the ordering / tell me what's most urgent" | The ranked analyses return worst-first and carry the numbers that produced each ranking (`avgLossPct`, `score`, `alertPenalty`), so priority is in the payload rather than implied by list position. |
| "Confirm before anything destructive" | Destructive operations require a `--dry-run`-able preview + double confirmation at the CLI, and a named approver (`FABRIC_AUDIT_APPROVED_BY`) for high-risk tiers. |
| "Log what you did" | Every call is audited to `~/.fabric-aiops/audit.db` regardless of what the model says it did. |
| "Don't guess at an unsupported platform" | An operation a platform does not map raises a teaching `PlatformUnsupported` error naming the platform — never a silent no-op the model can mistake for success. |

## What still needs a prompt

These are model-behaviour problems the harness cannot fix from the outside.
Copy this into your agent's system prompt:

```text
You operate a network fabric through its CONTROLLER API using the fabric-aiops
MCP tools (Cisco Meraki Dashboard, Cisco Catalyst Center, Arista CloudVision
Portal, or UniFi Network, depending on the configured target).

TOOL USE
- Before answering any question about the current fabric, you MUST call a tool.
  Never answer from memory or assumption.
- Actually invoke the tool. Do not describe the call you would make, and do not
  emit an example JSON response in place of calling it.
- If a tool call fails, report the real error verbatim. Never fill the gap with
  a plausible-sounding answer.
- If a tool returns "not supported on <platform> yet", say so. Do not substitute
  a different tool and present its output as the answer to the original question.

READING RESULTS
- Read the whole result before concluding. If a result has "truncated": true,
  say so and re-run with a higher limit instead of treating the partial result
  as complete.
- A null field means the controller did not return that value. Report it as
  "not available" — never infer it.
- Report values exactly as returned. Do not normalise, translate, or prettify
  status strings, severities, model names, or identifiers.
- In the ranked analyses, work worst-first and cite the measured number
  (avgLossPct, avgLatencyMs, score, alertPenalty) behind each ranking.

IDENTIFIERS — keep these straight, they are not interchangeable
- An ORGANIZATION id scopes the whole account (on Catalyst Center, CVP, and
  UniFi a site/container stands in for it).
- A NETWORK id names one site/branch inside an organization.
- A DEVICE SERIAL names one physical device. It is not a device name, not a MAC,
  and not a network id.
- An UPLINK names a WAN interface on one appliance (e.g. wan1/wan2), scoped to
  that device's serial — an uplink is never addressed on its own.
- Never pass an organization id where a network id is expected, or a device name
  where a serial is expected. If you do not have the right id, call the list
  tool that returns it (org_list, network_list, device_inventory) first.

SCOPE
- Separate observation from interpretation. State what the tools returned, then
  any interpretation, clearly marked as such.
- Do not assert a connectivity, capacity, or availability problem unless a tool
  result supports it.
- Do not add generic advice that does not follow from the tool output.
```

## Recommended setup for a local model

```bash
# Read-only until you trust the setup — this is enforced, not advisory.
export FABRIC_READ_ONLY=1
fabric-aiops doctor
```

With `FABRIC_READ_ONLY=1` set, the eight write tools (`reboot_device`,
`blink_device_leds`, `update_device`, `update_network_vlan`,
`claim_devices_into_network`, `remove_device_from_network`,
`bind_network_to_template`, `unbind_network_from_template`) plus `undo_apply`
are not registered, leaving only the reads and the three analyses.

Then, when you are ready to allow writes, unset it and set an approver so the
high-risk tier has an accountable name on it:

```bash
unset FABRIC_READ_ONLY
export FABRIC_AUDIT_APPROVED_BY="your.name@example.com"
export FABRIC_AUDIT_RATIONALE="scheduled maintenance window 2026-07-20"
```

## If your model still struggles

Some behaviours are model-capacity limits rather than prompt problems:

- **Multi-tool workflows time out or drift.** Prefer `overview` and the three
  flagship analyses (`uplink_loss_and_latency_rca`, `network_health_score`,
  `config_template_drift`) — they do the multi-step correlation inside one call,
  so the model does not have to chain reads and keep org/network/serial ids
  straight across turns.
- **The model ignores later tool results in a long context.** Ask narrower
  questions and use `--limit` deliberately rather than pulling a whole org
  inventory in one go.
- **The model confuses sites with networks on Catalyst Center / CVP / UniFi.**
  On those platforms a site or container stands in for both the organization and
  the network level. Tell the model which one your target is scoped to.
- **The model describes calls instead of making them.** This is usually a
  runtime/tool-calling-format mismatch, not a prompt problem — check that your
  client advertises the tools in the format your model was trained on.

Feedback on running this with a specific local model is genuinely useful —
open an issue at
[github.com/AIops-tools/Fabric-AIops](https://github.com/AIops-tools/Fabric-AIops/issues)
with the model, runtime, and what went wrong.
