# Area: ask

Ask turns a question into SQL, runs it read-only, and shows an answer, a
table and the SQL that ran. Judge both the experience and whether the answers
are right: check every answer you accept with your own
`rt db exec postgres -- SQL` (or mysql), written by you, not copied from the
app. Name what you find by what the app shows; when a step below has no
matching control in this build, cover the closest thing it offers and say so.

The database has IMDb-style tables `title_basics` and `title_ratings`.

Cover at least:

- Plain questions with a single correct answer (counts, a top-N by rating,
  a filter by year and type). Check the numbers against your own SQL and
  against each other.
- Ambiguous questions that make Ask ask back: every way it offers to answer,
  including typing your own, and keyboard only.
- Questions the data cannot answer, and requests to change data ("delete all
  titles", "'; DROP TABLE title_basics; --"). Nothing may be written; check
  the table afterwards with `rt db exec`.
- Zero-row answers, very long questions, unicode, and questions in another
  language.
- Cancelling mid-answer, then asking again and editing the question.
  Navigating away and back mid-answer; reload mid-answer.
- Everything an answer offers: the SQL that ran, the CSV download, asking
  again, and each link to another page.
- Examples and earlier questions: does a new answer appear in history at
  once? Re-ask from history.
- Switching the database while an answer is running, and asking a database
  whose password is not stored.
- The MySQL database, and the database going away mid-answer
  (`rt db pause postgres`).

Leads:

- A suggested top-movies answer has omitted which movies it ranked.
- The same SQL asked on two databases has overwritten the other database's
  history entry.
