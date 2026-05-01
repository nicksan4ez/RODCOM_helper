from __future__ import annotations

import logging

from .bot import RodcomBot
from .config import Config


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    bot = RodcomBot(Config.from_env())
    bot.run()


if __name__ == "__main__":
    main()

