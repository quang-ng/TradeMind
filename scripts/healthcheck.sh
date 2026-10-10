#!/usr/bin/env bash

# Post-deploy health verification, run by scripts/deploy.sh (and safe to run
# by hand on the VPS at any time). Exits non-zero — failing the Deploy
# workflow — when any of these do not hold:
#   - every long-running service has a running container, healthy if it
#     defines a healthcheck;
#   - the one-shot `migrate` job exited 0;
#   - no container restarted during a short soak window (catches services
#     that pass their first healthcheck and then crash-loop);
#   - the host-loopback HTTP endpoints answer.

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
soak_seconds="${HEALTHCHECK_SOAK_SECONDS:-60}"

cd "${repo_dir}"

compose_files=(
    -f docker-compose.yml
    -f docker-compose.production.yml
    -f docker-compose.public.yml
)

# One-shot jobs: expected to have exited 0, not to be running.
one_shot_services=" migrate "

failures=0

fail() {
    echo "FAIL $*" >&2
    failures=$((failures + 1))
}

# Records a failure for any container that is not in its expected state, and
# sets restart_counts to "<service> <restart_count>" lines. Runs in the
# current shell (not a $(...) subshell) so fail() can update the counter.
restart_counts=""
check_containers() {
    local service container_id state health exit_code restarts
    restart_counts=""
    for service in $(docker compose "${compose_files[@]}" config --services); do
        container_id="$(docker compose "${compose_files[@]}" ps --all --quiet "${service}")"
        if [[ -z "${container_id}" ]]; then
            fail "${service}: no container"
            continue
        fi
        read -r state health exit_code restarts < <(
            docker inspect --format \
                '{{.State.Status}} {{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}} {{.State.ExitCode}} {{.RestartCount}}' \
                "${container_id}"
        )
        if [[ "${one_shot_services}" == *" ${service} "* ]]; then
            if [[ "${state}" != "exited" || "${exit_code}" != "0" ]]; then
                fail "${service}: state=${state} exit_code=${exit_code} (expected exited/0)"
            fi
        elif [[ "${state}" != "running" ]]; then
            fail "${service}: state=${state} (expected running)"
        elif [[ "${health}" != "healthy" && "${health}" != "none" ]]; then
            fail "${service}: health=${health} (expected healthy)"
        fi
        restart_counts+="${service} ${restarts}"$'\n'
    done
}

probe() {
    local name="$1" url="$2"
    if ! curl --fail --silent --show-error --max-time 10 --output /dev/null "${url}"; then
        fail "${name}: ${url} did not answer"
    fi
}

echo "Health check: container state"
check_containers
restarts_before="${restart_counts}"

# Already broken: report now rather than soaking and repeating every failure.
if (( failures == 0 )); then
    echo "Health check: waiting ${soak_seconds}s for crash loops"
    sleep "${soak_seconds}"

    check_containers
    restarts_after="${restart_counts}"
    if [[ "${restarts_before}" != "${restarts_after}" ]]; then
        fail "container restart counts changed during soak:"
        diff <(printf '%s' "${restarts_before}") <(printf '%s' "${restarts_after}") >&2 || true
    fi
fi

echo "Health check: HTTP endpoints"
probe admin_api http://127.0.0.1:8000/health
probe frontend http://127.0.0.1:3000/

if (( failures > 0 )); then
    echo "Health check FAILED (${failures} problem(s))" >&2
    docker compose "${compose_files[@]}" ps --all >&2
    exit 1
fi

echo "Health check passed"
