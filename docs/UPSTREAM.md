# Changes to 86Box

Everything this project needs from 86Box is now in upstream 86Box `master`.
Both pull requests were merged on 2026-10-17:

| Pull request | What it adds |
|---|---|
| [#8365](https://github.com/86Box/86Box/pull/8365) | The automation socket (`mouse: let input through when an automation client sends it` and `Add an opt-in automation control socket`) |
| [#8361](https://github.com/86Box/86Box/pull/8361) | `char_file: add an option to write output to the file immediately`. 86box-mcp's COM1 capture turns the option on. |

`86BOX_COMMIT` names the upstream commit with both, which is the merge of #8361.
Build 86Box with `-D AUTOMATION=ON`; it's off by default, so official releases
don't have the socket. [automation-code-guide.md](automation-code-guide.md)
walks through the code.

When this project needs something new from 86Box, record it here: what
changes, why, and the pull request. Make it on a fresh branch from upstream
`master`.
