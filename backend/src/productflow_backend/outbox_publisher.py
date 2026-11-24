from productflow_backend.infrastructure.verification import require_verified_process


def main() -> None:
    require_verified_process()
    from productflow_backend.infrastructure.background_loop import run_polling_loop
    from productflow_backend.infrastructure.outbox import publish_one

    run_polling_loop(publish_one, 0.2)


if __name__ == "__main__":
    main()
