from confluid import configurable
from logflow import get_logger

from liquify import LiquifyApp

logger = get_logger("simple_app")


@configurable
class MyComponent:
    def __init__(self, value: int = 0, message: str = "default") -> None:
        self.value = value
        self.message = message


app = LiquifyApp(name="simple")


@app.script_command()
def test(component: MyComponent) -> None:
    """A simple test command."""
    logger.info(f"Component Value: {component.value}")
    logger.info(f"Component Message: {component.message}")
    logger.debug("Debug log verified.")


if __name__ == "__main__":
    app.run()
