# ARM64 host: x86 Build-Bench environment diagnosis and workaround

Recorded November 2, 2026 UTC (November 1 US Eastern).

## Finding

On our ARM64 EC2 host, QEMU 8.2.2 crashed while preparing the
python-intbitset x86_64 target environment. Updating the host's
x86 emulator registration to the tested QEMU 10.2.3 image allowed
this case to reach the package build stage.

The agent, package sources, frozen dependencies, and pinned
Build-Bench validator runtime were unchanged.

This demonstrates a working workaround on this host for this case.
It does not establish the exact upstream bug, validate all five
x86-target cases, or constitute an agent repair.

## Before and after

Host: Ubuntu 24.04.4 LTS, aarch64, kernel 7.0.0-1014-aws.

Before:
- qemu-user-static 1:8.2.2+ds-0ubuntu1.18.
- Original case stopped after 175 seconds: infrastructure_error.
- QEMU internal SIGSEGV, MAPERR, address 0x20.
- Plain amd64 Ubuntu grep also crashed outside Build-Bench.

After:
- QEMU 10.2.3 from the pinned image below.
- Both direct invocation and normal Docker emulation ran grep successfully.
- Original case stopped after 643 seconds: failed, stage package_build.
- Dependency recovery succeeded at 617 seconds.
- dpkg-buildpackage started for amd64.
- Cython processed intbitset.pyx.
- Documentation build failed with:
  error: invalid command 'build_sphinx'
- Expected binary package was not produced.

We have not yet compared this package failure with the organizers'
original failure log.

## Setup for another ARM64 Linux host

Use a dedicated Linux ARM64 machine with Docker and the extracted
local-development-v1 bundle. These instructions modify the host's
x86 binfmt registration, which affects x86 execution across containers.
Do this when no other x86 builds are running.

This is a host-side workaround, not a change to the organizer's bundle.
It has not been tested here on x86 hosts or Docker Desktop.

### 1. Inspect and preserve the existing configuration

    uname -m
    docker ps
    mkdir -p "$HOME/buildbench-qemu-setup"
    if [ -f /proc/sys/fs/binfmt_misc/qemu-x86_64 ]; then
      cat /proc/sys/fs/binfmt_misc/qemu-x86_64 \
        > "$HOME/buildbench-qemu-setup/binfmt-before.txt"
    fi

Expected host architecture: aarch64.

### 2. Pull the exact tested image

    qemu_image="tonistiigi/binfmt@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0"
    docker pull --platform linux/arm64 "$qemu_image"

Use this digest to reproduce our test rather than a changing latest tag.

### 3. Register its x86 emulator

    if [ -f /proc/sys/fs/binfmt_misc/qemu-x86_64 ]; then
      docker run --privileged --rm --platform linux/arm64 \
        "$qemu_image" --uninstall qemu-x86_64
    fi

    docker run --privileged --rm --platform linux/arm64 \
      "$qemu_image" --install amd64

    cat /proc/sys/fs/binfmt_misc/qemu-x86_64

Confirm the handler is enabled and its flags include F.

The system's installed QEMU executable is not overwritten.
The registered interpreter path can refer to a file from the installer
container; do not require that path to exist in the host filesystem.

### 4. Test ordinary x86 execution before a long build

    check_name="bb-qemu-check-$(date -u +%Y%m%dT%H%M%SZ)"

    docker run -d --name "$check_name" \
      --platform linux/amd64 \
      --network none \
      --ulimit core=0 \
      ubuntu@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852 \
      /bin/sh -c '
        /usr/bin/grep --version &&
        /usr/bin/grep -q "^ID=ubuntu" /etc/os-release
      '

    timeout 20s docker wait "$check_name"
    docker logs "$check_name"
    docker inspect "$check_name" \
      --format 'Running={{.State.Running}} ExitCode={{.State.ExitCode}}'

Expected: grep version text, Running=false, ExitCode=0.

If it is still running or exits nonzero, investigate before running cases.
A timeout of docker wait does not stop the container.
To stop only this diagnostic container:

    docker kill "$check_name"

Once stopped, remove it:

    docker rm "$check_name"

### 5. Run the original case

    cd "$HOME/buildbench-local-development-v1"

    result_dir="$HOME/buildbench-experiments/intbitset-qemu10-$(date -u +%Y%m%dT%H%M%SZ)"

    bash run.sh \
      --case launchpad-noble-arm64-amd64-python-intbitset-0ede6db7f446 \
      --output "$result_dir"

    result_exit=$?
    printf 'Exit code: %s\nResults: %s\n' "$result_exit" "$result_dir"
    cat "$result_dir/build-result.json"
    tail -n 100 "$result_dir/build.log"

An original source package can legitimately fail. Check the stage and
actual log: reaching package_build is different from infrastructure_error.

Run one case at a time and preserve each result directory.

### After a reboot or emulator package update

Recheck the registration and repeat the small grep test.
Do not assume the custom registration persists across a reboot.
If necessary, repeat the pinned registration procedure.

## Remaining work

- Test the other four x86-target cases unchanged.
- Compare resulting failures with organizer-provided failure evidence.
- Rerun agent evaluations only after confirming working environments.
- Keep environment recovery separate from agent repair success.
