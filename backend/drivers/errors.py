class CapabilityNotSupportedError(Exception):
    def __init__(self, capability_name: str, message: str | None = None):
        self.capability_name = capability_name
        self.message = message or f"Capability '{capability_name}' is not supported"
        super().__init__(self.message)