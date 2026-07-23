"""Universal Remote Desktop installer core."""

from .model import (
    CommandResult,
    ConfigError,
    DirectoryEnsureResult,
    ExecutionError,
    FactsError,
    FileInstallResult,
    InstallerConfig,
    InstallerError,
    IntegrationError,
    OperationResult,
    Plan,
    PlanStep,
    SupportLevel,
    SystemFacts,
)

__version__ = "2.0.0"

__all__ = [
    "CommandResult",
    "ConfigError",
    "DirectoryEnsureResult",
    "ExecutionError",
    "FactsError",
    "FileInstallResult",
    "InstallerConfig",
    "InstallerError",
    "IntegrationError",
    "OperationResult",
    "Plan",
    "PlanStep",
    "SupportLevel",
    "SystemFacts",
    "__version__",
]
