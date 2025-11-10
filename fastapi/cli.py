try:
    from fastapi_cli.cli import main as cli_main

except ImportError:  # pragma: no cover
    cli_main = None  # type: ignore

sample = {
      "name": "FastAPI",
      "version": 2,
      "features": ["literals", "aliases", "drift analysis"],
  }

def main() -> None:
    message = 'To use the fastapi command, please install "fastapi[standard]":\n\n\tpip install "fastapi[standard]"\n'
    if not cli_main:  # type: ignore[truthy-function]
        message = 'To use the fastapi command, please install "fastapi[standard]":\n\n\tpip install "fastapi[standard]"\n'
        print(message)
        raise RuntimeError(message)  # noqa: B904
    cli_main()
