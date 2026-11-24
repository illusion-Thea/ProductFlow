from productflow_backend.infrastructure.verification import require_verified_process


def main() -> None:
    require_verified_process()
    from productflow_backend.application.execution.recovery import scan_deadlines
    from productflow_backend.infrastructure.background_loop import run_polling_loop

    run_polling_loop(scan_deadlines, 0.5)


if __name__ == "__main__":
    main()
