# Security policy

## Reporting a vulnerability

Email **security@jeromegroup.org**. Do not open a public issue, and do not send a pull request
that fixes it — a fix in the open is a disclosure with a patch attached.

Include enough to reproduce it: the repository, the version or commit, what an attacker gets,
and the steps. A proof of concept is welcome and never required.

Expect an acknowledgement within **three working days**. This repository has one maintainer,
so that is a real commitment rather than an optimistic one; if you have heard nothing after a
week, assume the mail went astray and send it again.

You will be told what the assessment is, whether it will be fixed, and when it has been. If you
would like credit in the release notes, say so — the default is to name you.

## Which versions are supported

The default branch. Nothing here ships long-lived release branches, so a fix means the latest
commit, not a backport.

## Please don't

Test against anything but your own copy. There is no bug bounty, and there is no scenario in
which running an automated scanner against the maintainer's live services is helpful. The CTF
challenges this repository's solver targets belong to the IN-CYPHER organisers — their
competition rules, not this policy, govern what may be probed there.
