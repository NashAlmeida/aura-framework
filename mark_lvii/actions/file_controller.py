"""Sample Mark-LVII-style risky action for local confirmation testing."""


def delete_file(parameters: dict) -> str:
    path = parameters.get("path", "")
    return f"Would delete: {path}"


TOOL = {
    "name": "delete_file",
    "description": "Delete a file from disk",
    "parameters": {
        "type": "OBJECT",
        "properties": {
            "path": {"type": "STRING", "description": "Path of the file to delete"},
        },
        "required": ["path"],
    },
    "handler": delete_file,
}
