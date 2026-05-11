#!/usr/bin/env bash
# pi-tick.sh — cron wrapper for application-pipeline Pi deployment.
#
# Sequence per ADR-0010 and ADR-0011:
#   1. git fetch --tags in ~/application-pipeline/repo
#   2. Identify highest v* tag
#   3. If new tag: clone → create venv → pip install -e . → smoke test → symlink flip → prune
#   4. On failure before symlink flip: write Failure Report; exit non-zero
#   5. exec pipeline via ~/application-pipeline/current/.venv/bin/python -m application_pipeline
#
# Failure file format mirrors src/application_pipeline/failure_report.py (issue #115).
# We replicate the markdown shape via bash heredoc because the venv may not exist yet
# during the deploy stage. The "Error" class is reported as ShellError to distinguish
# deploy-stage failures from pipeline-stage failures.

set -euo pipefail

BASE_DIR="${HOME}/application-pipeline"
REPO_DIR="${BASE_DIR}/repo"
RELEASES_DIR="${BASE_DIR}/releases"
CURRENT_LINK="${BASE_DIR}/current"
RESULTS_DIR="${BASE_DIR}/data/results"
FAILURES_DIR="${RESULTS_DIR}/failures"
KEEP_RELEASES=3

# ── helpers ──────────────────────────────────────────────────────────────────

log() { echo "[$(date -u '+%Y-%m-%dT%H:%M:%SZ')] $*"; }

current_tag() {
    [[ -L "${CURRENT_LINK}" ]] || return 0
    basename "$(readlink "${CURRENT_LINK}")"
}

write_failure() {
    local stage="$1" error_msg="$2" captured_output="$3"
    local timestamp
    timestamp="$(date -u '+%Y-%m-%dT%H:%M:%SZ')"

    mkdir -p "${FAILURES_DIR}"
    local target="${FAILURES_DIR}/${timestamp}.md"
    local tmp="${target}.tmp"

    local tag
    tag="$(current_tag)"

    local heading="# Run failed at ${timestamp}"
    [[ -n "${tag}" ]] && heading="${heading} (tag ${tag})"

    # Matches on-disk shape from failure_report.py: heading, Stage, Error, log tail.
    cat > "${tmp}" <<FAILEOF
${heading}

**Stage:** ${stage}
**Error:** ShellError: '${error_msg}'
**Last 20 log lines:**
\`\`\`
${captured_output}
\`\`\`
FAILEOF
    mv "${tmp}" "${target}"
    log "Failure report written: ${target}"
}

# ── step 1: fetch tags ────────────────────────────────────────────────────────

log "Fetching tags from upstream"
if ! fetch_out="$(git -C "${REPO_DIR}" fetch --tags 2>&1)"; then
    write_failure "fetch-tags" "git fetch --tags failed" "${fetch_out}"
    exit 1
fi

# ── step 2: identify highest v* tag ──────────────────────────────────────────

log "Identifying latest v* tag"
LATEST_TAG="$(git -C "${REPO_DIR}" tag --sort=-v:refname | grep -E '^v' | head -1 || true)"
if [[ -z "${LATEST_TAG}" ]]; then
    write_failure "identify-tag" "No v* tags found in ${REPO_DIR}" ""
    exit 1
fi
log "Latest tag: ${LATEST_TAG}"

# ── step 3: deploy if tag changed ────────────────────────────────────────────

CURRENT_TAG="$(current_tag)"

if [[ "${LATEST_TAG}" == "${CURRENT_TAG}" ]]; then
    log "Already on ${LATEST_TAG}; skipping deploy"
else
    log "Upgrading: ${CURRENT_TAG:-<none>} → ${LATEST_TAG}"

    RELEASE_DIR="${RELEASES_DIR}/${LATEST_TAG}"

    if [[ -d "${RELEASE_DIR}" ]]; then
        log "Reusing existing staging dir: ${RELEASE_DIR}"
    else
        log "Cloning ${LATEST_TAG} into ${RELEASE_DIR}"
        if ! clone_out="$(git clone --branch "${LATEST_TAG}" --depth 1 "${REPO_DIR}" "${RELEASE_DIR}" 2>&1)"; then
            write_failure "clone" "git clone of ${LATEST_TAG} failed" "${clone_out}"
            exit 1
        fi
    fi

    log "Creating .venv"
    if ! venv_out="$(python3 -m venv "${RELEASE_DIR}/.venv" 2>&1)"; then
        write_failure "create-venv" "python3 -m venv failed" "${venv_out}"
        rm -rf "${RELEASE_DIR}"
        exit 1
    fi

    log "Installing package"
    if ! pip_out="$("${RELEASE_DIR}/.venv/bin/pip" install -e "${RELEASE_DIR}" 2>&1)"; then
        write_failure "pip-install" "pip install -e . failed" "${pip_out}"
        rm -rf "${RELEASE_DIR}"
        exit 1
    fi

    log "Running smoke test"
    if ! smoke_out="$("${RELEASE_DIR}/.venv/bin/python" -c "import application_pipeline" 2>&1)"; then
        write_failure "smoke-test" "import application_pipeline failed" "${smoke_out}"
        rm -rf "${RELEASE_DIR}"
        exit 1
    fi

    log "Flipping symlink to ${LATEST_TAG}"
    ln -sfn "${RELEASE_DIR}" "${CURRENT_LINK}"
    log "Deploy complete: ${LATEST_TAG}"

    log "Pruning old releases (keeping last ${KEEP_RELEASES})"
    # List v* dirs sorted by version ascending; delete all but the last KEEP_RELEASES.
    mapfile -t old_releases < <(
        ls -1d "${RELEASES_DIR}"/v* 2>/dev/null | sort -V | head -n "-${KEEP_RELEASES}"
    )
    for dir in "${old_releases[@]+"${old_releases[@]}"}"; do
        log "Pruning ${dir}"
        rm -rf "${dir}"
    done
fi

# ── step 5: exec pipeline ─────────────────────────────────────────────────────

log "Exec'ing pipeline"
exec "${CURRENT_LINK}/.venv/bin/python" -m application_pipeline
