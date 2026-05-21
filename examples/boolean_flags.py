from confluid import configurable

from liquifai import LiquifyApp


@configurable
class NetworkConfig:
    def __init__(self, use_ssl: bool = True, retry_on_failure: bool = False):
        """
        Configuration for network connectivity.

        Args:
            use_ssl: Enable SSL encryption for all connections.
            retry_on_failure: Automatically retry failed requests.
        """
        self.use_ssl = use_ssl
        self.retry_on_failure = retry_on_failure


app = LiquifyApp(name="bool-demo")


@app.command(default=True)
def main(config: NetworkConfig, dry_run: bool = False) -> None:
    """
    Demonstrate passing boolean values via CLI overrides using Suffix Polarity.

    Args:
        config: Injected network settings.
        dry_run: If enabled, no real actions will be performed.
    """
    print(f"Network Config -> use_ssl: {config.use_ssl} (type: {type(config.use_ssl).__name__})")
    print(
        f"Network Config -> retry_on_failure: {config.retry_on_failure} "
        f"(type: {type(config.retry_on_failure).__name__})"
    )
    print(f"Command Argument -> dry_run: {dry_run} (type: {type(dry_run).__name__})")


if __name__ == "__main__":
    app.run()
