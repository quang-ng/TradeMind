#!/usr/bin/env bash

set -euo pipefail

repo_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
deploy_branch="${DEPLOY_BRANCH:-main}"

cd "${repo_dir}"

if [[ -n "$(git status --porcelain --untracked-files=no)" ]]; then
    echo "Tracked files have local changes; refusing to overwrite them" >&2
    git status --short
    exit 1
fi

git fetch origin "${deploy_branch}"
git checkout "${deploy_branch}"
git pull --ff-only origin "${deploy_branch}"

compose_files=(
    -f docker-compose.yml
    -f docker-compose.production.yml
    -f docker-compose.public.yml
)

docker compose "${compose_files[@]}" config --quiet
docker compose "${compose_files[@]}" up -d --build --wait --wait-timeout 300

echo
echo "Deployed revision: $(git rev-parse --short HEAD)"
docker compose "${compose_files[@]}" ps
