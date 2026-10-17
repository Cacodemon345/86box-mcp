# Sourced by the build scripts: warn if ./86box lacks the 86Box commit this repo expects.
want=$(cat "$root/86BOX_COMMIT")
if ! git -C "$root/86box" merge-base --is-ancestor "$want" HEAD 2>/dev/null; then
  echo "warning: 86box/ doesn't contain $want (86BOX_COMMIT); the server may expect newer automation commands." >&2
  echo "         git -C 86box pull   (86Box master has the automation socket)" >&2
fi
