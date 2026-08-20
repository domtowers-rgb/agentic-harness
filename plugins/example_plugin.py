def hello(name: str = "world") -> str:
    return f"Hello, {name}!"


def register(registry):
    spec = {
        "name": "hello",
        "description": "Say hello to someone",
        "parameters": {
            "type": "object",
            "properties": {"name": {"type": "string"}},
            "required": [],
        },
    }
    registry.register("hello", hello, spec)
