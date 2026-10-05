# HEART
Role: filter — vet every proposed action so it cannot harm the user or others.

- policies/ : written rules the system must follow (legality, no spam, no financial advice claims, no unsafe content)
- review/   : pending action queue waiting for heart approval before motar executes

Implementation: hard-coded rules (regex/PII/keyword/risk limits) + one LLM safety pass. Never let brain skip heart.
