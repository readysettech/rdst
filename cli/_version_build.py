"""RDST version metadata.

Release builds overwrite this file with the real version
(.buildkite/build_package.sh); this checked-in placeholder makes the
package buildable straight from a source checkout, including
`pip install` from the public GitHub repository.
"""

__version__ = "0.0.0.dev0"
