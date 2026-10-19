# Area: platform

The Readyset Platform page (`/platform`, where `/demo` leads) runs a caching
demo: it provisions Postgres, Readyset and QueryPilot in Docker on this
machine and shows what caching does to a workload. It needs no database
target. Provisioning pulls images and can take several minutes; wait for it.
Test that the page always says truthfully what state the demo is in, and that
every promise it makes about caching holds.

Cover at least:

- The checks before starting, what they say when Docker is missing or the
  check itself fails, and whether Start is offered only when it can work.
- Starting, the progress shown, and the finished state. If provisioning
  fails, does the page say so and offer a way out, rather than show a demo
  that is not there?
- Any guided steps: does each do what it says?
- Traffic and its chart, caching a query and uncaching it, any caching
  switches: does the chart move the way the copy claims?
- Leaving the page during setup and during traffic, reload, `rt restart`, and
  `rt kill-backend` mid-setup: what state is shown on return?
- Shutting the demo down, including during setup, and starting again.
- Wording that names the user's machine on this platform.

Always finish by shutting the demo down so the containers are removed.
