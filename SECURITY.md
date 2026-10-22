# Security Policy

## Supported versions

LocalConvert is in public beta. Security fixes land in the latest release only.

## Reporting a vulnerability

Please **don't open a public issue** for security problems.

Use GitHub's private reporting instead: go to the **Security** tab of this repository and click **Report a vulnerability**. Include steps to reproduce, the LocalConvert version and your operating system.

You'll get an acknowledgement within a few days. Once a fix ships, we're happy to credit you in the release notes.

## What we care about most

LocalConvert's promise is that files never leave your computer, so these matter most:

- Any way for a file, or data from one, to leave the machine, or for the app to make a network request at all.
- Other websites or local programs reaching the local server: bypassing the per-launch token, the `127.0.0.1` binding, the `Host` check or the Content-Security-Policy.
- Malicious input files that write outside the output folder (for example archive path traversal), run code, or read other files on disk.
- Temporary copies of dropped files that aren't cleaned up on exit.
