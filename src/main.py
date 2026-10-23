"""Primary application entry point for Quote Bot AI."""
import os

if __package__:
    from .server import PrivateRequestHandler, create_app
else:
    from server import PrivateRequestHandler, create_app


def main():
    create_app().run(
        host="0.0.0.0",
        port=int(os.getenv("PORT", "5000")),
        request_handler=PrivateRequestHandler,
    )


if __name__ == "__main__":
    main()
