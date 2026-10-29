# Spec: Sleep timer panel (POD-22)

## Problem statement
Listeners fall asleep to episodes and wake up hours later with their place lost. They want playback to stop
on its own.

## Solution
A "Sleep timer" panel in the player.

## How it works
- The listener taps "Sleep timer", types a number of minutes into the "Minutes" field and taps "Start".
- If the field is empty when they tap "Start", the panel shows "Enter a number of minutes" under the field and
  no timer starts.
- If the value is not a whole number from 1 to 180, the panel shows "Choose between 1 and 180 minutes" and no
  timer starts.
- After a successful start the player bar reads "Sleep timer · <minutes> min", e.g. "Sleep timer · 45 min".
- Tapping "Cancel timer" stops the timer and the player bar reads "Sleep timer off". The duration is
  remembered: the next time the listener taps "Sleep timer", the field is pre-filled with it.

## Out of scope
- What happens when the timer runs out (POD-23).
