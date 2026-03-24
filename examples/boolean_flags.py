from confluid import configurable

from liquify import LiquifyApp


@configurable
class NetworkConfig:
    def __init__(self, use_ssl: bool = True, retry_on_failure: bool = False):
        self.use_ssl = use_ssl
        self.retry_on_failure = retry_on_failure


app = LiquifyApp(name="bool-demo")


@app.command(default=True)
def main(config: NetworkConfig, dry_run: bool = False) -> None:
    """
    Demonstrate passing boolean values via CLI overrides using Suffix Polarity.

    Usage Examples:
      1. Default values:
         python boolean_flags.py

      2. Single-argument Positive polarity (+):
         python boolean_flags.py --config.retry_on_failure+

      3. Single-argument Negative polarity (-):
         python boolean_flags.py --config.use_ssl-

      4. Implicit Positive (standard flag):
         python boolean_flags.py --dry_run

      5. Mixed:
         python boolean_flags.py --dry_run+ --config.use_ssl-
    """
    print(f"Network Config -> use_ssl: {config.use_ssl} (type: {type(config.use_ssl).__name__})")
    print(
        f"Network Config -> retry_on_failure: {config.retry_on_failure} "
        f"(type: {type(config.retry_on_failure).__name__})"
    )
    print(f"Command Argument -> dry_run: {dry_run} (type: {type(dry_run).__name__})")


if __name__ == "__main__":
    app.run()
