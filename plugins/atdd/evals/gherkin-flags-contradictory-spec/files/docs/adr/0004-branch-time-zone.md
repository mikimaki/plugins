# ADR-0004: Date and time rules use the borrower's home-branch time zone

Every branch has exactly one IANA time zone (e.g. America/Denver). Any rule that talks about "a day", a date or
a time of day is evaluated in the time zone of the borrower's home branch, never in UTC or the server's zone.
