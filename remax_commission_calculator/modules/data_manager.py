from modules.database import (
    get_agents,
    get_operations,
    get_properties
)


def load_agents():
    return get_agents()


def load_properties():
    return get_properties()


def load_history():
    return get_operations()