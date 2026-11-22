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

## Private workflow access

Settings → Actions → General → Access must allow other repositories in the
`NeoTech-vietnam` organization. This permission lets GitHub load the workflow; it
does not grant the caller's token access to private source modules. The caller
passes the separate read-only module credential explicitly.

## Releases and verification

Published tags such as `v1.0.0` are immutable by convention. Changes get a new
workflow version; callers select when to upgrade.

Run official `actionlint` against both workflow files before publication.
`smoke.yml` also builds the SDK's stock hello_world example for ESP32-S3 on a
GitHub runner. This proves runner/container/toolchain availability independently
of product credentials; it is not a NeoCam product or hardware validation.
