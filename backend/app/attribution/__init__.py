"""
Turning a spatial path into a lead, without inventing anything.

`integrations.md` §2 pins the normalized `LeadHandoff`, and most of it is
assembly from things already measured. Two fields are not: `attention_score` and
`lead_score` are numbers the contract asks for and the repo had no source for.

Both live here rather than in the consumer, and both are pure functions of rows
the caller already fetched, because a number a client will argue about needs to
be testable without a database in front of it.
"""
