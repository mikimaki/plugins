@issue-fit-12
Feature: Step sync
  Members see their daily steps as soon as their tracker syncs.

  @R1
  Scenario: A synced step count adds to the member's daily steps
    Given the member "Ana" in time zone "Australia/Sydney"
    And Ana has 4200 daily steps on 2026-05-04
    When a step count of 800 steps for 2026-05-04 10:00 is synced
    Then Ana's daily steps for 2026-05-04 are 5000
