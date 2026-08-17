#!/usr/bin/env bash

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
IMG_GEN_DIR="$(cd -- "${SCRIPT_DIR}/.." && pwd)"
PROJECT_DIR="$(cd -- "${IMG_GEN_DIR}/.." && pwd)"
PROMPT_DIR="${IMG_GEN_DIR}/prompt/user"
OUTPUT_DIR="${IMG_GEN_DIR}/output"
LOG_DIR="${OUTPUT_DIR}/logs"
PYTHON_BIN="${PYTHON_BIN:-python}"
MAX_CONCURRENCY="${MAX_CONCURRENCY:-200}"
IMAGES_PER_PROMPT="${IMAGES_PER_PROMPT:-2}"
MAX_ATTEMPTS="${MAX_ATTEMPTS:-8}"
RETRY_DELAY_SECONDS="${RETRY_DELAY_SECONDS:-20}"

mkdir -p "${OUTPUT_DIR}" "${LOG_DIR}"

mapfile -d '' PROMPTS < <(
    find "${PROMPT_DIR}" -maxdepth 1 -type f -name '*.md' -print0 | sort -z
)

if (( ${#PROMPTS[@]} == 0 )); then
    printf 'No Markdown prompts found in %s\n' "${PROMPT_DIR}" >&2
    exit 1
fi

if ! [[ "${MAX_CONCURRENCY}" =~ ^[1-9][0-9]*$ ]]; then
    printf 'MAX_CONCURRENCY must be a positive integer: %s\n' "${MAX_CONCURRENCY}" >&2
    exit 1
fi

if ! [[ "${IMAGES_PER_PROMPT}" =~ ^[1-9][0-9]*$ ]]; then
    printf 'IMAGES_PER_PROMPT must be a positive integer: %s\n' "${IMAGES_PER_PROMPT}" >&2
    exit 1
fi

if ! [[ "${MAX_ATTEMPTS}" =~ ^[1-9][0-9]*$ ]]; then
    printf 'MAX_ATTEMPTS must be a positive integer: %s\n' "${MAX_ATTEMPTS}" >&2
    exit 1
fi

if ! [[ "${RETRY_DELAY_SECONDS}" =~ ^[1-9][0-9]*$ ]]; then
    printf 'RETRY_DELAY_SECONDS must be a positive integer: %s\n' "${RETRY_DELAY_SECONDS}" >&2
    exit 1
fi

run_prompt() {
    local prompt_path="$1"
    local prompt_name
    local log_path
    local attempt
    local status
    local delay

    prompt_name="$(basename -- "${prompt_path}" .md)"
    log_path="${LOG_DIR}/${prompt_name}.log"
    : >"${log_path}"

    for (( attempt = 1; attempt <= MAX_ATTEMPTS; attempt++ )); do
        {
            printf '[attempt] %s/%s\n' "${attempt}" "${MAX_ATTEMPTS}"
            printf '[start] %s\n' "$(date --iso-8601=seconds)"
            printf '[prompt] %s\n' "${prompt_path}"
        } >>"${log_path}"

        set +e
        "${PYTHON_BIN}" "${IMG_GEN_DIR}/src/main.py" \
            --user-prompt "${prompt_path}" \
            --n "${IMAGES_PER_PROMPT}" \
            --output-dir "${OUTPUT_DIR}" \
            --prefix "${prompt_name}" >>"${log_path}" 2>&1
        status=$?
        set -e

        if (( status == 0 )); then
            printf '[done] %s\n' "$(date --iso-8601=seconds)" >>"${log_path}"
            return 0
        fi

        if ! rg -q '429|RateLimitReached|rate limit' "${log_path}"; then
            return "${status}"
        fi

        if (( attempt == MAX_ATTEMPTS )); then
            return "${status}"
        fi

        delay=$(( RETRY_DELAY_SECONDS * attempt ))
        printf '[retry] rate limited; sleeping %ss\n' "${delay}" >>"${log_path}"
        sleep "${delay}"
    done
}

printf '[batch-start] %s\n' "$(date --iso-8601=seconds)"
printf '[project] %s\n' "${PROJECT_DIR}"
printf '[prompt-dir] %s\n' "${PROMPT_DIR}"
printf '[output-dir] %s\n' "${OUTPUT_DIR}"
printf '[prompt-count] %s\n' "${#PROMPTS[@]}"
printf '[images-per-prompt] %s\n' "${IMAGES_PER_PROMPT}"
printf '[max-concurrency] %s\n' "${MAX_CONCURRENCY}"
printf '[max-attempts] %s\n' "${MAX_ATTEMPTS}"

declare -a PIDS=()
declare -a PID_NAMES=()
failed_count=0

for prompt_path in "${PROMPTS[@]}"; do
    while (( ${#PIDS[@]} >= MAX_CONCURRENCY )); do
        pid="${PIDS[0]}"
        pid_name="${PID_NAMES[0]}"
        if wait "${pid}"; then
            printf '[success] %s\n' "${pid_name}"
        else
            printf '[failed] %s; see %s\n' "${pid_name}" "${LOG_DIR}/${pid_name}.log" >&2
            failed_count=$((failed_count + 1))
        fi
        PIDS=("${PIDS[@]:1}")
        PID_NAMES=("${PID_NAMES[@]:1}")
    done

    prompt_name="$(basename -- "${prompt_path}" .md)"
    run_prompt "${prompt_path}" &
    PIDS+=("$!")
    PID_NAMES+=("${prompt_name}")
    printf '[queued] %s\n' "${prompt_name}"
done

while (( ${#PIDS[@]} > 0 )); do
    pid="${PIDS[0]}"
    pid_name="${PID_NAMES[0]}"
    if wait "${pid}"; then
        printf '[success] %s\n' "${pid_name}"
    else
        printf '[failed] %s; see %s\n' "${pid_name}" "${LOG_DIR}/${pid_name}.log" >&2
        failed_count=$((failed_count + 1))
    fi
    PIDS=("${PIDS[@]:1}")
    PID_NAMES=("${PID_NAMES[@]:1}")
done

if (( failed_count > 0 )); then
    printf '[batch-failed] %s prompt(s) failed; logs: %s\n' "${failed_count}" "${LOG_DIR}" >&2
    exit 1
fi

printf '[batch-done] %s\n' "$(date --iso-8601=seconds)"
