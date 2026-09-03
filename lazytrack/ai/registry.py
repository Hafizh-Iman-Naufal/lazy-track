from lazytrack.ai.base import AIProvider


class ProviderRegistry:
    _instance = None
    _providers: dict[str, type[AIProvider]] = {}

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super().__new__(cls)
            cls._instance._providers = {}
        return cls._instance

    def register(self, name: str, provider_class: type[AIProvider]) -> None:
        self._providers[name.lower()] = provider_class

    def get(self, name: str) -> type[AIProvider] | None:
        return self._providers.get(name.lower())

    def list_providers(self) -> list[str]:
        return list(self._providers.keys())


def get_registry() -> ProviderRegistry:
    return ProviderRegistry()


def register_provider(name: str, provider_class: type[AIProvider]) -> None:
    get_registry().register(name, provider_class)


def get_provider(name: str) -> type[AIProvider] | None:
    return get_registry().get(name)


def list_providers() -> list[str]:
    return get_registry().list_providers()
