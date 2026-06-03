"""Repository-local Antigravity agent modules.

Keep this package extendable so tests can import repo-local Antigravity modules
while still resolving Hermes-owned modules from the installed agent package.
"""

from pkgutil import extend_path

__path__ = extend_path(__path__, __name__)
