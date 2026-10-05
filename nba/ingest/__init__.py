"""Resumable, cached, rate-limited nba_api ingestion.

Submodules import nba_api lazily (inside functions) so importing this
package -- and running the test suite -- never touches the network or
requires the dependency to be importable at collection time.
"""
