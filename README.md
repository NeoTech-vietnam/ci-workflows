# NeoTech reusable CI workflows

Private, versioned GitHub Actions orchestration for NeoTech projects. Workflows
are called from product repositories; runs, artifacts and project credentials
belong to the caller. Project scripts remain the authority for fetch/test/check
behavior, so local development uses the same commands as CI.

## ESP-IDF workflow contract

Call `.github/workflows/esp-idf.yml@v1.0.0` from a job using `workflow_call`.

| Input | Default | Meaning |
| --- | --- | --- |
| `idf-version` | `v6.0.1` | Official `espressif/idf` image tag, pinned as vX.Y.Z |
| `target` | `esp32s3` | Build target |
| `package` | empty | Optional package key from the caller's packages.yml |
| `package-ref` | empty | Corresponding branch, tag or SHA override |

Pass secret `PACKAGE_READ_TOKEN`: a fine-grained token with Contents: read on
the caller's private source modules. Store it on the product repository, not in
this repository's source. Do not upload a broad desktop Git/CLI credential.

The caller supplies `TOOLCONF/requirements-dev.txt`, `TOOLCONF/workspace.py`,
`test/run_host_tests.py` and `TOOLCONF/check_build.py`. The latter is the product's
authority for supported SDK, target, configuration and memory checks. Merely
changing workflow inputs does not make a product compatible with another target.

Jobs use GitHub-hosted Ubuntu 24.04. Host tests run with Python 3.12 and the system
C compiler. Firmware builds run inside the official SDK container with a matching
user ID, writable temporary HOME, and explicit Git safe directories. Output is
uploaded for seven days, including resolved module SHAs, logs, firmware and map.

## Artifact flashing workflow contract

Call `.github/workflows/flash-esp32.yml@v1.2.0` from a manually dispatched main
workflow with `contents: read` and `actions: read` permissions. Inputs are
`source-run-id` (empty selects the latest successful main integration build),
`serial-port` (required explicit Linux USB serial path), and `baud` (default
`460800`; also supports `115200` and `921600`). `monitor-seconds` defaults to
`30` and accepts `0` (skip), `15`, `30`, `60`, `120` and `300`. `monitor-baud`
defaults to `115200` and also accepts `230400`, `460800` and `921600`.
No additional secret is required.

The caller provides `TOOLCONF/hardware_flash.py`, `TOOLCONF/requirements-flash.txt`,
its `integration.yml` build workflow and the `firmware-esp32s3-<run_id>` artifact
contract. Selection runs on
GitHub-hosted Ubuntu. Flashing runs on `[self-hosted, Linux, neocam-flash]` and
uses `$HOME/.venvs/neocam-flash/bin/python` with esptool 5.6.0 and IDF Monitor
1.11.0, installed from the caller's pinned requirements. It accepts only
completed successful main push/manual builds from the calling repository.

The self-hosted job downloads the selected artifact by ID, validates its manifest,
writes/verifies/resets the ESP32-S3, then runs Espressif's monitor in command-stream
mode for the selected duration. The monitor resets the board on connection to
capture a fresh boot, streams timestamped output and exits normally when its
sleep command finishes. A duration plus 30-second watchdog bounds reconnect or
shutdown hangs and fails the step if exceeded. Raw serial output is uploaded as
`serial-monitor.log` alongside the flash report. Address/panic/core-dump decoding
is disabled because the flashing runner does not require the SDK/toolchain.
This capture does not assert application health. The workflow never
rebuilds or consumes the module token. Per-board concurrency does not cancel an
active write. The label represents one dedicated board; do not assign it to
unrelated runners. See the calling project's `docs/self-hosted-flashing.md` for
Ubuntu registration, service, serial-access and dispatch instructions.

## Private workflow access

Settings → Actions → General → Access must allow other repositories in the
`NeoTech-vietnam` organization. This permission lets GitHub load the workflow; it
does not grant the caller's token access to private source modules. The caller
passes the separate read-only module credential explicitly.

## Releases and verification

Published tags such as `v1.0.0` are immutable by convention. Changes get a new
workflow version; callers select when to upgrade.

Run official `actionlint` against all workflow files before publication.
`smoke.yml` also builds the SDK's stock hello_world example for ESP32-S3 on a
GitHub runner. This proves runner/container/toolchain availability independently
of product credentials; it is not a NeoCam product or hardware validation.
