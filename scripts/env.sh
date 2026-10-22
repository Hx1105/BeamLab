# Source this file for the BeamLab instance only. Never alter HOME/CODEX_HOME.
BEAMLAB_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
export BEAMLAB_ROOT
export AGH_HOME="${BEAMLAB_INSTANCE_HOME:-$BEAMLAB_ROOT/runtime/agh-home}"
export AGNES_PROFILE=local-dev
export AGNES_HUB_LISTEN="${BEAMLAB_HUB_LISTEN:-127.0.0.1:44480}"
export AGNES_HUB_DATA="$BEAMLAB_ROOT/runtime/hub-data"
export AGNES_HUB_ORIGIN="http://$AGNES_HUB_LISTEN"
export BEAMLAB_HUB_AGENT="${BEAMLAB_HUB_AGENT:-ws://$AGNES_HUB_LISTEN/ws/hub}"
export BEAMLAB_DEVICE_URL="${BEAMLAB_DEVICE_URL:-ws://127.0.0.1:44481}"
export BEAMLAB_NODE="$BEAMLAB_ROOT/vendor/node"
export BEAMLAB_AGH="$BEAMLAB_ROOT/vendor/agh-runtime/agnes.mjs"
export BEAMLAB_UPSTREAM="$AGH_SOURCE_DIR"
export OMP_NUM_THREADS=4 OPENBLAS_NUM_THREADS=4 MKL_NUM_THREADS=4
export AGNES_SECRET_BEAMLAB_LOCAL=beamlab-local-placeholder
export PYTHONPATH="$BEAMLAB_ROOT/src"
