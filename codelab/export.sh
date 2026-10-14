#!/usr/bin/env bash
# Export codelab.md for the Google codelabs format (claat), applying the
# publish-time rewrites that the local HTML build does not need:
#
#   - "Go deeper" links written as deep-dive.md#anchor become absolute links to
#     wherever you host deep-dive.html (relative links 404 on
#     codelabs.developers.google.com, which only serves the exported codelab).
#   - `status: Draft` becomes `status: Published`.
#
# Usage:
#   ./codelab/export.sh https://your-host.example/agent-governance-lab/deep-dive.html
#
# Output: codelab/export/agent-governance-lab/ (serve it with `claat serve`
# from codelab/export). The source codelab.md is not modified.
set -euo pipefail
cd "$(dirname "$0")"

DEEP_DIVE_URL="${1:?Usage: $0 https://host/path/deep-dive.html}"
if ! command -v claat >/dev/null 2>&1; then
  echo "claat not found. Install: go install github.com/googlecodelabs/tools/claat@latest" >&2
  exit 1
fi
if grep -q 'YOUR_GITHUB_USERNAME' codelab.md; then
  echo "codelab.md still contains YOUR_GITHUB_USERNAME; run the placeholder sed in maintainer/PUBLISHING.md first." >&2
  exit 1
fi

rm -rf export && mkdir -p export
sed -e "s|](deep-dive\.md#|](${DEEP_DIVE_URL}#|g" \
    -e "s|](deep-dive\.md)|](${DEEP_DIVE_URL})|g" \
    -e 's|^status: Draft$|status: Published|' \
    codelab.md > export/codelab.md
cp -R img export/img

remaining=$(grep -c 'deep-dive\.md' export/codelab.md || true)
if [ "${remaining}" != "0" ]; then
  echo "Note: ${remaining} line(s) still mention deep-dive.md in prose (not links); review them:" >&2
  grep -n 'deep-dive\.md' export/codelab.md >&2 || true
fi

( cd export && claat export codelab.md )
# claat copies referenced images for Markdown sources; make sure the folder exists either way.
[ -d export/agent-governance-lab/img ] || cp -R img export/agent-governance-lab/img

echo
echo "Exported to codelab/export/agent-governance-lab/"
echo "Preview:  (cd codelab/export && claat serve)"
echo "Reminder: the diagrams are SVG; convert img/*.svg to PNG and update the references if your host does not serve SVG."
