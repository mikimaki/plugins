# fit-12: Daily step goals (existing behavior)

Source: .atdd/fit-12/spec.md

## Requirements

- **R1** A member can set a daily step goal between 1,000 and 50,000 steps, inclusive, and is told "Your daily step goal is <goal> steps". (spec: "Valid goals are whole numbers of steps between 1,000 and 50,000, inclusive"; "When the goal is accepted, the member is told …")
- **R2** A goal outside that range is refused with "Enter a whole number of steps between 1,000 and 50,000", and the member's goal doesn't change. (spec: "When the goal is outside the range …")
- **R3** When a member's synced steps for a day reach their daily step goal, the member gets the push message "Goal reached: <steps> steps!". (spec: "When a member's steps for a day sync and reach their daily step goal …")

## Out of scope

- Weekly totals.

## Open questions
