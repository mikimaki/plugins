---
type: llm
focus: trace
weight: 1
---

The celebration scenarios are bound without patching the app's own modules (stepgoals.celebrations, stepgoals.push_gateway or stepgoals.app); the push message is faked at the network boundary (for example urllib.request.urlopen), and its text is asserted.
