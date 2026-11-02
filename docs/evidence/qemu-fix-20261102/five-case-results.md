# Five x86-target cases after the QEMU update

## Experiment

Host: ARM64 EC2.
Changed the host's registered x86 emulator from QEMU 8.2.2 to 10.2.3.

Pinned emulator image:
tonistiigi/binfmt@sha256:400a4873b838d1b89194d982c45e5fb3cda4593fbfd7e08a02e76b03b21166f0

The organizer-supplied ARM64 validator image, original case sources,
build configurations, and frozen dependencies were unchanged.
No repair agent or model was invoked.

## Results

All five cases reached package build stages.

| Case | Duration | Observed failure |
|---|---:|---|
| python-intbitset | 643 seconds | Documentation build: invalid command build_sphinx |
| libbpp-qt | 756 seconds | Shared-library packaging: symbol differences and dh_makeshlibs failure |
| pybigwig | 516 seconds | Linking: libBigWig.a relocation error, recompile with -fPIC |
| libbpp-core | 2130 seconds | Shared-library packaging: symbol differences and dh_makeshlibs failure |
| libevtx | 2446 seconds | test_tools.sh: message_handle segmentation fault, exit 139 |

## Interpretation

The update allowed all five previously setup-blocked cases to reach
package build stages. This is environment recovery, not an agent repair
or successful package build.

The earlier QEMU 8.2.2 crash was also reproduced outside Build-Bench
using an amd64 Ubuntu grep command. The equivalent diagnostic passed
with the replacement emulator.

The libevtx test crash remains unresolved. Its runner redirects the
test executable's stderr to /dev/null, so the captured log does not
establish whether the cause is a package defect or emulation.

The exact upstream cause of the old QEMU crash has not been established.

## Outstanding validation

- Compare each observed failure with the organizer-provided initial log.
- Record whether the failure stage and diagnostic match.
- Investigate discrepancies before treating them as intended repair tasks.
- Investigate libevtx's crash further if the original evidence differs.
- Rerun agent evaluations separately after validating the environments.

## Evidence locations

- before-qemu8/: python-intbitset before the emulator update.
- after-qemu10/: python-intbitset after the emulator update.
- x86-original-qemu10-20261102T022142Z/: libbpp-qt and pybigwig.
- x86-remaining-qemu10-20261102T030236Z/: libbpp-core and libevtx.

The first multi-case loop ended before recording pybigwig in
exit-codes.tsv. Its build-result.json and build.log contain its result.
Do not treat that TSV as the complete results list.
