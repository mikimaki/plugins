@issue-lib-3
Feature: Borrowing
  Borrowers take items home and know when to bring them back.

  @R1
  Scenario: A new loan is due 21 days after it starts
    Given the borrower "Kofi" registered at the "Riverside" branch in time zone "America/Denver"
    When Kofi borrows "The Quiet Orchard" on 2026-04-01
    Then the loan is due on 2026-04-22
