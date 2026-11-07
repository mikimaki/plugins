@issue-a02-celebrations
Feature: Reaching the daily goal

  Background:
    Given Robin is a Stride member

  @R1
  Scenario: Reaching the daily goal exactly is celebrated
    Given Robin has a daily step goal of 8000 steps
    When Robin's steps for today sync as 8000
    Then Robin gets the push message "Goal reached: 8,000 steps!"

  @R1
  Scenario: Passing the daily goal is celebrated with the synced steps
    Given Robin has a daily step goal of 8000 steps
    When Robin's steps for today sync as 12345
    Then Robin gets the push message "Goal reached: 12,345 steps!"
