#!/usr/bin/env bash
set -euo pipefail

CASE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
COEVOLVE_ROOT="$(cd "${CASE_DIR}/../../../.." && pwd)"
TARGET_REPOSITORY="${TARGET_REPOSITORY:-https://github.com/HaodiFan/engineering-everything.git}"
BASELINE_COMMIT="abcd3bb26bb2c05236ac041d6cebf3af86a81357"
ROLLBACK_COMMIT="ba7468a61f701cf8b8643503b8e7082885af5d22"
EVALUATED_COMMIT="6997b61d100708603bf80711a3d7c1604dc097fe"
RUN_GITHUB_GATES="${RUN_GITHUB_GATES:-0}"
HOST_HOME="${HOME}"
TEMP_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/coevolve-engineering-everything.XXXXXX")"
TARGET_DIR="${TEMP_ROOT}/engineering-everything"
BASELINE_DIR="${TEMP_ROOT}/baseline"
ROLLBACK_DIR="${TEMP_ROOT}/rollback"
TEMP_HOME="${TEMP_ROOT}/home"

cleanup() {
  if [[ -d "${TARGET_DIR}/.git" || -f "${TARGET_DIR}/.git" ]]; then
    git -C "${TARGET_DIR}" worktree remove --force "${BASELINE_DIR}" >/dev/null 2>&1 || true
    git -C "${TARGET_DIR}" worktree remove --force "${ROLLBACK_DIR}" >/dev/null 2>&1 || true
  fi
  rm -rf "${TEMP_ROOT}"
}
trap cleanup EXIT

run_native_gates() {
  local repo="$1"
  (
    cd "${repo}"
    python3 scripts/sync_references.py --check --json
    python3 scripts/eval_scenarios.py validate --json
    python3 scripts/skill_doctor.py --json
    python3 scripts/self_evolve.py check --json
    python3 scripts/self_evolve.py doctor --json
    python3 scripts/lesson.py validate
    python3 -m unittest discover -s tests
    python3 -m py_compile scripts/*.py
  )
}

printf '== CoEvolve tests ==\n'
(
  cd "${COEVOLVE_ROOT}"
  python3 -m pytest -q
)

printf '== Fetch pinned Skillware revisions ==\n'
git clone --quiet "${TARGET_REPOSITORY}" "${TARGET_DIR}"
git -C "${TARGET_DIR}" checkout --quiet --detach "${EVALUATED_COMMIT}"
git -C "${TARGET_DIR}" worktree add --quiet --detach "${BASELINE_DIR}" "${BASELINE_COMMIT}"
git -C "${TARGET_DIR}" worktree add --quiet --detach "${ROLLBACK_DIR}" "${ROLLBACK_COMMIT}"

printf '== Baseline native gates ==\n'
run_native_gates "${BASELINE_DIR}"

printf '== Evaluated release native gates ==\n'
run_native_gates "${TARGET_DIR}"
(
  cd "${TARGET_DIR}"
  python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py structure
)

printf '== Normalized target-owned Skill preservation ==\n'
python3 - "${TARGET_DIR}" "${BASELINE_COMMIT}" "${EVALUATED_COMMIT}" <<'PY'
import re
import subprocess
import sys

repo, baseline, evaluated = sys.argv[1:]

def git_text(revision, path):
    return subprocess.check_output(
        ["git", "-C", repo, "show", f"{revision}:{path}"], text=True
    )

def normalize(text, bootloader=False):
    text = re.sub(
        r"(?m)^  version: \d+\.\d+\.\d+$", "  version: <normalized>", text
    )
    if bootloader:
        text = re.sub(
            r"\n## EvoZeus-CoEvolve 状态检查\n.*?(?=\n# Using Engineering Everything / 启动器\n)",
            "\n",
            text,
            flags=re.S,
        )
        text = re.sub(
            r"\n## EvoZeus-CoEvolve 自进化治理\n.*?(?=\n## References\n)",
            "\n",
            text,
            flags=re.S,
        )
        text = re.sub(
            r"\n## EvoZeus-CoEvolve Migration Note:.*\Z", "\n", text, flags=re.S
        )
        text = re.sub(r"\n{3,}", "\n\n", text)
    return "\n".join(line.rstrip() for line in text.strip().splitlines()) + "\n"

skills = subprocess.check_output(
    ["git", "-C", repo, "ls-tree", "-d", "--name-only", f"{evaluated}:skills"],
    text=True,
).splitlines()
if len(skills) != 12:
    raise SystemExit(f"expected 12 runtime Skills, found {len(skills)}")

equal = 0
for skill in skills:
    path = f"skills/{skill}/SKILL.md"
    before = normalize(git_text(baseline, path), skill == "using-engineering-everything")
    after = normalize(git_text(evaluated, path), skill == "using-engineering-everything")
    if before != after:
        raise SystemExit(f"normalized behavior changed: {path}")
    equal += 1
print(f"normalized_skill_files_equal={equal}/12")
PY

printf '== Fresh-client canonical install and idempotency ==\n'
mkdir -p "${TEMP_HOME}"
HOME="${TEMP_HOME}" python3 "${TARGET_DIR}/scripts/install.py" install --target both
FIRST_DIGEST="$({
  find "${TEMP_HOME}/.codex/skills" "${TEMP_HOME}/.agents/skills" \
    -mindepth 1 -maxdepth 1 -type l -print | sort | while IFS= read -r path; do
      printf '%s -> %s\n' "${path#"${TEMP_HOME}"}" "$(readlink "${path}")"
    done
} | git hash-object --stdin)"
HOME="${TEMP_HOME}" python3 "${TARGET_DIR}/scripts/install.py" install --target both
SECOND_DIGEST="$({
  find "${TEMP_HOME}/.codex/skills" "${TEMP_HOME}/.agents/skills" \
    -mindepth 1 -maxdepth 1 -type l -print | sort | while IFS= read -r path; do
      printf '%s -> %s\n' "${path#"${TEMP_HOME}"}" "$(readlink "${path}")"
    done
} | git hash-object --stdin)"
LINK_COUNT="$(find "${TEMP_HOME}/.codex/skills" "${TEMP_HOME}/.agents/skills" \
  -mindepth 1 -maxdepth 1 -type l | wc -l | tr -d ' ')"
COPY_COUNT="$(find "${TEMP_HOME}/.codex/skills" "${TEMP_HOME}/.agents/skills" \
  -mindepth 1 -maxdepth 1 ! -type l | wc -l | tr -d ' ')"
[[ "${FIRST_DIGEST}" == "${SECOND_DIGEST}" ]]
[[ "${LINK_COUNT}" == "24" ]]
[[ "${COPY_COUNT}" == "0" ]]
printf 'fresh_client_symlinks=%s copied_entries=%s idempotent=true\n' "${LINK_COUNT}" "${COPY_COUNT}"

if [[ "${RUN_GITHUB_GATES}" == "1" ]]; then
  printf '== Authenticated GitHub governance gates ==\n'
  AUTH_TOKEN="${GH_TOKEN:-${GITHUB_TOKEN:-}}"
  if [[ -z "${AUTH_TOKEN}" ]]; then
    AUTH_TOKEN="$(HOME="${HOST_HOME}" gh auth token)"
  fi
  mkdir -p "${TEMP_HOME}/.evozeus/.projects/HaodiFan"
  ln -s "${TARGET_DIR}" "${TEMP_HOME}/.evozeus/.projects/HaodiFan/engineering-everything"
  python3 - "${TARGET_DIR}" "${TEMP_HOME}" <<'PY'
import json
import pathlib
import sys

target = pathlib.Path(sys.argv[1])
home = pathlib.Path(sys.argv[2])
manifest_path = target / ".evozeus-wrapper" / "wrapper.json"
manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
manifest["install_links"] = [
    str(home / host / "skills" / skill)
    for host in (".codex", ".agents")
    for skill in ("using-engineering-everything", "engineering-everything")
]
manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
PY
  (
    cd "${TARGET_DIR}"
    HOME="${TEMP_HOME}" GH_TOKEN="${AUTH_TOKEN}" \
      python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py doctor \
        --repo HaodiFan/engineering-everything
    HOME="${TEMP_HOME}" GH_TOKEN="${AUTH_TOKEN}" \
      python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py version \
        --repo HaodiFan/engineering-everything --current-tag v0.13.0
    HOME="${TEMP_HOME}" GH_TOKEN="${AUTH_TOKEN}" \
      python3 .evozeus-wrapper/scripts/evozeus_wrapper_preflight.py release \
        --tag v0.13.0 --release-notes release-notes-v0.13.0.md
  )
fi

printf '== Prior-release recovery gates ==\n'
run_native_gates "${ROLLBACK_DIR}"

printf 'PASS: Engineering Everything feasibility case reproduced.\n'
