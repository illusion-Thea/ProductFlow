import time

from productflow_backend.infrastructure.outbox import publish_one


def main() -> None:
    while True:
        if not publish_one():
            time.sleep(0.2)


if __name__ == "__main__":
    main()
