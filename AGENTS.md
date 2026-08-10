# Agent guidance

This project was built with the microsoft-foundry skill. Before working on or answering questions about Foundry agents, read the microsoft-foundry skill first.

- Preserve the explicit `simulation` and `live` runtime modes.
- Never add a live-to-simulation fallback.
- Keep GenAI message content recording disabled unless `LLMOPS_CAPTURE_CONTENT=true` is explicitly set.
- Use `ManagedIdentityCredential` in Azure and `DefaultAzureCredential` only for local development.
- Keep Azure SRE Agent in read-only mode and never use automatic approval.
- Treat Azure Copilot Observability Agent output as recommendations; every environment change requires human approval.
- Never replace missing live SRE evidence or an uncreated Azure Monitor issue with simulated success.
- Do not run `azd provision`, `azd deploy`, `azd up`, or destructive Azure commands without explicit approval.
