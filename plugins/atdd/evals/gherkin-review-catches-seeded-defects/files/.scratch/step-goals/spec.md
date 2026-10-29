# Spec: Daily step goal celebrations (FIT-31)

## Problem statement
Members set themselves a daily step target, but the app never acknowledges it when they hit it. Members told
us a small celebration on the day would keep them going.

## Solution
A member can set a daily step goal. When the member's daily steps reach the goal, the app shows the member
one celebration for that day.

## User stories
1. As a member, I want to set a daily step goal, so that I have something to aim for.
2. As a member, I want to be celebrated when I reach my goal, so that I stay motivated.
3. As a member, I want to pause celebrations without losing my goal, so that I can take a break while injured.

## Rules
- The step goal is a whole number of steps from 1,000 to 50,000. Any other value is rejected with the message
  "Choose a goal between 1,000 and 50,000 steps", and the previous goal stays in place.
- "Reaching the goal" means daily steps at least equal to the goal. Daily steps exactly equal to the goal count
  as reached.
- A calendar day runs from midnight to midnight in the member's time zone (see ADR-0002).
- A member's daily steps are the sum of the step counts synced for that calendar day.
- A member gets at most one celebration per calendar day, however many more steps they take.
- Pausing celebrations stops them immediately. The goal is kept; resuming celebrations uses the same goal.
- A step count that is synced late (after its calendar day has ended) still counts toward the day it belongs
  to, but never triggers a celebration for that past day.

## Out of scope
- Sharing achievements with friends.
- Weekly or monthly goals.
- Celebration styles; celebrations are in-app messages only.

## Implementation decisions
- Celebrations are produced by the existing step-sync pipeline.
