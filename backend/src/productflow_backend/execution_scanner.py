import time

from productflow_backend.application.execution.recovery import scan_deadlines


def main() -> None:
    while True:
        scan_deadlines()
        time.sleep(0.5)


if __name__ == "__main__":
    main()
