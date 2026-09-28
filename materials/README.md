# Downloaded Build-Bench materials

This branch preserves the downloaded project materials and inspection outputs, excluding the entire `buildbench-starter-kit-0.1.0-rc.2/` folder. macOS `.DS_Store` metadata is omitted. Existing course repository files remain unchanged.

The complete snapshot contains 84,645 files, 16,414 directories, and 2,450 symbolic links: 4,302,311,086 bytes of regular-file content. It is stored losslessly in `downloaded-materials.tar.zst` (980,535,523 bytes) using Git LFS because individual original files exceed GitHub's ordinary file limit. Nothing is omitted merely because it is large.

| Included path | Contents |
| --- | --- |
| `BuildBench-Runtime-Images-0.1.0-rc.3/` | Runtime image README and publishing workflow |
| `buildbench-validator-context-0.1.0-rc.3/` and its `.tar.gz` | Validator sources, schemas, Dockerfile, and vendored OBS packages |
| `buildbench-example-assets-context-0.1.0-rc.3/` and its `.tar.gz` | Hello example and 145 RPM dependencies |
| `buildbench-development-cases-v0.1/` | All 200 development cases, source archives, logs, and checksums |
| `unpacked-sources/` | Expanded source packages, diffs, and nested assets |
| `analysis/` | Project report, 200-case index, extracted-source records, and analysis scripts |
| `SHA256SUMS` | Original downloaded checksum listing |
| `Unconfirmed 781036.crdownload` | Retained, complete development-case ZIP bytes; this browser filename is preserved verbatim |

`manifest.json` lists every included path, file size, SHA-256, permission mode, and symbolic-link target. The archive preserves the current downloaded/inspection tree. Three filename collisions encountered during earlier macOS extraction were preserved under `__member_collisions__`; use the original source tarballs when materializing Debian packages on Linux. Upstream source packages retain their original licenses and copyright notices.

## Fetch, verify, and extract on Linux

Prerequisites: Git, Git LFS, Python 3, GNU tar, and zstd. Git LFS must be installed before downloading the archive payload.

```bash
git clone --branch codex/downloaded-materials https://github.com/ec528-fall26/build-bench.git
cd build-bench
git lfs install --local
git lfs pull
python3 materials/verify.py
mkdir -p "$HOME/buildbench-materials"
tar --zstd -xf materials/downloaded-materials.tar.zst -C "$HOME/buildbench-materials"
python3 materials/verify.py --directory "$HOME/buildbench-materials"
```

Extract into an empty directory on the Linux machine's own filesystem. Allow at least 6 GB free for the downloaded archive and expanded files, plus additional space for Docker images and builds. The full archive and per-file verification were performed before upload.

The standalone `.zip` files were deleted at the user's request before this snapshot. Their extracted contents remain included. The archive inventory in the earlier analysis is historical; consult `analysis/zip-cleanup.json` for those deletions. Paths in the earlier analysis describe the original Mac workspace and may need to be interpreted relative to your extraction directory.

The starter kit is deliberately absent. This snapshot is development material, not an agent submission. The dataset contains Debian source packages and historical failure logs; it does not include the frozen dependency snapshots and validator manifests needed to run all 200 cases directly. No repair-rate result is claimed.
