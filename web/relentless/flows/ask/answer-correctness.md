# Flow: ask/answer-correctness

Capability: ask
Areas: ask
Modes: desktop, browser
Minutes: 25
Covers: rdst/features/ask web-apps/apps/rdst/src/features/ask/AskAnswer.tsx web-apps/apps/rdst/src/features/ask/askModel.ts

Every answer Ask gives must be right, and its summary must say what the
table shows. Use your own Anthropic key through the UI (`rt fill-secret`); do
not sign in to Readyset.

## Mission

The PostgreSQL database has IMDb-style `title_basics` and `title_ratings`.
Before asking, learn the data yourself (row counts, title types, year range,
NULLs, ties in ratings, titles with no rating) so you know the true answers.

Ask at least 25 questions across these shapes, and for each record the
question, the app's answer, your independent answer, and whether they agree:

- Counts and filters: totals, by type, by year, ranges, NOT and NULL
  handling ("titles with no runtime", "titles without a rating").
- Rankings: top-N by rating, by votes, lowest-N, ties at the cut, top-N with
  a minimum vote count.
- Grouping: counts per type and per decade, averages per genre (genres is a
  comma list), groups where nothing matches.
- Joins: ratings with titles, titles missing from ratings, an average rating
  per type.
- Dates and numbers: decades, rounding of averages, percentages that should
  sum to 100.
- Answers with one row, zero rows, and more rows than the page shows (does
  it say it shows only part?).
- Follow-ups in the same session ("now only movies", "and the year before").
- A question that names a title type ("movies", "TV series", "shorts"): does
  the SQL that ran filter that type?

## Prove

- Each answer matches your own SQL; the SQL shown is the SQL that ran.
- The summary names the right column, leader and row count, and reads ties,
  NULLs, negative numbers and decimals correctly. Numbers are formatted
  correctly (thousands, decimals, NULL).
- The same question on MySQL gives the same answer where the data is the
  same.

File a finding for each wrong answer or misleading summary, with your
independent SQL and its result as evidence. End with a table: question,
your answer, the app's answer, agree (yes/no).
