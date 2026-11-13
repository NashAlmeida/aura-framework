"""Sample Mark-LVII-style action for local bridge testing."""


def open_url(parameters: dict) -> str:
    return f"Would open: {parameters.get('url', '')}"


TOOL = {
    "name": "open_url",
    "description": "Open a URL in the default browser",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "url": {"type": "STRING", "description": "URL to open"},
        },
        "required": ["url"],
    },
    "handler": open_url,
}
