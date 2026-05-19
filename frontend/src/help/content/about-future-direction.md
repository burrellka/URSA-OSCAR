# Future direction

URSA-OSCAR 1.0 closed a substantial body of work, but the project isn't done. This page describes what's currently anticipated and where the boundary lines are.

## What's coming in the near term

These items are sized, scoped, or actively being worked on:

- **Phase 8 — Community engagement.** Polishing the public-facing surface so the GitHub repo is approachable for new readers: README sweep, issue templates, contribution guidelines, example use cases. Currently being framed by the architect.
- **Token cost surfacing for AI usage.** The Anthropic prompt caching shipped in 0.13.6 produces `cache_creation_input_tokens` and `cache_read_input_tokens` per request, but the operator can't see them in the UI yet. A future patch will surface running cost estimates per provider, with a "compare actual vs. cached cost" comparison so the value of caching is visible.
- **Conversation export.** AI chat history lives in browser localStorage today. A future patch will let you export a conversation as Markdown or PDF for sharing with your sleep medicine provider.
- **Mobile-narrow polish.** The UI works on phones but feels cramped, especially the Reports page. A pass to clean up the < 480px layout is on the list.

## What's on the shelf but not scheduled

These are designs the maintainer has thought through but hasn't committed to building:

- **Audit log persistence.** Login attempts and password changes go to stdout today. Persisting them in DuckDB would enable an in-UI audit view for operators with regulated environments. Low priority because the single-tenant trust model doesn't really need it.
- **Multi-language UI.** All strings, prompts, and PDF templates are English-only. Internationalization is a meaningful engineering project; would only happen with significant community pull.
- **Recall / memory layer for the AI assistant.** A persistent, operator-scoped store of "things the AI has learned about you across conversations" — your provider's name, your dose schedule, your treatment goals. Designed but not built. Likely a Phase 9+ project.

## What's deliberately out of scope (and likely stays that way)

These are decisions the maintainer has actively chosen NOT to do:

- **Multi-tenant / multi-user.** URSA-OSCAR 1.0 is single-tenant by architecture: one operator per deployment, one DuckDB per deployment, one set of secrets per deployment. Making it multi-tenant means redesigning the trust boundary, isolating data per tenant, plumbing user IDs through every query, adding role-based access. That's a different product. If your household has two CPAP users, the current answer is "run two URSA-OSCAR instances on different ports." See the Multi-instance page in Architecture and Deployment.
- **Cloud-hosted SaaS.** URSA-OSCAR is self-hosted. There's no plan to operate a hosted instance for users. The trust model — your data on your hardware, no third-party access — is the point.
- **Direct prescription / pressure adjustment integration.** URSA-OSCAR shows you your data and helps you talk to your provider. It does not write to your CPAP, recommend pressure changes, or interface with your provider's EMR.
- **Medical device certification.** URSA-OSCAR is a hobbyist / research tool. Pursuing FDA / CE / TGA medical-device approval would require a different organizational structure, a different testing regime, and a different liability posture. That's not the project this is.

## How to influence direction

URSA-OSCAR is open source under GPL-3.0. The most reliable ways to influence what gets built:

1. **File a GitHub issue** with a specific use case and the data shape it'd require. Vague feature requests sit; concrete ones get sized.
2. **Submit a pull request.** A working implementation, even rough, is the highest-bandwidth way to propose new functionality.
3. **Fork it.** The GPL guarantees you can run your own divergent version on your own hardware indefinitely. If your needs are far from the project's direction, this is the right answer.

## Project boundaries and maintainer time

URSA-OSCAR is maintained as a side project by one person. Expectations:

- Issues will be triaged, but not all will be closed.
- Pull requests will be reviewed, but reviews can take weeks.
- Security issues get fastest attention; everything else competes for time with the maintainer's actual job and life.
- This is a labor-of-love project, not a commercial product or a community-driven foundation. Plan accordingly.
