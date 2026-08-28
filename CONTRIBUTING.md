# Contributing to RDST

Pull requests are accepted. This document is about what happens to them, since
the mechanics here are unusual enough to be worth stating plainly.

## How this repository works

This repository is **generated**. Readyset develops RDST in an internal
monorepo and projects the suite here on every merge, preserving full history —
commits keep their author, date and message. Nothing is edited directly on
`main` here, so a merge button on a pull request would be overwritten by the
next sync.

That does not mean patches are unwelcome. It means they take one extra hop.

## The flow

1. **Open a pull request** as you would anywhere. Base it on `main`.
2. A maintainer reviews it here, in the pull request, and discussion happens
   here.
3. On approval, a maintainer carries the commits into Readyset's internal
   review system, preserving your authorship with a `Co-authored-by:` trailer,
   and lands them there.
4. The next sync brings the change back to this repository under your name, and
   the pull request is closed with a link to the resulting commit.

So your commit lands, with your name on it. What you do not get is a merge
performed on this repository, and the resulting SHA will differ from the one in
your branch.

## Before you open a pull request

- **Small and focused travels fastest.** A patch a maintainer can read in one
  sitting reaches the internal review in days; a large refactor may not.
- **Discuss anything structural first** in an issue. The design system in
  `packages/` is shared with software that is not in this repository, so an API
  change there has consequences you cannot see from here.
- **Run the checks**: `pnpm install && pnpm lint && turbo build`, plus
  `pnpm --filter rdst-web test` for the web UI. The CLI has its own suite —
  see [`cli/README.md`](cli/README.md).
- **Match the surrounding style.** `pnpm check` runs the formatter and linter
  that the internal tree is held to.

## Reporting bugs and security issues

Bugs and feature requests: open an issue.

Security vulnerabilities: do **not** open an issue. Follow
[`cli/SECURITY.md`](cli/SECURITY.md).

## Two things that will look odd

**Commits you did not expect.** The shared packages under `packages/` are used
by Readyset software beyond this suite, so their history carries commits about
products that are not here.

**Assets that differ from the official builds.** The type and icon artwork in
this repository are open equivalents of commercially licensed sets; see
[`LICENSE-THIRD-PARTY`](LICENSE-THIRD-PARTY). A patch that adds an icon needs an
open source for it, or the build has nothing to draw.
