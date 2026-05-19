# License

URSA-OSCAR is licensed under the **GNU General Public License v3.0 or later** (GPL-3.0-or-later).

## What this means in practice

- **You can use it.** Run URSA-OSCAR on your own hardware for your own purposes. No license fee, no usage restrictions, no telemetry.
- **You can study it.** The full source is on GitHub. Read the code, understand what it does to your data, audit the security claims yourself.
- **You can modify it.** Fork the repository, change anything, run your fork on your hardware.
- **You can share it.** Pass copies along to friends or contribute back upstream. If you ship a modified version to someone else, the GPL requires you also ship the source.
- **You can't relicense it.** A derivative work (fork, integration, repackaging) that's distributed must also be GPL-3.0. This is the copyleft term that keeps the codebase open.

## Full license text

The complete GPL-3.0-or-later text is in the repository at `LICENSE`. The license is also available at https://www.gnu.org/licenses/gpl-3.0.html.

## Patent grant

The GPL-3.0 includes an explicit patent grant from contributors. Any patents the contributors hold that would be infringed by using URSA-OSCAR are licensed to you under the same terms as the copyright.

## Warranty disclaimer

Per the GPL-3.0:

> THERE IS NO WARRANTY FOR THE PROGRAM, TO THE EXTENT PERMITTED BY APPLICABLE LAW. EXCEPT WHEN OTHERWISE STATED IN WRITING THE COPYRIGHT HOLDERS AND/OR OTHER PARTIES PROVIDE THE PROGRAM "AS IS" WITHOUT WARRANTY OF ANY KIND, EITHER EXPRESSED OR IMPLIED, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE.

URSA-OSCAR is provided as-is. It is not a medical device. It does not replace your sleep medicine provider. It does not diagnose or prescribe. If you rely on the data for clinical decisions, that's your responsibility and your provider's, not URSA-OSCAR's maintainers'.

## Why GPL-3.0 specifically

A few alternatives were considered:

- **MIT / BSD** — permissive, but allows downstream forks to close the source. The URSA-OSCAR project intentionally wants any derivative work to remain open so the community benefits from improvements.
- **AGPL-3.0** — stricter copyleft that triggers source-disclosure on network use, not just distribution. Considered but rejected because URSA-OSCAR is designed for self-hosted single-tenant use; the AGPL trigger doesn't match the deployment model.
- **GPL-3.0** — the balance. Source-disclosure on distribution, not on running. Patent grant included. Matches the upstream OSCAR project's spirit (OSCAR itself is GPL-3.0).
