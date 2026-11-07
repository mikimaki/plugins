# Requirements: a01-goals A01. Daily step goals

Source: docs/specs/a01-goals.md

## Requirements

- **R1**: A member can set a daily step goal between 1,000 and 50,000 steps, inclusive, with or without thousands separators, and is told "Your daily step goal is <goal> steps" with the goal written with thousands separators.
  > Spec: "Valid goals are whole numbers of steps between 1,000 and 50,000, inclusive; thousands separators are allowed"
- **R2**: A goal outside that range is refused with "Goal must be between 1000 and 50000 steps", and the member's goal doesn't change.
  > Spec: "When the goal is outside the range, the member is told "Goal must be between 1000 and 50000 steps" and their goal doesn't change."

## Out of scope

- Weekly totals.

## Open questions

- None
