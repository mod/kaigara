.PHONY: test up down logs chat

test:
	uv run pytest tests/ -v

up:
	podman-compose up -d --build

down:
	podman-compose down

logs:
	podman-compose logs -f

chat:
	uv run python cli.py

chat-owner:
	uv run python cli.py --role owner

chat-guest:
	uv run python cli.py --role guest
