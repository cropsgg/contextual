# Taste (Continuously Learned by [CommandCode][cmd])

[cmd]: https://commandcode.ai/

# UI/UX Design
- Apply Claude Code styling to all UI components. Use only client/tailwind.config.ts, client/app/globals.css, and client/app/layout.tsx. Replace indigo-* hovers with accent tokens. Do not change layout, routes, or behavior when restyling. Confidence: 0.95
- When redesigning UI, maintain exact functionality and button placement - only change visual appearance. Confidence: 0.85

# Workflow Patterns
- Run /self-audit-and-validation after all code implementation, API changes, database changes, or cross-module integrations. Assume the system is flawed and check for production failure modes. Confidence: 0.90
- When user provides an implementation plan, follow it exactly without editing the plan file. Mark to-dos as in_progress starting from the first one. Do not recreate to-dos. Confidence: 0.90
- Provide brief task result summaries. Do not explicitly state "no follow-ups needed" when there are none. Confidence: 0.80

# Testing & QA
- Use browser-based QA with multiple test accounts (admin, User A, User B) for comprehensive testing. Execute all checklist items and mark PASS/PARTIAL/FAIL/BLOCKED. Confidence: 0.75

# Architecture Preferences
- Implement token quota systems with admin (infinite) and normal user (daily limits) tiers. Allow admin to override quotas. Confidence: 0.70
