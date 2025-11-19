# RDST Refactor - Migration Summary

## Overview

RDST has been successfully decoupled from the cloud agent build system and moved to a standalone directory at the repository root with its own build pipeline and dependency management.

## Changes Made

### 1. Directory Structure

**Before:**
```
cloud/
├── rdst/              # RDST build scripts only
└── cloud_agent/
    ├── rdst.py        # Main entry point
    ├── lib/           # Shared with cloud_agent
    └── requirements.txt  # Shared dependencies
```

**After:**
```
rdst/                  # Standalone at repository root
├── rdst.py            # Main entry point (copied)
├── requirements.txt   # Dedicated dependencies (8 packages, down from 21)
├── pyproject.toml     # Python project configuration
├── README.md          # Documentation
├── .gitignore         # Build artifacts
├── lib/               # RDST-specific modules (copied)
│   ├── cli/
│   ├── functions/
│   ├── llm_manager/
│   ├── query_registry/
│   ├── workflows/
│   └── prompts/
├── common/            # Shared utilities (inlined from control_plane)
│   ├── logger.py
│   ├── sqs_manager.py
│   ├── s3_operations.py
│   ├── secrets_manager.py
│   └── constants.py
└── Build scripts and Dockerfiles
```

### 2. Dependencies

**Reduced from 21 to 8 packages:**
- ✅ Kept: boto3, requests, psycopg2-binary, pymysql, pandas, rich, toml, pygments
- ❌ Removed: awscli, gevent, psycogreen, psutil, ddtrace, opentelemetry-*, prometheus-client, cryptography, fastapi, pydantic, uvicorn

### 3. Build Scripts

**Updated files:**
- `rdst/orchestrate_rdst.sh` - Now references rdst/ at repository root
- `rdst/build_rdst.sh` - Simplified to copy from /workspace (rdst/), removed cloud_agent dependencies
- `rdst/Dockerfile.*` - No changes needed (work with new structure)

**Key changes:**
- Docker containers mount `rdst/` to `/workspace` (not `cloud/`)
- Build artifacts go to `/build` (external mount)
- No more copying from cloud_agent or control_plane directories

### 4. BuildKite Pipeline

**New pipeline:** `.buildkite/pipeline.rdst.yml`

**Strategy:**
- **Dev Builds** (auto on merge to main):
  - Build DEB, RPM, AL2023 packages
  - Deploy to dev01 S3 location
  - Run integration tests (PostgreSQL + MySQL)

- **Release Builds** (manual trigger):
  - Manual approval step
  - Build all platforms: DEB, RPM, AL2023, macOS
  - Deploy to stage01 S3 location
  - Copy to production release folder

**Cloud pipeline changes:**
- Removed all RDST build steps from `cloud/control_plane/.buildkite/duplo-cicd-pipeline.yml`
- Added comments: "NOTE: RDST builds moved to .buildkite/pipeline.rdst.yml"
- Removed sections for dev02, stage01, and prod01 RDST builds

### 5. Import Updates

Fixed imports in common/ files to reference the new structure:
- `logger.py`: Now imports from `common.constants` and `common.s3_operations`
- `sqs_manager.py`: Now imports from `common.logger`

## Next Steps

### Testing

1. **Local Build Test:**
   ```bash
   cd rdst
   ./orchestrate_rdst.sh deb
   ```

2. **Verify Docker Build:**
   ```bash
   # Should mount rdst/ and build successfully
   docker build -t test-rdst -f rdst/Dockerfile.deb.ubuntu rdst/
   ```

3. **Integration Tests:**
   - Dev builds will run automatically on merge to main
   - Integration tests will validate PostgreSQL and MySQL connections

### BuildKite Setup

**Required Actions (coordinate with Ron if needed):**

1. **Add new pipeline to BuildKite:**
   - Pipeline file: `.buildkite/pipeline.rdst.yml`
   - Watch paths: `rdst/**`
   - Trigger: On push to main branch

2. **Verify S3 permissions:**
   - Dev location: `s3://readysetobservabilityagent-test/dev01/latest/`
   - Release location: `s3://readysetobservabilityagent/stage01/latest/`
   - Ensure BuildKite agents have write access

### Migration Checklist

- [x] Create rdst/ directory structure at repository root
- [x] Copy RDST source files (rdst.py, lib/)
- [x] Copy and inline control_plane helpers to common/
- [x] Create dedicated requirements.txt (8 packages)
- [x] Create pyproject.toml
- [x] Update build scripts (orchestrate_rdst.sh, build_rdst.sh)
- [x] Create new BuildKite pipeline (.buildkite/pipeline.rdst.yml)
- [x] Remove RDST from cloud pipeline
- [x] Add .gitignore for build artifacts
- [ ] Test local build (DEB)
- [ ] Test local build (RPM)
- [ ] Test local build (AL2023)
- [ ] Verify dev build on BuildKite
- [ ] Run integration tests
- [ ] Test release build (manual trigger)
- [ ] Verify macOS build
- [x] Update any documentation referencing old path

### Rollback Plan

If issues arise, the old RDST build setup is preserved in `cloud/rdst/` and can be re-enabled by:

1. Reverting changes to `cloud/control_plane/.buildkite/duplo-cicd-pipeline.yml`
2. Temporarily disabling `.buildkite/pipeline.rdst.yml`
3. Using `cloud/rdst/orchestrate_rdst.sh` with original paths

## Benefits

1. **Clean Separation:** RDST no longer shares dependencies with cloud_agent
2. **Simpler Builds:** Single dev build + single release build (no per-environment rebuilds)
3. **Faster Iteration:** Changes to RDST don't trigger cloud agent builds
4. **Better Dependency Management:** Only 8 required packages vs 21 shared
5. **Independent Releases:** RDST can be released independently from cloud platform

## Notes

- S3 bucket structure remains unchanged (using existing buckets)
- Integration tests have been moved to `rdst/tests/integration/`
- Unit tests are in `rdst/tests/unit/`
- Mac build cleanup scripts still reference cloud infrastructure
- The refactor maintains backward compatibility with existing S3 paths and artifact naming

## Questions / Issues

If you encounter any issues:
1. Check Docker build logs for path errors
2. Verify S3 upload permissions
3. Ensure BuildKite agents can access rdst/ directory
4. Validate that common/ imports work correctly in Nuitka build

---

**Completed:** 2025-11-18
**Next Review:** After first successful BuildKite build
