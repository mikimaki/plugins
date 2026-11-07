@issue-a02-celebrations
Feature: Steps below the daily goal

  Background:
    Given Robin is a Stride member

  @R2
  Scenario: Steps below the goal aren't celebrated
    Given Robin has a daily step goal of 8000 steps
    When Robin's steps for today sync as 7999
    Then Robin gets no push message
