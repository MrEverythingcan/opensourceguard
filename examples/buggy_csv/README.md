# Empty CSV case

The baseline implementation indexes `rows[0]` for empty input. The candidate
patch preserves the existing one-row and multi-row behavior while returning an
empty list for empty input.
