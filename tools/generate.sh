# tools/generate.sh
#!/usr/bin/env bash
set -euo pipefail

ROOT="${1:-.}"
PROTO_DIR="${ROOT}/proto"
OUT_DIR="${ROOT}/generated"

mkdir -p "${OUT_DIR}"

PROTOC="$(command -v protoc)"
GRPC_PLUGIN="$(command -v grpc_cpp_plugin)"

if [[ -z "${PROTOC}" ]]; then
  echo "ERROR: protoc not found in PATH"
  exit 1
fi
if [[ -z "${GRPC_PLUGIN}" ]]; then
  echo "ERROR: grpc_cpp_plugin not found in PATH"
  exit 1
fi

"${PROTOC}" \
  -I "${PROTO_DIR}" \
  --cpp_out="${OUT_DIR}" \
  --grpc_out="${OUT_DIR}" \
  --plugin=protoc-gen-grpc="${GRPC_PLUGIN}" \
  "${PROTO_DIR}/dist_index.proto"

echo "Generated into ${OUT_DIR}:"
ls -1 "${OUT_DIR}"
