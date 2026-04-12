#!/usr/bin/env python3
"""
Architecture Sync Check

Verifies that every feature directory under features/ has a corresponding
entry in ARCHITECTURE.md. This keeps the architecture doc up to date as
new features are added.

Exit codes:
  0 - All features documented
  1 - Undocumented feature found
"""

import re
import sys
import os

# Directories under features/ that are not actual features
IGNORED_DIRS = {"__pycache__"}


def get_feature_dirs():
    """Get all feature directory names from features/."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    features_path = os.path.join(script_dir, "..", "..", "..", "features")

    if not os.path.isdir(features_path):
        print(f"ERROR: features/ directory not found at {features_path}", file=sys.stderr)
        return set()

    return {
        d
        for d in os.listdir(features_path)
        if os.path.isdir(os.path.join(features_path, d)) and d not in IGNORED_DIRS
    }


def get_documented_features():
    """Get features listed in the 'Feature slices' section of ARCHITECTURE.md."""
    script_dir = os.path.dirname(os.path.abspath(__file__))
    arch_path = os.path.join(script_dir, "..", "..", "..", "ARCHITECTURE.md")

    try:
        with open(arch_path, "r") as f:
            content = f.read()
    except FileNotFoundError:
        print(f"ERROR: ARCHITECTURE.md not found at {arch_path}", file=sys.stderr)
        return set(), set()

    # Extract the "Feature slices" bullet list (- `name`)
    slices = set()
    slices_match = re.search(
        r"### Feature slices\s*\n\nCurrent feature-owned domains:\s*\n((?:\s*-\s*`\w+`\s*\n?)+)",
        content,
    )
    if slices_match:
        for m in re.finditer(r"-\s*`(\w+)`", slices_match.group(1)):
            slices.add(m.group(1))

    # Extract the "Feature map" entries (- `name`: description)
    feature_map = set()
    map_match = re.search(
        r"### Feature map\s*\n((?:\s*-\s*`\w+`:.+\n?)+)",
        content,
    )
    if map_match:
        for m in re.finditer(r"-\s*`(\w+)`:", map_match.group(1)):
            feature_map.add(m.group(1))

    return slices, feature_map


def check_sync():
    """Check that all feature dirs are documented in ARCHITECTURE.md."""
    feature_dirs = get_feature_dirs()
    slices, feature_map = get_documented_features()

    if not feature_dirs:
        print("ERROR: Could not list feature directories", file=sys.stderr)
        return False

    if not slices and not feature_map:
        print("ERROR: Could not parse any features from ARCHITECTURE.md", file=sys.stderr)
        return False

    print(f"Feature directories: {sorted(feature_dirs)}")
    print(f"Feature slices documented: {sorted(slices)}")
    print(f"Feature map documented: {sorted(feature_map)}")
    print()

    errors = []

    # Every feature dir must appear in both the slices list and the feature map
    for feature in sorted(feature_dirs):
        if feature not in slices:
            errors.append(
                f"Feature '{feature}' has a directory but is missing from "
                f"'Feature slices' in ARCHITECTURE.md"
            )
        if feature not in feature_map:
            errors.append(
                f"Feature '{feature}' has a directory but is missing from "
                f"'Feature map' in ARCHITECTURE.md"
            )

    # Stale docs: listed in ARCHITECTURE.md but no directory exists
    for feature in sorted(slices - feature_dirs):
        errors.append(
            f"Feature '{feature}' is listed in 'Feature slices' but has no "
            f"directory under features/"
        )
    for feature in sorted(feature_map - feature_dirs):
        errors.append(
            f"Feature '{feature}' is listed in 'Feature map' but has no "
            f"directory under features/"
        )

    if errors:
        print("ARCHITECTURE SYNC ERRORS FOUND:")
        for error in errors:
            print(f"  - {error}")
        print()
        print("To fix:")
        print("  - Add the new feature to both 'Feature slices' and 'Feature map'")
        print("    sections in ARCHITECTURE.md")
        print("  - Or remove stale entries for features that no longer exist")
        return False

    print("All features are documented in ARCHITECTURE.md")
    return True


def main():
    print("=" * 60)
    print("Architecture Sync Check")
    print("=" * 60)
    print()

    success = check_sync()

    print()
    if success:
        print("PASS: Architecture docs are in sync with features/")
        sys.exit(0)
    else:
        print("FAIL: Architecture docs are out of sync with features/")
        sys.exit(1)


if __name__ == "__main__":
    main()
