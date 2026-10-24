# Sourced by the build scripts: warn if ./86box predates 86BOX_MIN_COMMIT, the oldest upstream
# 86Box commit with every automation command the MCP server uses. Newer is fine.
want=$(cat "$root/86BOX_MIN_COMMIT")
if ! git -C "$root/86box" merge-base --is-ancestor "$want" HEAD 2>/dev/null; then
  echo "warning: 86box/ doesn't contain $want (86BOX_MIN_COMMIT); some MCP tools need newer automation commands." >&2
  echo "         git -C 86box pull   (86Box master has the automation socket)" >&2
fi
