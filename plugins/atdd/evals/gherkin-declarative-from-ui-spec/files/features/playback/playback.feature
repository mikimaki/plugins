@issue-pod-4
Feature: Playback
  Listeners pick up where they left off.

  @R1
  Scenario: Playback resumes at the saved position
    Given the listener "Mei" stopped "Episode 12: Tides" at 14:05
    When Mei plays "Episode 12: Tides" again
    Then playback starts at 14:05
