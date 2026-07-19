"""Providers — multi-chain abstraction layer (Phase 2, 2026.07.21).

Import this package to auto-register all chain providers.
Use provider_for(address) to route an address to the right provider.
"""

from services.providers.base import (
    ChainProvider,
    PROVIDERS,
    register_provider,
    provider_for,
)
from services.providers.evm import EvmProvider, evm_provider

__all__ = [
    "ChainProvider",
    "PROVIDERS",
    "register_provider",
    "provider_for",
    "EvmProvider",
    "evm_provider",
]
