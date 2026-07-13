#!/usr/bin/env bash
# Regenerate gRPC Python stubs from the vendored .proto files into
# src/advisor/lnrpc/. Run from the advisor/ project root with the venv active.
#
#   . .venv/bin/activate && ./scripts/gen_proto.sh
#
# lnd's lightning.proto is self-contained (no imports), so generation is a
# single protoc invocation. The generated *_pb2_grpc.py uses an absolute
# `import lightning_pb2` — we rewrite it to a package-relative import so the
# stubs work as the advisor.lnrpc subpackage.
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
OUT="$ROOT/src/advisor/lnrpc"
mkdir -p "$OUT"

python -m grpc_tools.protoc \
  --proto_path="$ROOT/proto" \
  --python_out="$OUT" \
  --grpc_python_out="$OUT" \
  lightning.proto

# Make the grpc stub import its pb2 module package-relatively.
python - "$OUT/lightning_pb2_grpc.py" <<'PY'
import re, sys
p = sys.argv[1]
s = open(p).read()
s = re.sub(r'^import lightning_pb2 as', 'from . import lightning_pb2 as', s, flags=re.M)
open(p, "w").write(s)
PY

# Ensure the package is importable.
touch "$OUT/__init__.py"
echo "generated stubs in $OUT"
